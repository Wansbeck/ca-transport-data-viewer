#!/usr/bin/env python3
import json, requests
from pathlib import Path
from datetime import datetime, timezone

URL="https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Rest_Areas/FeatureServer/0/query"
OUT=Path("data/caltrans-srra.geojson")
META=Path("data/caltrans-srra-meta.json")

def main():
    params={
      "where":"1=1","outFields":"*","returnGeometry":"true",
      "outSR":"4326","f":"geojson"
    }
    r=requests.get(URL,params=params,timeout=180)
    r.raise_for_status()
    data=r.json()
    OUT.write_text(json.dumps(data,separators=(",",":")),encoding="utf-8")
    META.write_text(json.dumps({
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "source":"Caltrans Safety Roadside Rest Areas FeatureServer",
      "feature_count":len(data.get("features",[])),
      "source_dataset_note":"Caltrans dataset states source data was last updated 2023-08-15."
    },indent=2),encoding="utf-8")
    print(f"Wrote {len(data.get('features',[]))} Caltrans SRRA features")

if __name__=="__main__": main()
