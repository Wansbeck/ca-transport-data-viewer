#!/usr/bin/env python3
"""Build corridor opportunity screening scores for destination-style highway services."""

from __future__ import annotations
import json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
from scipy.spatial import cKDTree

SEGMENTS=Path("data/aadt-2024-segments.geojson")
TRUCKS=Path("data/truck-2024.geojson")
ACS=Path("data/acs-2024-tract-context.geojson")
SERVICES=Path("data/services-osm.geojson")
TRAVELLER=Path("data/traveller-context-osm.geojson")
URBAN=Path("data/urban-areas-2020.geojson")
SRRA=Path("data/caltrans-srra.geojson")
OUT=Path("data/opportunity-2024.geojson")
META=Path("data/opportunity-2024-meta.json")

WEIGHTS={
    "traveller_long_distance":25.0,
    "service_gap":20.0,
    "competition_quality":15.0,
    "total_aadt":15.0,
    "accessibility":10.0,
    "tourism":5.0,
    "household_income":5.0,
    "truck_share":5.0,
}

# Transparent screening assumptions, not measured trip-purpose shares.
LONG_DISTANCE_SHARE={
    "5":0.55,"8":0.40,"10":0.45,"15":0.50,"40":0.60,"80":0.50,
    "101":0.35,"99":0.25,"50":0.35,"58":0.45,"395":0.50,"1":0.45,
}
DEFAULT_LONG_DISTANCE_SHARE=0.15

FALLBACK_COMPETITION_STRENGTH={
    "fuel":0.8,"truck_service":2.4,"service_area":3.0,
    "rest_area":0.8,"ev_charging":0.6,"food_cluster":1.5,
}
COMMERCIAL_CATEGORIES={"fuel","truck_service","service_area","ev_charging","food_cluster"}

def clamp(v,lo=0.0,hi=1.0): return max(lo,min(hi,v))

def linear_score(value,low,high,weight):
    if value is None:
        return 0.0
    try: value=float(value)
    except (TypeError,ValueError): return 0.0
    if not math.isfinite(value): return 0.0
    return weight*clamp((value-low)/(high-low))

def midpoint(geom):
    if geom is None or geom.is_empty: return None
    return geom.interpolate(0.5,normalized=True)

def haversine(lon1,lat1,lon2,lat2):
    r=3958.7613
    p1,p2=math.radians(lat1),math.radians(lat2)
    dp=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))

def xyz(lon,lat):
    lon=np.radians(lon); lat=np.radians(lat)
    return np.column_stack((np.cos(lat)*np.cos(lon),np.cos(lat)*np.sin(lon),np.sin(lat)))

def nearest_points(midpoints,gdf,kmax=40):
    if gdf.empty: return [([],[]) for _ in midpoints]
    lon=gdf.geometry.x.to_numpy(); lat=gdf.geometry.y.to_numpy()
    tree=cKDTree(xyz(lon,lat))
    mlon=np.array([p.x for p in midpoints]); mlat=np.array([p.y for p in midpoints])
    k=min(kmax,len(gdf))
    _,idx=tree.query(xyz(mlon,mlat),k=k)
    if k==1: idx=idx[:,None]
    out=[]
    for i in range(len(midpoints)):
        rows=[]; ds=[]
        for j in range(k):
            n=int(idx[i,j]); rows.append(gdf.iloc[n])
            ds.append(haversine(mlon[i],mlat[i],lon[n],lat[n]))
        out.append((rows,ds))
    return out

def service_stats(midpoints,services,srra):
    commercial=services[services["CATEGORY"].isin(COMMERCIAL_CATEGORIES)].copy()
    near_commercial=nearest_points(midpoints,commercial,60)
    near_srra=nearest_points(midpoints,srra,8)
    results=[]
    for i in range(len(midpoints)):
        rows,ds=near_commercial[i]
        comp5=0.0; comp10=0.0; counts=defaultdict(int)
        nearest_commercial=min(ds) if ds else None
        for row,d in zip(rows,ds):
            if d<=10:
                cat=row.get("CATEGORY")
                try: strength=float(row.get("COMPETITION_STRENGTH"))
                except (TypeError,ValueError): strength=FALLBACK_COMPETITION_STRENGTH.get(cat,1.0)
                comp10+=strength; counts[cat]+=1
                if d<=5: comp5+=strength
        srra_ds=near_srra[i][1]
        nearest_srra=min(srra_ds) if srra_ds else None
        results.append((nearest_commercial,nearest_srra,comp5,comp10,dict(counts)))
    return results

