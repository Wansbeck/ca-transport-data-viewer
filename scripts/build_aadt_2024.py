import json
import math
import re
from pathlib import Path

import pandas as pd
import requests

AADT_2024_URL = "https://dot.ca.gov/-/media/dot-media/programs/traffic-operations/documents/census/2024/2024-traffic-volumes-ca-a11y.xlsx"
GIS_QUERY_URL = "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Traffic_AADT/FeatureServer/0/query"
OUT = Path("data/aadt-2024.geojson")
META = Path("data/aadt-2024-meta.json")

def clean_name(v):
    return re.sub(r"[^a-z0-9]+", "_", str(v).strip().lower()).strip("_")

def clean_text(v):
    if pd.isna(v):
        return ""
    return str(v).strip()

def clean_route(v):
    s = clean_text(v)
    if not s:
        return ""
    m = re.search(r"\d+", s)
    return str(int(m.group())) if m else s.upper()

def clean_county(v):
    return re.sub(r"[^A-Z]", "", clean_text(v).upper())

def clean_pm(v):
    if pd.isna(v) or clean_text(v) == "":
        return None
    try:
        return round(float(v), 3)
    except Exception:
        m = re.search(r"-?\d+(?:\.\d+)?", clean_text(v))
        return round(float(m.group()), 3) if m else None

def to_int(v):
    if pd.isna(v) or clean_text(v) == "":
        return None
    s = clean_text(v).replace(",", "")
    try:
        return int(round(float(s)))
    except Exception:
        return None

def find_header_row(path):
    preview = pd.read_excel(path, header=None, nrows=25)
    for i, row in preview.iterrows():
        vals = [clean_name(x) for x in row.tolist()]
        joined = " ".join(vals)
        has_route = "route" in joined
        has_postmile = "postmile" in joined or "post_mile" in joined or ("post" in joined and "mile" in joined)
        has_county = "county" in joined or re.search(r"(^|_)co($|_)", joined) is not None
        if has_route and has_postmile and has_county:
            return i
    print("Workbook preview:")
    print(preview.to_string(index=True, header=False))
    raise RuntimeError("Could not identify the workbook header row.")

def find_col(columns, required_tokens, forbidden_tokens=()):
    for c in columns:
        n = clean_name(c)
        if all(t in n for t in required_tokens) and not any(t in n for t in forbidden_tokens):
            return c
    return None

def download_xlsx(path):
    r = requests.get(AADT_2024_URL, timeout=60)
    r.raise_for_status()
    path.write_bytes(r.content)

def load_gis():
    features = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": 2000,
        }
        r = requests.get(GIS_QUERY_URL, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        batch = page.get("features", [])
        features.extend(batch)
        if len(batch) < 2000:
            break
        offset += len(batch)
    return features

def prop_value(props, *candidates):
    norm = {clean_name(k): v for k, v in props.items()}
    for c in candidates:
        if clean_name(c) in norm:
            return norm[clean_name(c)]
    return None

def main():
    Path("data").mkdir(exist_ok=True)
    xlsx = Path("/tmp/2024-aadt.xlsx")
    download_xlsx(xlsx)

    header_row = find_header_row(xlsx)
    df = pd.read_excel(xlsx, header=header_row)
    df = df.dropna(how="all")
    print("Workbook columns:", list(df.columns))

    route_col = find_col(df.columns, ("route",))
    county_col = find_col(df.columns, ("county",))
    pm_col = find_col(df.columns, ("postmile",)) or find_col(df.columns, ("post", "mile"))
    desc_col = find_col(df.columns, ("description",))
    back_aadt_col = find_col(df.columns, ("back", "aadt"))
    ahead_aadt_col = find_col(df.columns, ("ahead", "aadt"))
    back_peak_col = find_col(df.columns, ("back", "peak", "hour"))
    ahead_peak_col = find_col(df.columns, ("ahead", "peak", "hour"))

    required = {
        "route": route_col,
        "county": county_col,
        "postmile": pm_col,
        "back_aadt": back_aadt_col,
        "ahead_aadt": ahead_aadt_col,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise RuntimeError(f"Missing expected columns: {missing}. Columns were: {list(df.columns)}")

    gis = load_gis()
    print(f"Loaded {len(gis)} GIS reference points.")

    index = {}
    by_route_county = {}
    for f in gis:
        p = f.get("properties", {})
        route = clean_route(prop_value(p, "RTE", "Route"))
        county = clean_county(prop_value(p, "CNTY", "CO", "County"))
        pm = clean_pm(prop_value(p, "PM", "Postmile"))
        if not route or not county or pm is None:
            continue
        key = (route, county, pm)
        index.setdefault(key, []).append(f)
        by_route_county.setdefault((route, county), []).append((pm, f))

    output = []
    exact = 0
    nearest = 0
    unmatched = 0

    for _, row in df.iterrows():
        route = clean_route(row.get(route_col))
        county = clean_county(row.get(county_col))
        pm = clean_pm(row.get(pm_col))
        if not route or not county or pm is None:
            continue

        match = None
        candidates = index.get((route, county, pm), [])
        if candidates:
            match = candidates[0]
            exact += 1
        else:
            rc = by_route_county.get((route, county), [])
            if rc:
                best_pm, best_feature = min(rc, key=lambda x: abs(x[0] - pm))
                if abs(best_pm - pm) <= 0.03:
                    match = best_feature
                    nearest += 1

        if not match:
            unmatched += 1
            continue

        back = to_int(row.get(back_aadt_col))
        ahead = to_int(row.get(ahead_aadt_col))
        values = [v for v in (back, ahead) if v is not None]
        max_aadt = max(values) if values else 0

        props = {
            "YEAR": 2024,
            "RTE": route,
            "CNTY": county,
            "PM": pm,
            "DESCRIPTION": clean_text(row.get(desc_col)) if desc_col else "",
            "BACK_AADT": back,
            "AHEAD_AADT": ahead,
            "MAX_AADT": max_aadt,
            "BACK_PEAK_HOUR": to_int(row.get(back_peak_col)) if back_peak_col else None,
            "AHEAD_PEAK_HOUR": to_int(row.get(ahead_peak_col)) if ahead_peak_col else None,
            "MATCH_METHOD": "exact" if candidates else "nearest_postmile",
        }

        output.append({
            "type": "Feature",
            "geometry": match["geometry"],
            "properties": props,
        })

    fc = {"type": "FeatureCollection", "features": output}
    OUT.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")

    meta = {
        "source_year": 2024,
        "source_url": AADT_2024_URL,
        "gis_reference_layer": GIS_QUERY_URL,
        "records_written": len(output),
        "exact_matches": exact,
        "nearest_postmile_matches": nearest,
        "unmatched_rows": unmatched,
        "header_row": header_row,
        "columns": [str(c) for c in df.columns],
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
