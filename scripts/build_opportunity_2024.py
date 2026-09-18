#!/usr/bin/env python3
"""Build a first-pass corridor opportunity layer.

This is an analytical screening model, not a site recommendation. It combines:
- total AADT (primary demand signal)
- truck share (secondary demand signal)
- service scarcity / nearby competition
- ACS household income and population density context

Distances to services are straight-line distances from each segment midpoint, so
they are an initial screening proxy rather than routed highway travel distance.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import Point

SEGMENTS = Path("data/aadt-2024-segments.geojson")
TRUCKS = Path("data/truck-2024.geojson")
ACS = Path("data/acs-2024-tract-context.geojson")
SERVICES = Path("data/services-osm.geojson")
TRAVELLER = Path("data/traveller-context-osm.geojson")
OUT = Path("data/opportunity-2024.geojson")
META = Path("data/opportunity-2024-meta.json")

# Overall weights sum to 100.
WEIGHTS = {
    "total_aadt": 30.0,
    "truck_share": 5.0,
    "service_distance": 15.0,
    "nearby_competition": 15.0,
    "household_income": 10.0,
    "population_density": 5.0,
    "traveller_demand": 10.0,
    "accessibility": 10.0,
}

SERVICE_WEIGHT = {
    "fuel": 0.8,
    "truck_service": 2.4,
    "service_area": 3.0,
    "rest_area": 1.2,
    "ev_charging": 0.6,
    "food_cluster": 1.5,
}

LONG_DISTANCE_ROUTES = {
    "5": 1.0, "8": 0.9, "10": 1.0, "15": 1.0, "40": 1.0, "80": 1.0,
    "101": 0.9, "99": 0.8, "50": 0.7, "58": 0.8, "395": 0.8, "1": 0.7,
}

MEANINGFUL_CATEGORIES = set(SERVICE_WEIGHT)


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def linear_score(value, low, high, weight):
    if value is None or not math.isfinite(float(value)):
        return 0.0
    return weight * clamp((float(value) - low) / (high - low))


def midpoint_of_line(geom):
    if geom is None or geom.is_empty:
        return None
    try:
        return geom.interpolate(0.5, normalized=True)
    except Exception:
        return geom.centroid


def haversine_miles(lon1, lat1, lon2, lat2):
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * r * math.asin(math.sqrt(a))


def lonlat_to_unit(lon, lat):
    lon_r = np.radians(lon)
    lat_r = np.radians(lat)
    return np.column_stack((
        np.cos(lat_r) * np.cos(lon_r),
        np.cos(lat_r) * np.sin(lon_r),
        np.sin(lat_r),
    ))


def nearest_service_stats(midpoints, services):
    svc = services[services["CATEGORY"].isin(MEANINGFUL_CATEGORIES)].copy()
    lon = svc.geometry.x.to_numpy()
    lat = svc.geometry.y.to_numpy()
    xyz = lonlat_to_unit(lon, lat)
    tree = cKDTree(xyz)

    mlon = np.array([p.x for p in midpoints])
    mlat = np.array([p.y for p in midpoints])
    mxyz = lonlat_to_unit(mlon, mlat)

    # Query several nearby records so we can calculate both nearest distance and
    # a weighted competition intensity around the segment midpoint.
    k = min(40, len(svc))
    chord, idx = tree.query(mxyz, k=k)
    if k == 1:
        chord = chord[:, None]
        idx = idx[:, None]

    results = []
    for i in range(len(midpoints)):
        distances = []
        weighted_10mi = 0.0
        weighted_5mi = 0.0
        category_counts = defaultdict(int)

        for j in range(k):
            row = svc.iloc[int(idx[i, j])]
            d = haversine_miles(mlon[i], mlat[i], row.geometry.x, row.geometry.y)
            distances.append(d)
            if d <= 10:
                cat = row["CATEGORY"]
                strength = row.get("COMPETITION_STRENGTH")
                try:
                    strength = float(strength)
                except (TypeError, ValueError):
                    strength = SERVICE_WEIGHT.get(cat, 1.0)
                weighted_10mi += strength
                category_counts[cat] += 1
                if d <= 5:
                    weighted_5mi += strength

        nearest = min(distances) if distances else None
        results.append((nearest, weighted_5mi, weighted_10mi, dict(category_counts)))
    return results



def traveller_access_stats(midpoints, traveller):
    junctions = traveller[traveller["CATEGORY"] == "interchange"].copy()
    clusters = traveller[traveller["CATEGORY"] == "traveller_cluster"].copy()

    def make_tree(gdf):
        if gdf.empty:
            return None
        lon = gdf.geometry.x.to_numpy()
        lat = gdf.geometry.y.to_numpy()
        return cKDTree(lonlat_to_unit(lon, lat)), lon, lat

    junction_tree = make_tree(junctions)
    cluster_tree = make_tree(clusters)
    results = []

    for p in midpoints:
        nearest_junction = None
        traveller_intensity = 0.0

        if junction_tree:
            tree, lon, lat = junction_tree
            _, idx = tree.query(lonlat_to_unit(np.array([p.x]), np.array([p.y]))[0], k=1)
            nearest_junction = haversine_miles(p.x, p.y, lon[int(idx)], lat[int(idx)])

        if cluster_tree:
            tree, lon, lat = cluster_tree
            k = min(12, len(clusters))
            _, idxs = tree.query(lonlat_to_unit(np.array([p.x]), np.array([p.y]))[0], k=k)
            idxs = np.atleast_1d(idxs)
            for idx in idxs:
                row = clusters.iloc[int(idx)]
                d = haversine_miles(p.x, p.y, row.geometry.x, row.geometry.y)
                if d <= 20:
                    intensity = float(row.get("TRAVELLER_INTENSITY") or 0)
                    traveller_intensity += intensity * max(0.0, 1.0 - d / 20.0)

        results.append((nearest_junction, traveller_intensity))
    return results

def attach_acs(mid_gdf, acs):
    cols = ["MEDIAN_HH_INCOME", "POP_DENSITY_SQMI", "POPULATION", "geometry"]
    acs_sub = acs[[c for c in cols if c in acs.columns]].copy()
    joined = gpd.sjoin(mid_gdf, acs_sub, how="left", predicate="within")

    # In rare cases a point lies exactly on a tract edge; collapse duplicates
    # and retain the first match for screening.
    joined = joined[~joined.index.duplicated(keep="first")]
    return joined.reindex(mid_gdf.index)


def nearest_truck_share(midpoints, segments, trucks):
    # Prefer same-route truck observations and use the closest point within
    # approximately 15 miles. Truck data is only 10% of the overall score.
    by_route = {}
    for route, group in trucks.groupby(trucks["RTE"].astype(str)):
        if len(group):
            by_route[route] = group

    values = []
    for midpoint, (_, seg) in zip(midpoints, segments.iterrows()):
        route = str(seg.get("RTE", ""))
        group = by_route.get(route)
        if group is None or group.empty:
            values.append(None)
            continue

        best_d = None
        best_pct = None
        for _, row in group.iterrows():
            if row.geometry is None or row.geometry.is_empty:
                continue
            d = haversine_miles(midpoint.x, midpoint.y, row.geometry.x, row.geometry.y)
            if best_d is None or d < best_d:
                best_d = d
                best_pct = row.get("TRUCK_PERCENT")

        values.append(float(best_pct) if best_d is not None and best_d <= 15 and best_pct is not None else None)
    return values


def main():
    segments = gpd.read_file(SEGMENTS).to_crs(4326)
    trucks = gpd.read_file(TRUCKS).to_crs(4326)
    acs = gpd.read_file(ACS).to_crs(4326)
    services = gpd.read_file(SERVICES).to_crs(4326)
    traveller = gpd.read_file(TRAVELLER).to_crs(4326)

    midpoints = [midpoint_of_line(g) for g in segments.geometry]
    mid_gdf = gpd.GeoDataFrame({"geometry": midpoints}, crs="EPSG:4326")

    acs_join = attach_acs(mid_gdf, acs)
    truck_pct = nearest_truck_share(midpoints, segments, trucks)
    service_stats = nearest_service_stats(midpoints, services)
    traveller_stats = traveller_access_stats(midpoints, traveller)

    features = []
    score_values = []

    for i, (_, seg) in enumerate(segments.iterrows()):
        aadt = float(seg.get("AADT") or 0)
        t_pct = truck_pct[i]
        income = acs_join.iloc[i].get("MEDIAN_HH_INCOME")
        density = acs_join.iloc[i].get("POP_DENSITY_SQMI")
        population = acs_join.iloc[i].get("POPULATION")
        nearest_service, competition_5, competition_10, category_counts = service_stats[i]
        nearest_junction, traveller_intensity = traveller_stats[i]
        route_key = str(int(float(seg.get("RTE")))) if seg.get("RTE") not in (None, "") else ""
        long_distance_factor = LONG_DISTANCE_ROUTES.get(route_key, 0.35)

        # Total passing traffic remains the primary demand signal.
        aadt_score = linear_score(aadt, 10000, 120000, WEIGHTS["total_aadt"])

        # Trucks are useful incremental demand, deliberately limited to 5% of the model.
        truck_score = linear_score(t_pct, 3, 20, WEIGHTS["truck_share"])

        # Scarcity: little credit within 2 miles; full distance score at 25+ miles.
        service_distance_score = linear_score(
            nearest_service if nearest_service is not None else 30,
            2, 25, WEIGHTS["service_distance"]
        )

        # Competition: weighted services within 10 miles reduce the score.
        # 0 weighted competition = full 15 points; 12+ weighted units = 0.
        nearby_comp_score = WEIGHTS["nearby_competition"] * (1 - clamp(competition_10 / 12.0))

        # Consumer context is useful, but the concept is primarily supported by travellers.
        income_score = linear_score(income, 50000, 160000, WEIGHTS["household_income"])
        density_score = linear_score(density, 25, 4000, WEIGHTS["population_density"])

        route_component = WEIGHTS["traveller_demand"] * 0.55 * long_distance_factor
        tourism_component = linear_score(traveller_intensity, 0, 180, WEIGHTS["traveller_demand"] * 0.45)
        traveller_score = route_component + tourism_component

        if nearest_junction is None:
            access_score = WEIGHTS["accessibility"] * 0.5
        elif nearest_junction <= 1.5:
            access_score = WEIGHTS["accessibility"]
        elif nearest_junction <= 5:
            access_score = WEIGHTS["accessibility"] * (1.0 - 0.5 * ((nearest_junction - 1.5) / 3.5))
        else:
            access_score = WEIGHTS["accessibility"] * 0.5

        total_score = round(
            aadt_score + truck_score + service_distance_score + nearby_comp_score +
            income_score + density_score + traveller_score + access_score, 1
        )

        # Avoid presenting weak-demand remote roads as attractive solely because
        # they are far from services.
        if aadt < 10000:
            total_score = min(total_score, 45.0)

        props = dict(seg.drop(labels=["geometry"], errors="ignore"))
        props.update({
            "OPPORTUNITY_SCORE": total_score,
            "AADT_SCORE": round(aadt_score, 1),
            "TRUCK_SCORE": round(truck_score, 1),
            "SERVICE_DISTANCE_SCORE": round(service_distance_score, 1),
            "COMPETITION_SCORE": round(nearby_comp_score, 1),
            "INCOME_SCORE": round(income_score, 1),
            "DENSITY_SCORE": round(density_score, 1),
            "TRAVELLER_SCORE": round(traveller_score, 1),
            "ACCESS_SCORE": round(access_score, 1),
            "LONG_DISTANCE_FACTOR": round(long_distance_factor, 2),
            "TRAVELLER_INTENSITY": round(traveller_intensity, 1),
            "NEAREST_INTERCHANGE_MI": None if nearest_junction is None else round(nearest_junction, 1),
            "TRUCK_PERCENT_NEARBY": None if t_pct is None else round(float(t_pct), 1),
            "MEDIAN_HH_INCOME": None if income is None or not np.isfinite(float(income)) else int(income),
            "POP_DENSITY_SQMI": None if density is None or not np.isfinite(float(density)) else round(float(density), 1),
            "POPULATION": None if population is None or not np.isfinite(float(population)) else int(population),
            "NEAREST_SERVICE_MI": None if nearest_service is None else round(nearest_service, 1),
            "COMPETITION_WEIGHT_5MI": round(competition_5, 2),
            "COMPETITION_WEIGHT_10MI": round(competition_10, 2),
            "SERVICE_COUNTS_10MI": json.dumps(category_counts, separators=(",", ":")),
            "MODEL_VERSION": "0.2",
        })
        score_values.append(total_score)
        features.append({
            "type": "Feature",
            "geometry": seg.geometry.__geo_interface__,
            "properties": props,
        })

    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_version": "0.2",
        "feature_count": len(features),
        "weights": WEIGHTS,
        "score_summary": {
            "min": round(float(np.min(score_values)), 1),
            "median": round(float(np.median(score_values)), 1),
            "p75": round(float(np.percentile(score_values, 75)), 1),
            "p90": round(float(np.percentile(score_values, 90)), 1),
            "max": round(float(np.max(score_values)), 1),
        },
        "limitations": [
            "Screening score, not a site recommendation.",
            "Service distances are straight-line from segment midpoint, not routed highway travel distances.",
            "OpenStreetMap completeness and tagging vary.",
            "ACS tract conditions describe nearby community context, not traveller demographics.",
            "Truck share uses the nearest same-route truck count within approximately 15 miles.",
            "Traveller demand uses OSM tourism/lodging concentration plus a transparent interregional-route proxy, not measured trip purpose.",
            "Accessibility uses OSM motorway junction proximity and is neutralized where that tagging does not describe an at-grade route.",
            "Land availability, parcel access, visibility, entitlement constraints, land value, utilities and environmental constraints are not yet included.",
        ],
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
