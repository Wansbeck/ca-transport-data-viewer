import json
import re
from pathlib import Path

import pandas as pd
import requests

TRUCK_2024_URL = "https://dot.ca.gov/-/media/dot-media/programs/traffic-operations/documents/census/2024/2024-truck-aadt-a11y.xlsx"
GIS_QUERY_URL = "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Truck_Volumes_AADT/FeatureServer/0/query"
OUT = Path("data/truck-2024.geojson")
META = Path("data/truck-2024-meta.json")

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

def json_value(v):
    if pd.isna(v):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
        return int(n) if n.is_integer() else n
    s = str(v).strip()
    if s == "":
        return None
    try:
        n = float(s.replace(",", "").replace("%", ""))
        return int(n) if n.is_integer() else n
    except Exception:
        return s

def find_sheet_and_header(path):
    book = pd.ExcelFile(path)
    print("Workbook sheets:", book.sheet_names)
    for sheet in book.sheet_names:
        preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=35)
        if preview.empty:
            continue
        for i, row in preview.iterrows():
            vals = [clean_name(x) for x in row.tolist()]
            joined = " ".join(vals)
            has_route = "route" in joined or "rte" in vals
            has_pm = "postmile" in joined or "post_mile" in joined or "pm" in vals
            has_county = "county" in joined or "cnty" in vals or "co" in vals
            if has_route and has_pm and has_county:
                print(f"Using sheet {sheet!r}, header row {i}")
                return sheet, i
        print(f"Preview for sheet {sheet!r}:")
        print(preview.head(15).to_string(index=True, header=False))
    raise RuntimeError("Could not identify truck workbook data sheet/header.")

def download_xlsx(path):
    r = requests.get(TRUCK_2024_URL, timeout=60)
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

def pick_col(norm_cols, *names):
    for name in names:
        if name in norm_cols:
            return norm_cols[name]
    return None

def first_numeric(row, columns):
    for c in columns:
        if c is None:
            continue
        v = json_value(row.get(c))
        if isinstance(v, (int, float)):
            return v
    return None

def main():
    Path("data").mkdir(exist_ok=True)
    xlsx = Path("/tmp/2024-truck.xlsx")
    download_xlsx(xlsx)

    sheet_name, header_row = find_sheet_and_header(xlsx)
    df = pd.read_excel(xlsx, sheet_name=sheet_name, header=header_row).dropna(how="all")
    print("Workbook columns:", list(df.columns))

    norm_cols = {clean_name(col): col for col in df.columns}
    route_col = pick_col(norm_cols, "rte", "route")
    county_col = pick_col(norm_cols, "cnty", "county", "co")
    pm_col = pick_col(norm_cols, "pm", "postmile", "post_mile")
    desc_col = pick_col(norm_cols, "description", "desc")

    if not all([route_col, county_col, pm_col]):
        raise RuntimeError(f"Missing route/county/postmile columns. Columns: {list(df.columns)}")

    gis = load_gis()
    print(f"Loaded {len(gis)} truck GIS reference points.")

    index = {}
    by_route_county = {}
    for f in gis:
        p = f.get("properties", {})
        route = clean_route(prop_value(p, "RTE", "Route"))
        county = clean_county(prop_value(p, "CNTY", "CO", "County"))
        pm = clean_pm(prop_value(p, "PM", "Postmile"))
        if not route or not county or pm is None:
            continue
        index.setdefault((route, county, pm), []).append(f)
        by_route_county.setdefault((route, county), []).append((pm, f))

    exact = nearest = unmatched = 0
    output = []

    total_aadt_candidates = [
        pick_col(norm_cols, "total_aadt"),
        pick_col(norm_cols, "vehicle_aadt"),
        pick_col(norm_cols, "aadt"),
    ]
    truck_aadt_candidates = [
        pick_col(norm_cols, "truck_aadt"),
        pick_col(norm_cols, "total_truck_aadt"),
        pick_col(norm_cols, "truck_adt"),
    ]
    truck_pct_candidates = [
        pick_col(norm_cols, "truck_percent"),
        pick_col(norm_cols, "truck_pct"),
        pick_col(norm_cols, "percent_trucks"),
        pick_col(norm_cols, "pct_trucks"),
    ]

    for _, row in df.iterrows():
        route = clean_route(row.get(route_col))
        county = clean_county(row.get(county_col))
        pm = clean_pm(row.get(pm_col))
        if not route or not county or pm is None:
            continue

        candidates = index.get((route, county, pm), [])
        match = candidates[0] if candidates else None
        method = "exact" if match else None

        if not match:
            rc = by_route_county.get((route, county), [])
            if rc:
                best_pm, best_feature = min(rc, key=lambda x: abs(x[0] - pm))
                if abs(best_pm - pm) <= 0.03:
                    match = best_feature
                    method = "nearest_postmile"

        if not match:
            unmatched += 1
            continue

        exact += method == "exact"
        nearest += method == "nearest_postmile"

        props = {
            "YEAR": 2024,
            "RTE": route,
            "CNTY": county,
            "PM": pm,
            "DESCRIPTION": clean_text(row.get(desc_col)) if desc_col else "",
            "MATCH_METHOD": method,
        }

        for col in df.columns:
            key = clean_name(col).upper()
            if key in {"RTE", "CNTY", "PM", "DESCRIPTION"}:
                continue
            props[key] = json_value(row.get(col))

        total_aadt = first_numeric(row, total_aadt_candidates)
        truck_aadt = first_numeric(row, truck_aadt_candidates)
        truck_pct = first_numeric(row, truck_pct_candidates)

        if truck_pct is None and total_aadt and truck_aadt is not None:
            truck_pct = round((truck_aadt / total_aadt) * 100, 2)

        props["TOTAL_AADT"] = total_aadt
        props["TRUCK_AADT"] = truck_aadt
        props["TRUCK_PERCENT"] = truck_pct

        output.append({
            "type": "Feature",
            "geometry": match["geometry"],
            "properties": props,
        })

    OUT.write_text(json.dumps({"type":"FeatureCollection","features":output}, separators=(",",":")), encoding="utf-8")

    meta = {
        "source_year": 2024,
        "source_url": TRUCK_2024_URL,
        "gis_reference_layer": GIS_QUERY_URL,
        "records_written": len(output),
        "exact_matches": int(exact),
        "nearest_postmile_matches": int(nearest),
        "unmatched_rows": int(unmatched),
        "sheet_name": sheet_name,
        "header_row": int(header_row),
        "columns": [str(c) for c in df.columns],
        "normalized_columns": list(norm_cols.keys()),
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
