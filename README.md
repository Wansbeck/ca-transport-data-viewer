# California Highway Opportunity Explorer

Interactive California highway traffic and service-area screening map.

## Current analytical layers

- Caltrans 2024 total AADT points and derived highway segments
- Caltrans 2024 truck counts and truck share
- ACS 2024 tract income and population-density context
- OpenStreetMap fuel, truck-oriented service, highway service/rest areas, EV charging, and food clusters
- OpenStreetMap traveller context using motorway junctions and tourism/overnight-stay clusters
- Corridor opportunity score

## Opportunity model v0.2

The score is a transparent screening tool rather than a site recommendation.

| Component | Weight |
| --- | ---: |
| Total AADT | 30% |
| Service scarcity | 15% |
| Competition quality/intensity | 15% |
| Traveller / tourism demand | 10% |
| Interchange accessibility | 10% |
| Median household income | 10% |
| Population density | 5% |
| Truck share | 5% |

Competition is quality-weighted so a destination-scale service area has substantially more impact than a basic fuel station, rest area, charger, or small food cluster.

Important limitations: service and interchange distances are straight-line screening proxies; OSM completeness varies; traveller demand is inferred from corridor function and mapped tourism/lodging; parcel availability, land value, site access, visibility, entitlement, utilities, environmental constraints, and routed drive-time gaps are not yet included.
