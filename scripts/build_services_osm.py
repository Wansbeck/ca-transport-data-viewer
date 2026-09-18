#!/usr/bin/env python3
"""Build a compact statewide service/competition GeoJSON from OpenStreetMap.

Outputs:
  data/services-osm.geojson
  data/services-osm-meta.json

The final dataset contains individual fuel, EV, rest/service-area and truck-oriented
locations, plus aggregated food clusters. Raw restaurant POIs are not committed.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
BBOX = (32.52, -124.48, 42.01, -114.13)  # south, west, north, east
OUT = Path("data/services-osm.geojson")
META = Path("data/services-osm-meta.json")

SERVICE_QUERY = """
[out:json][timeout:180];
(
  nwr["amenity"="fuel"]({s},{w},{n},{e});
  nwr["amenity"="charging_station"]({s},{w},{n},{e});
  nwr["highway"="rest_area"]({s},{w},{n},{e});
  nwr["highway"="services"]({s},{w},{n},{e});
);
out center tags;
"""

FOOD_QUERY = """
[out:json][timeout:180];
(
  nwr["amenity"="restaurant"]["name"]({s},{w},{n},{e});
  nwr["amenity"="fast_food"]["name"]({s},{w},{n},{e});
  nwr["amenity"="cafe"]["name"]({s},{w},{n},{e});
  nwr["amenity"="food_court"]["name"]({s},{w},{n},{e});
);
out center tags;
"""

TRUCK_BRANDS = ("pilot", "flying j", "love", "loves", "travelcenters of america", "ta ", "petro")


def query_overpass(query: str) -> dict:
    last_error = None
    for attempt in range(5):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        try:
            response = requests.post(
                url,
                data={"data": query},
                headers={"User-Agent": "ca-transport-data-viewer/0.7 (GitHub Pages analytical project)"},
                timeout=240,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Overpass request failed after retries: {last_error}")


def coords(element: dict):
    if "lat" in element and "lon" in element:
        return float(element["lon"]), float(element["lat"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lon"]), float(center["lat"])
    return None


def clean_text(value):
    value = str(value or "").strip()
    return value or None


def is_truck_oriented(tags: dict) -> bool:
    name_blob = " ".join(
        str(tags.get(k, "")).lower()
        for k in ("name", "brand", "operator")
    )
    if tags.get("hgv") in ("yes", "designated"):
        return True
    if tags.get("parking:hgv") in ("yes", "designated"):
        return True
    if tags.get("fuel:hgv_diesel") == "yes":
        return True
    return any(token in name_blob for token in TRUCK_BRANDS)


def service_category(tags: dict) -> str:
    if tags.get("highway") == "rest_area":
        return "rest_area"
    if tags.get("highway") == "services":
        return "truck_service" if is_truck_oriented(tags) else "service_area"
    if tags.get("amenity") == "charging_station":
        return "ev_charging"
    if tags.get("amenity") == "fuel":
        return "truck_service" if is_truck_oriented(tags) else "fuel"
    return "other"


def competition_strength(category: str, tags: dict) -> tuple[float, str]:
    """Approximate how strongly a location competes with a destination service concept."""
    if category == "service_area":
        return 3.0, "destination-scale"
    if category == "truck_service":
        strength = 2.2
        if tags.get("shower") == "yes":
            strength += 0.2
        if tags.get("toilets") == "yes":
            strength += 0.2
        return min(strength, 2.8), "full-service"
    if category == "rest_area":
        return 1.2, "basic-stop"
    if category == "fuel":
        strength = 0.8
        if tags.get("shop") in ("yes", "convenience"):
            strength += 0.25
        if tags.get("toilets") == "yes":
            strength += 0.2
        if tags.get("opening_hours") == "24/7":
            strength += 0.15
        return min(strength, 1.5), "fuel-convenience"
    if category == "ev_charging":
        capacity = clean_text(tags.get("capacity"))
        try:
            cap = int(float(capacity)) if capacity else 0
        except ValueError:
            cap = 0
        strength = 0.45 + min(cap, 20) * 0.025
        return min(strength, 0.95), "charging"
    return 0.5, "other"


def element_feature(element: dict) -> dict | None:
    xy = coords(element)
    if not xy:
        return None
    tags = element.get("tags") or {}
    category = service_category(tags)
    if category == "other":
        return None

    strength, quality_tier = competition_strength(category, tags)
    props = {
        "CATEGORY": category,
        "COMPETITION_STRENGTH": round(strength, 2),
        "QUALITY_TIER": quality_tier,
        "NAME": clean_text(tags.get("name") or tags.get("brand") or tags.get("operator")),
        "BRAND": clean_text(tags.get("brand")),
        "OPERATOR": clean_text(tags.get("operator")),
        "OPENING_HOURS": clean_text(tags.get("opening_hours")),
        "HGV": clean_text(tags.get("hgv")),
        "TOILETS": clean_text(tags.get("toilets")),
        "SHOWER": clean_text(tags.get("shower")),
        "CAPACITY": clean_text(tags.get("capacity")),
        "OSM_TYPE": element.get("type"),
        "OSM_ID": element.get("id"),
    }
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [xy[0], xy[1]]},
        "properties": props,
    }


def build_food_clusters(elements: list[dict]) -> list[dict]:
    # ~5 km cells in latitude. This is deliberately a market-context cluster,
    # not a claim that every venue is reachable from a particular interchange.
    cell = 0.05
    buckets = defaultdict(list)

    for element in elements:
        xy = coords(element)
        if not xy:
            continue
        lon, lat = xy
        key = (math.floor(lat / cell), math.floor(lon / cell))
        buckets[key].append((lon, lat, element.get("tags") or {}))

    features = []
    for items in buckets.values():
        if len(items) < 3:
            continue
        lon = sum(x[0] for x in items) / len(items)
        lat = sum(x[1] for x in items) / len(items)
        sample_names = []
        types = defaultdict(int)
        for _, _, tags in items:
            amenity = tags.get("amenity", "food")
            types[amenity] += 1
            name = clean_text(tags.get("name"))
            if name and name not in sample_names and len(sample_names) < 5:
                sample_names.append(name)

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "CATEGORY": "food_cluster",
                "NAME": f"{len(items)} mapped food venues",
                "COUNT": len(items),
                "RESTAURANTS": types.get("restaurant", 0),
                "FAST_FOOD": types.get("fast_food", 0),
                "CAFES": types.get("cafe", 0),
                "FOOD_COURTS": types.get("food_court", 0),
                "SAMPLE_NAMES": ", ".join(sample_names) if sample_names else None,
                "COMPETITION_STRENGTH": round(min(0.5 + len(items) * 0.08, 2.4), 2),
                "QUALITY_TIER": "food-cluster",
            },
        })

    return features


def dedupe(features: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for feature in features:
        p = feature["properties"]
        xy = feature["geometry"]["coordinates"]
        if p["CATEGORY"] == "food_cluster":
            key = ("food_cluster", round(xy[0], 5), round(xy[1], 5))
        else:
            key = (p.get("OSM_TYPE"), p.get("OSM_ID"), p["CATEGORY"])
        if key in seen:
            continue
        seen.add(key)
        result.append(feature)
    return result


def main():
    s, w, n, e = BBOX
    service_raw = query_overpass(SERVICE_QUERY.format(s=s, w=w, n=n, e=e))
    # Give the public Overpass service a short breather before the larger food query.
    time.sleep(8)
    food_raw = query_overpass(FOOD_QUERY.format(s=s, w=w, n=n, e=e))

    individual = [
        f for f in (element_feature(el) for el in service_raw.get("elements", []))
        if f is not None
    ]
    food_clusters = build_food_clusters(food_raw.get("elements", []))
    features = dedupe(individual + food_clusters)

    category_counts = defaultdict(int)
    for feature in features:
        category_counts[feature["properties"]["CATEGORY"]] += 1

    collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    OUT.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "OpenStreetMap contributors via Overpass API",
        "bbox": list(BBOX),
        "feature_count": len(features),
        "category_counts": dict(sorted(category_counts.items())),
        "food_cluster_method": "0.05-degree grid; clusters require at least 3 named restaurant/fast_food/cafe/food_court POIs",
        "notes": [
            "OSM completeness and tagging vary by location.",
            "Food clusters indicate nearby mapped venue concentration, not confirmed highway access.",
            "Competition strength distinguishes basic fuel/charging/rest stops from stronger destination-scale service alternatives.",
            "Truck-oriented classification uses OSM HGV tags plus common truck-stop brand heuristics.",
        ],
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Wrote {len(features):,} service/competition features")
    print(dict(sorted(category_counts.items())))


if __name__ == "__main__":
    main()
