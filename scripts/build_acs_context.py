import io
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

YEAR = 2024
STATE_FIPS = "06"

INCOME_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b19013.dat"
POP_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b01003.dat"
TRACT_URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_06_tract_500k.zip"

OUT = Path("data/acs-2024-tract-context.geojson")
META = Path("data/acs-2024-tract-context-meta.json")

def download_bytes(url):
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content

def read_table(url, usecols):
    content = download_bytes(url)
    df = pd.read_csv(
        io.BytesIO(content),
        sep="|",
        dtype={"GEO_ID": str},
        usecols=usecols,
        low_memory=False,
    )
    prefix = f"1400000US{STATE_FIPS}"
    return df[df["GEO_ID"].str.startswith(prefix, na=False)].copy()

def clean_num(series):
    return pd.to_numeric(series, errors="coerce").where(lambda x: x >= 0)

def main():
    Path("data").mkdir(exist_ok=True)

    print("Downloading 2024 ACS median household income...")
    income = read_table(
        INCOME_URL,
        ["GEO_ID", "B19013_E001", "B19013_M001"],
    )
    income["GEOID"] = income["GEO_ID"].str.replace("1400000US", "", regex=False)
    income["MEDIAN_HH_INCOME"] = clean_num(income["B19013_E001"])
    income["MEDIAN_HH_INCOME_MOE"] = clean_num(income["B19013_M001"])

    print("Downloading 2024 ACS population...")
    pop = read_table(
        POP_URL,
        ["GEO_ID", "B01003_E001", "B01003_M001"],
    )
    pop["GEOID"] = pop["GEO_ID"].str.replace("1400000US", "", regex=False)
    pop["POPULATION"] = clean_num(pop["B01003_E001"])
    pop["POPULATION_MOE"] = clean_num(pop["B01003_M001"])

    print("Downloading 2024 California tract cartographic boundaries...")
    tract_zip = download_bytes(TRACT_URL)
    zip_path = "/tmp/ca_tracts.zip"
    Path(zip_path).write_bytes(tract_zip)

    tracts = gpd.read_file(f"zip://{zip_path}")
    tracts = tracts[tracts["STATEFP"] == STATE_FIPS].copy()

    # ALAND is Census land area in square meters. Use this attribute, not
    # cartographic polygon area, for density.
    tracts["LAND_SQMI"] = pd.to_numeric(tracts["ALAND"], errors="coerce") / 2_589_988.110336

    merged = tracts.merge(
        income[["GEOID", "MEDIAN_HH_INCOME", "MEDIAN_HH_INCOME_MOE"]],
        on="GEOID",
        how="left",
    ).merge(
        pop[["GEOID", "POPULATION", "POPULATION_MOE"]],
        on="GEOID",
        how="left",
    )

    merged["POP_DENSITY_SQMI"] = (
        merged["POPULATION"] / merged["LAND_SQMI"]
    ).where(merged["LAND_SQMI"] > 0)

    keep = [
        "GEOID",
        "NAME",
        "COUNTYFP",
        "MEDIAN_HH_INCOME",
        "MEDIAN_HH_INCOME_MOE",
        "POPULATION",
        "POPULATION_MOE",
        "POP_DENSITY_SQMI",
        "LAND_SQMI",
        "geometry",
    ]
    merged = merged[keep].to_crs(4326)

    # Round display/context fields to reduce file size.
    merged["MEDIAN_HH_INCOME"] = merged["MEDIAN_HH_INCOME"].round()
    merged["MEDIAN_HH_INCOME_MOE"] = merged["MEDIAN_HH_INCOME_MOE"].round()
    merged["POPULATION"] = merged["POPULATION"].round()
    merged["POPULATION_MOE"] = merged["POPULATION_MOE"].round()
    merged["POP_DENSITY_SQMI"] = merged["POP_DENSITY_SQMI"].round(1)
    merged["LAND_SQMI"] = merged["LAND_SQMI"].round(3)

    # GeoPandas' to_json omits NaN cleanly with na="null".
    OUT.write_text(merged.to_json(na="null", drop_id=True), encoding="utf-8")

    meta = {
        "source_year": YEAR,
        "acs_release": "2024 ACS 5-year estimates",
        "geography": "California census tracts",
        "tract_count": int(len(merged)),
        "income_records": int(income["MEDIAN_HH_INCOME"].notna().sum()),
        "population_records": int(pop["POPULATION"].notna().sum()),
        "income_variable": "B19013_E001",
        "income_definition": "Median household income in the past 12 months (2024 inflation-adjusted dollars)",
        "population_variable": "B01003_E001",
        "income_source": INCOME_URL,
        "population_source": POP_URL,
        "geometry_source": TRACT_URL,
        "notes": [
            "ACS values are survey estimates and include margins of error.",
            "Population density uses Census ALAND land area rather than cartographic polygon area.",
            "Cartographic tract boundaries are generalized for web mapping."
        ],
    }
    META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"GeoJSON bytes: {OUT.stat().st_size:,}")

if __name__ == "__main__":
    main()
