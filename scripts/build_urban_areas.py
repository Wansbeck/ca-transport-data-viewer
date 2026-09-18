#!/usr/bin/env python3
import json, requests
from pathlib import Path
from datetime import datetime, timezone

URL="https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Urban/MapServer/0/query"
OUT=Path("data/urban-areas-2020.geojson")
META=Path("data/urban-areas-2020-meta.json")

params={
  "where":"1=1",
  "geometry":"-124.48,32.52,-114.13,42.01",
  "geometryType":"esriGeometryEnvelope",
  "inSR":"4326",
  "spatialRel":"esriSpatialRelIntersects",
  "outFields":"GEOID,BASENAME,NAME,POP100,HU100,AREALAND",
  "returnGeometry":"true",
  "outSR":"4326",
  "f":"geojson"
}

def main():
    r=requests.get(URL,params=params,timeout=180)
    r.raise_for_status()
    data=r.json()
    for f in data.get("features",[]):
        p=f["properties"]
        pop=float(p.get("POP100") or 0)
        p["MAJOR_URBAN_AREA"]=pop>=100000
    OUT.write_text(json.dumps(data,separators=(",",":")),encoding="utf-8")
    META.write_text(json.dumps({
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "source":"U.S. Census Bureau TIGERweb 2020 Urban Areas",
      "feature_count":len(data.get("features",[])),
      "major_area_threshold_population":100000
    },indent=2),encoding="utf-8")
    print(f"Wrote {len(data.get('features',[]))} California urban areas")

if __name__=="__main__": main()