def traveller_stats(midpoints,traveller):
    junctions=traveller[traveller["CATEGORY"]=="interchange"].copy()
    clusters=traveller[traveller["CATEGORY"]=="traveller_cluster"].copy()
    nj=nearest_points(midpoints,junctions,1)
    nc=nearest_points(midpoints,clusters,15)
    results=[]
    for i in range(len(midpoints)):
        jdist=nj[i][1][0] if nj[i][1] else None
        intensity=0.0
        for row,d in zip(nc[i][0],nc[i][1]):
            if d<=25:
                intensity+=float(row.get("TRAVELLER_INTENSITY") or 0)*max(0,1-d/25)
        results.append((jdist,intensity))
    return results

def attach_context(mid_gdf,acs,urban):
    acols=[c for c in ["MEDIAN_HH_INCOME","POP_DENSITY_SQMI","POPULATION","geometry"] if c in acs.columns]
    aj=gpd.sjoin(mid_gdf,acs[acols],how="left",predicate="within")
    aj=aj[~aj.index.duplicated(keep="first")].reindex(mid_gdf.index)

    ucols=[c for c in ["NAME","BASENAME","POP100","MAJOR_URBAN_AREA","geometry"] if c in urban.columns]
    uj=gpd.sjoin(mid_gdf,urban[ucols],how="left",predicate="within")
    uj=uj[~uj.index.duplicated(keep="first")].reindex(mid_gdf.index)
    return aj,uj

def nearest_truck(midpoints,segments,trucks):
    byroute={}
    for rte,g in trucks.groupby(trucks["RTE"].astype(str)): byroute[rte]=g
    vals=[]
    for p,(_,seg) in zip(midpoints,segments.iterrows()):
        g=byroute.get(str(seg.get("RTE","")))
        if g is None or g.empty: vals.append(None); continue
        best=(999,None)
        for _,row in g.iterrows():
            d=haversine(p.x,p.y,row.geometry.x,row.geometry.y)
            if d<best[0]: best=(d,row.get("TRUCK_PERCENT"))
        vals.append(float(best[1]) if best[0]<=15 and best[1] is not None else None)
    return vals

def urban_class(urban_row,density):
    in_urban=urban_row.get("index_right") is not None and not (isinstance(urban_row.get("index_right"),float) and math.isnan(urban_row.get("index_right")))
    if not in_urban: return "rural_or_exurban",1.0,False
    major=bool(urban_row.get("MAJOR_URBAN_AREA"))
    try: dens=float(density)
    except (TypeError,ValueError): dens=0
    if major and dens>=2500:
        return "excluded_major_urban_core",0.0,True
    if major and dens>=750:
        return "major_urban_fringe",0.65,False
    return "small_urban_or_low_density_fringe",0.85,False

