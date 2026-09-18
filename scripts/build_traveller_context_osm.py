#!/usr/bin/env python3
"""Build statewide traveller-demand and access context from OpenStreetMap."""

import json, math, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import requests

BBOX=(32.52,-124.48,42.01,-114.13)
OUT=Path("data/traveller-context-osm.geojson")
META=Path("data/traveller-context-osm-meta.json")
URLS=["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter"]

QUERY="""
[out:json][timeout:180];
(
  node["highway"="motorway_junction"]({s},{w},{n},{e});
  nwr["tourism"~"attraction|hotel|motel|camp_site|caravan_site"]["name"]({s},{w},{n},{e});
  nwr["leisure"="theme_park"]["name"]({s},{w},{n},{e});
);
out center tags;
"""

def query(q):
    err=None
    for i in range(5):
        try:
            r=requests.post(URLS[i%len(URLS)],data={"data":q},
                headers={"User-Agent":"ca-transport-data-viewer/0.9"},timeout=240)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            err=exc; time.sleep(8*(i+1))
    raise RuntimeError(err)

def xy(el):
    if "lat" in el: return float(el["lon"]),float(el["lat"])
    c=el.get("center") or {}
    if "lat" in c: return float(c["lon"]),float(c["lat"])
    return None

def main():
    s,w,n,e=BBOX
    raw=query(QUERY.format(s=s,w=w,n=n,e=e))
    junctions=[]
    travel=[]
    for el in raw.get("elements",[]):
        p=xy(el)
        if not p: continue
        t=el.get("tags") or {}
        if t.get("highway")=="motorway_junction":
            junctions.append({
                "type":"Feature","geometry":{"type":"Point","coordinates":list(p)},
                "properties":{"CATEGORY":"interchange","NAME":t.get("name") or t.get("ref"),
                    "REF":t.get("ref"),"OSM_ID":el.get("id")}
            })
        else:
            tourism=t.get("tourism")
            cat="theme_park" if t.get("leisure")=="theme_park" else tourism
            travel.append((p[0],p[1],cat,t.get("name")))

    # Aggregate tourism/hospitality POIs into ~10 km cells to keep map/data compact.
    cell=.1
    buckets=defaultdict(list)
    for lon,lat,cat,name in travel:
        buckets[(math.floor(lat/cell),math.floor(lon/cell))].append((lon,lat,cat,name))

    clusters=[]
    for vals in buckets.values():
        if len(vals)<2: continue
        lon=sum(v[0] for v in vals)/len(vals); lat=sum(v[1] for v in vals)/len(vals)
        attractions=sum(1 for v in vals if v[2] in ("attraction","theme_park"))
        lodging=sum(1 for v in vals if v[2] in ("hotel","motel","camp_site","caravan_site"))
        intensity=min(100, attractions*8 + lodging*3)
        samples=[]
        for v in vals:
            if v[3] and v[3] not in samples and len(samples)<5: samples.append(v[3])
        clusters.append({
            "type":"Feature","geometry":{"type":"Point","coordinates":[round(lon,6),round(lat,6)]},
            "properties":{"CATEGORY":"traveller_cluster","COUNT":len(vals),
                "ATTRACTIONS":attractions,"LODGING":lodging,
                "TRAVELLER_INTENSITY":intensity,
                "SAMPLE_NAMES":", ".join(samples) if samples else None}
        })

    features=junctions+clusters
    OUT.write_text(json.dumps({"type":"FeatureCollection","features":features},separators=(",",":")),encoding="utf-8")
    META.write_text(json.dumps({
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "source":"OpenStreetMap contributors via Overpass API",
        "junction_count":len(junctions),"traveller_cluster_count":len(clusters),
        "notes":["Tourism/hospitality intensity is a screening proxy, not measured visitation.",
                 "Interchanges are OSM motorway_junction nodes; at-grade highway access is not fully represented."]
    },indent=2),encoding="utf-8")
    print(f"Wrote {len(junctions)} junctions and {len(clusters)} traveller clusters")

if __name__=="__main__": main()
