# California Highway Opportunity Explorer

Interactive California highway traffic and destination-service opportunity screening.

## Current analytical layers

- Caltrans 2024 total AADT points and derived highway segments
- Caltrans 2024 truck counts and truck share
- Official Caltrans Safety Roadside Rest Areas
- ACS 2024 tract income and population-density context
- Census 2020 Urban Areas for urban suitability screening
- OpenStreetMap fuel, travel centres, highway services, EV charging and food clusters
- OpenStreetMap interchange and tourism / overnight-stay context
- Corridor opportunity score

## Opportunity model v0.3

The score is a transparent screening tool, not a site recommendation.

| Component | Weight |
| --- | ---: |
| Addressable long-distance traveller demand | 25% |
| Service gap | 20% |
| Competition quality / intensity | 15% |
| Total AADT | 15% |
| Interchange accessibility | 10% |
| Tourism / overnight-stay context | 5% |
| Median household income | 5% |
| Truck share | 5% |

Raw population density is no longer a positive factor.

### Urban suitability gate

Dense major-urban cores are categorically excluded before opportunity ranking. The initial screening definition is:

- inside a 2020 Census Urban Area with population of at least 100,000; and
- local ACS tract population density of at least 2,500 people per square mile.

Major-urban fringe segments receive a 0.65 score multiplier. Smaller or lower-density urban areas receive a 0.85 multiplier. Rural and exurban segments are unpenalized.

### Addressable passing traffic

The model separates raw AADT from an explicit estimate of the share likely to represent long-distance / interregional movement. These route shares are modelling assumptions and are shown in the output so they can be recalibrated.

### Rest areas and competition

Official Caltrans Safety Roadside Rest Areas are a separate authoritative layer. They are treated as partial substitutes for basic stopping needs, rather than as direct commercial competitors. Commercial competition is quality-weighted so a destination-scale service area has substantially more impact than a basic fuel station, charger or small food cluster.

Important limitations: service, SRRA and interchange distances are currently straight-line screening proxies rather than routed highway travel distances. Traveller shares and tourism intensity are model assumptions, not observed trip-purpose data. Parcel availability, land value, exact access, visibility, entitlement, utilities and environmental constraints are not yet included.