def main():
    seg=gpd.read_file(SEGMENTS).to_crs(4326)
    trucks=gpd.read_file(TRUCKS).to_crs(4326)
    acs=gpd.read_file(ACS).to_crs(4326)
    services=gpd.read_file(SERVICES).to_crs(4326)
    traveller=gpd.read_file(TRAVELLER).to_crs(4326)
    urban=gpd.read_file(URBAN).to_crs(4326)
    srra=gpd.read_file(SRRA).to_crs(4326)

    mids=[midpoint(g) for g in seg.geometry]
    mg=gpd.GeoDataFrame({"geometry":mids},crs="EPSG:4326")
    acsj,urbanj=attach_context(mg,acs,urban)
    trucks_pct=nearest_truck(mids,seg,trucks)
    svc=service_stats(mids,services,srra)
    tvl=traveller_stats(mids,traveller)

    features=[]; scores=[]; excluded=0
    for i,(_,s) in enumerate(seg.iterrows()):
        aadt=float(s.get("AADT") or 0)
        route=str(int(float(s.get("RTE")))) if s.get("RTE") not in (None,"") else ""
        share=LONG_DISTANCE_SHARE.get(route,DEFAULT_LONG_DISTANCE_SHARE)
        addressable=aadt*share
        income=acsj.iloc[i].get("MEDIAN_HH_INCOME")
        density=acsj.iloc[i].get("POP_DENSITY_SQMI")
        t_pct=trucks_pct[i]
        nearest_service,nearest_srra,comp5,comp10,counts=svc[i]
        nearest_junction,tourism_intensity=tvl[i]
        ustatus,umult,is_excluded=urban_class(urbanj.iloc[i],density)

        long_score=linear_score(addressable,3000,60000,WEIGHTS["traveller_long_distance"])
        gap_score=linear_score(nearest_service if nearest_service is not None else 35,2,30,WEIGHTS["service_gap"])
        # An SRRA satisfies some basic stopping need, but is not treated as direct commercial competition.
        if nearest_srra is not None:
            if nearest_srra<=5: gap_score*=0.85
            elif nearest_srra<=10: gap_score*=0.92
        comp_score=WEIGHTS["competition_quality"]*(1-clamp(comp10/14.0))
        aadt_score=linear_score(aadt,8000,90000,WEIGHTS["total_aadt"])
        if nearest_junction is None: access_score=WEIGHTS["accessibility"]*0.45
        elif nearest_junction<=1.5: access_score=WEIGHTS["accessibility"]
        elif nearest_junction<=5: access_score=WEIGHTS["accessibility"]*(1-0.55*((nearest_junction-1.5)/3.5))
        else: access_score=WEIGHTS["accessibility"]*0.45
        tourism_score=linear_score(tourism_intensity,0,180,WEIGHTS["tourism"])
        income_score=linear_score(income,50000,160000,WEIGHTS["household_income"])
        truck_score=linear_score(t_pct,3,20,WEIGHTS["truck_share"])

        raw=long_score+gap_score+comp_score+aadt_score+access_score+tourism_score+income_score+truck_score
        score=0.0 if is_excluded else round(raw*umult,1)
        if aadt<8000 and not is_excluded: score=min(score,40.0)
        if is_excluded: excluded+=1

        p=dict(s.drop(labels=["geometry"],errors="ignore"))
        p.update({
            "OPPORTUNITY_SCORE":score,"RAW_SCORE":round(raw,1),
            "URBAN_STATUS":ustatus,"URBAN_MULTIPLIER":umult,"URBAN_EXCLUDED":is_excluded,
            "LONG_DISTANCE_SHARE":share,"ADDRESSABLE_AADT":round(addressable),
            "LONG_DISTANCE_SCORE":round(long_score,1),"AADT_SCORE":round(aadt_score,1),
            "SERVICE_GAP_SCORE":round(gap_score,1),"COMPETITION_SCORE":round(comp_score,1),
            "ACCESS_SCORE":round(access_score,1),"TOURISM_SCORE":round(tourism_score,1),
            "INCOME_SCORE":round(income_score,1),"TRUCK_SCORE":round(truck_score,1),
            "TRUCK_PERCENT_NEARBY":None if t_pct is None else round(t_pct,1),
            "MEDIAN_HH_INCOME":None if income is None or not np.isfinite(float(income)) else int(income),
            "POP_DENSITY_SQMI":None if density is None or not np.isfinite(float(density)) else round(float(density),1),
            "NEAREST_SERVICE_MI":None if nearest_service is None else round(nearest_service,1),
            "NEAREST_SRRA_MI":None if nearest_srra is None else round(nearest_srra,1),
            "NEAREST_INTERCHANGE_MI":None if nearest_junction is None else round(nearest_junction,1),
            "TOURISM_INTENSITY":round(tourism_intensity,1),
            "COMPETITION_WEIGHT_5MI":round(comp5,2),"COMPETITION_WEIGHT_10MI":round(comp10,2),
            "SERVICE_COUNTS_10MI":json.dumps(counts,separators=(",",":")),
            "MODEL_VERSION":"0.3"
        })
        scores.append(score)
        features.append({"type":"Feature","geometry":s.geometry.__geo_interface__,"properties":p})

    OUT.write_text(json.dumps({"type":"FeatureCollection","features":features},separators=(",",":")),encoding="utf-8")
    meta={
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "model_version":"0.3","feature_count":len(features),"excluded_urban_core_segments":excluded,
      "weights":WEIGHTS,"long_distance_share_assumptions":LONG_DISTANCE_SHARE,
      "default_long_distance_share":DEFAULT_LONG_DISTANCE_SHARE,
      "urban_rule":{
        "excluded":"Inside a Census major urban area (2020 population >=100,000) and local ACS tract density >=2,500/sq mi",
        "major_urban_fringe_multiplier":0.65,
        "small_urban_or_low_density_fringe_multiplier":0.85
      },
      "limitations":[
        "Screening model, not a site recommendation.",
        "Long-distance shares are explicit modelling assumptions, not observed trip-purpose data.",
        "Service, SRRA and interchange distances are straight-line screening proxies, not routed highway distance.",
        "Census urban-area and ACS-density rules approximate urban suitability; parcel-level context may differ.",
        "Tourism demand uses mapped attraction/lodging concentration rather than measured visitation.",
        "Land availability, parcel access, visibility, entitlement, utilities, environmental constraints and land economics are not yet included."
      ]
    }
    if scores:
        meta["score_summary"]={"min":round(float(np.min(scores)),1),"median":round(float(np.median(scores)),1),"p75":round(float(np.percentile(scores,75)),1),"p90":round(float(np.percentile(scores,90)),1),"max":round(float(np.max(scores)),1)}
    META.write_text(json.dumps(meta,indent=2),encoding="utf-8")
    print(json.dumps(meta,indent=2))

if __name__=="__main__": main()
