#!/usr/bin/env python3
"""Create a lightweight web-display version of the corridor opportunity layer."""

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

SRC = Path("data/opportunity-2024.geojson")
OUT = Path("data/opportunity-2024-lite.geojson")
META = Path("data/opportunity-2024-lite-meta.json")

KEEP = [
    "RTE","CNTY","START_PM","END_PM","AADT",
    "OPPORTUNITY_SCORE","RAW_SCORE",
    "URBAN_STATUS","URBAN_MULTIPLIER","URBAN_EXCLUDED",
    "LONG_DISTANCE_SHARE","ADDRESSABLE_AADT",
    "LONG_DISTANCE_SCORE","SERVICE_GAP_SCORE","COMPETITION_SCORE",
    "AADT_SCORE","ACCESS_SCORE","TOURISM_SCORE","INCOME_SCORE","TRUCK_SCORE",
    "TRUCK_PERCENT_NEARBY","MEDIAN_HH_INCOME","POP_DENSITY_SQMI",
    "NEAREST_SERVICE_MI","NEAREST_SRRA_MI","NEAREST_INTERCHANGE_MI",
    "MODEL_VERSION",
]

def json_safe(v):
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v

def main():
    gdf = gpd.read_file(SRC).to_crs(4326)

    # Simplify in a California projected CRS so tolerance is in metres.
    projected = gdf.to_crs(3310)
    projected["geometry"] = projected.geometry.simplify(100, preserve_topology=True)
    lite = projected.to_crs(4326)

    cols = [c for c in KEEP if c in lite.columns] + ["geometry"]
    lite = lite[cols]

    features = []
    for _, row in lite.iterrows():
        props = {c: json_safe(row[c]) for c in cols if c != "geometry"}
        features.append({
            "type":"Feature",
            "geometry":row.geometry.__geo_interface__,
            "properties":props,
        })

    OUT.write_text(
        json.dumps({"type":"FeatureCollection","features":features}, separators=(",",":")),
        encoding="utf-8",
    )

    meta = {
        "source":"data/opportunity-2024.geojson",
        "feature_count":len(features),
        "simplification_tolerance_m":100,
        "output_bytes":OUT.stat().st_size,
        "source_bytes":SRC.stat().st_size,
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
