import json
from collections import defaultdict
from pathlib import Path

import requests
from shapely.geometry import shape, mapping, LineString, MultiLineString
from shapely.ops import substring, linemerge

SHN_URL = "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/SHN_Lines/FeatureServer/0/query"
AADT_PATH = Path("data/aadt-2024.geojson")
OUT = Path("data/aadt-2024-segments.geojson")
META = Path("data/aadt-2024-segments-meta.json")

def s(v):
    return "" if v is None else str(v).strip().upper()

def route(v):
    try:
        return str(int(v))
    except Exception:
        return s(v).lstrip("0") or "0"

def num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None

def traffic_value(current, nxt):
    vals = []
    a = num(current.get("AHEAD_AADT"))
    b = num(nxt.get("BACK_AADT"))
    if a and a > 0:
        vals.append(a)
    if b and b > 0:
        vals.append(b)
    if not vals:
        a = num(current.get("MAX_AADT"))
        b = num(nxt.get("MAX_AADT"))
        if a and a > 0:
            vals.append(a)
        if b and b > 0:
            vals.append(b)
    if not vals:
        return None
    return round(sum(vals) / len(vals))

def fetch_shn():
    features = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "Route,RteSuffix,County,District,PMPrefix,bPM,ePM,PMSuffix,PMRouteID,RouteType,Direction,AlignCode",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": 2000,
        }
        r = requests.get(SHN_URL, params=params, timeout=90)
        r.raise_for_status()
        page = r.json()
        batch = page.get("features", [])
        features.extend(batch)
        if len(batch) < 2000:
            break
        offset += len(batch)
    return features

def make_intervals(features):
    groups = defaultdict(list)
    for f in features:
        p = f.get("properties", {})
        pm = num(p.get("PM"))
        if pm is None:
            continue
        key = (
            route(p.get("RTE")),
            s(p.get("RTE_SFX")),
            s(p.get("CNTY")),
            s(p.get("PM_PFX")),
            s(p.get("PM_SFX")),
        )
        groups[key].append(p)

    intervals = defaultdict(list)
    for key, rows in groups.items():
        # Collapse duplicate postmiles, preferring the row with the strongest traffic values.
        by_pm = {}
        for p in rows:
            pm = float(p["PM"])
            score = max(num(p.get("MAX_AADT")) or 0, num(p.get("AHEAD_AADT")) or 0, num(p.get("BACK_AADT")) or 0)
            if pm not in by_pm or score > by_pm[pm][0]:
                by_pm[pm] = (score, p)
        ordered = [by_pm[k][1] for k in sorted(by_pm)]

        for current, nxt in zip(ordered[:-1], ordered[1:]):
            start = float(current["PM"])
            end = float(nxt["PM"])
            if end <= start:
                continue
            aadt = traffic_value(current, nxt)
            if aadt is None:
                continue
            intervals[key].append({
                "start_pm": start,
                "end_pm": end,
                "aadt": int(aadt),
                "start_desc": current.get("DESCRIPTION") or "",
                "end_desc": nxt.get("DESCRIPTION") or "",
                "start_ahead": current.get("AHEAD_AADT"),
                "end_back": nxt.get("BACK_AADT"),
            })
    return intervals

def usable_line(geom):
    g = shape(geom)
    if isinstance(g, LineString):
        return g
    if isinstance(g, MultiLineString):
        merged = linemerge(g)
        if isinstance(merged, LineString):
            return merged
    return None

def main():
    aadt = json.loads(AADT_PATH.read_text(encoding="utf-8"))
    intervals = make_intervals(aadt.get("features", []))
    shn = fetch_shn()
    print(f"Loaded {len(shn)} SHN line features and {sum(len(v) for v in intervals.values())} traffic intervals.")

    output = []
    matched_lines = 0
    no_intervals = 0
    bad_geometry = 0

    # Fallback index ignoring postmile prefix/suffix. Used only when the exact
    # Caltrans PM route grouping has no traffic intervals.
    broad = defaultdict(list)
    for key, vals in intervals.items():
        broad[key[:3]].extend(vals)

    for f in shn:
        p = f.get("properties", {})
        b = num(p.get("bPM"))
        e = num(p.get("ePM"))
        if b is None or e is None or e <= b:
            continue

        key = (
            route(p.get("Route")),
            s(p.get("RteSuffix")),
            s(p.get("County")),
            s(p.get("PMPrefix")),
            s(p.get("PMSuffix")),
        )
        candidates = intervals.get(key)
        match_method = "exact_pm_route"

        if not candidates:
            candidates = broad.get(key[:3], [])
            match_method = "route_county_fallback"

        if not candidates:
            no_intervals += 1
            continue

        line = usable_line(f.get("geometry"))
        if line is None or line.length == 0:
            bad_geometry += 1
            continue

        wrote = False
        for iv in candidates:
            ov_start = max(b, iv["start_pm"])
            ov_end = min(e, iv["end_pm"])
            if ov_end <= ov_start:
                continue

            # Caltrans supplies begin/end postmiles for each SHN geometry.
            # We use their relative position to clip the polyline. This is an
            # analytical approximation rather than an official traffic segment.
            start_fraction = max(0.0, min(1.0, (ov_start - b) / (e - b)))
            end_fraction = max(0.0, min(1.0, (ov_end - b) / (e - b)))
            if end_fraction <= start_fraction:
                continue

            clipped = substring(
                line,
                start_fraction * line.length,
                end_fraction * line.length,
            )
            if clipped.is_empty or clipped.length == 0:
                continue

            output.append({
                "type": "Feature",
                "geometry": mapping(clipped),
                "properties": {
                    "YEAR": 2024,
                    "RTE": key[0],
                    "RTE_SFX": key[1],
                    "CNTY": key[2],
                    "PM_PFX": key[3],
                    "PM_SFX": key[4],
                    "START_PM": round(ov_start, 3),
                    "END_PM": round(ov_end, 3),
                    "AADT": iv["aadt"],
                    "START_DESC": iv["start_desc"],
                    "END_DESC": iv["end_desc"],
                    "START_AHEAD_AADT": iv["start_ahead"],
                    "END_BACK_AADT": iv["end_back"],
                    "DISTRICT": p.get("District"),
                    "ROUTE_TYPE": p.get("RouteType"),
                    "DIRECTION": p.get("Direction"),
                    "ALIGN_CODE": p.get("AlignCode"),
                    "MATCH_METHOD": match_method,
                    "DERIVATION": "Between adjacent 2024 Caltrans AADT count locations; geometry clipped proportionally by SHN postmile.",
                },
            })
            wrote = True

        if wrote:
            matched_lines += 1

    fc = {"type": "FeatureCollection", "features": output}
    OUT.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")

    meta = {
        "source_year": 2024,
        "shn_source": SHN_URL,
        "aadt_source": str(AADT_PATH),
        "segment_features_written": len(output),
        "shn_features_loaded": len(shn),
        "shn_features_with_segments": matched_lines,
        "shn_features_without_intervals": no_intervals,
        "bad_geometries": bad_geometry,
        "methodology": (
            "Traffic intervals are constructed between adjacent 2024 Caltrans AADT count locations. "
            "AADT for each interval is the mean of the upstream station's Ahead AADT and downstream "
            "station's Back AADT where available. Official SHN lines are clipped proportionally using "
            "their begin/end postmile measures. The result is a derived analytical layer, not an official "
            "Caltrans segment-level AADT dataset."
        ),
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
