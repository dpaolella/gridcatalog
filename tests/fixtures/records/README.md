# Record fixtures

Fifteen valid records drawn from real datasets in `data/seed-sources.yaml`.
Every fact here — licence, URL, DOI, byte size, coverage — comes from the seed
inventory or from the dataset's own documentation as described there. Where a
value is not known it is absent, and the completeness level says so. Nothing is
invented (PRD principle 4).

The corpus doubles as the start of the golden set (PRD §11), so it is expected
to grow toward ~60 records rather than be replaced.

| Fixture | Domain | Level | Tier | Exists for |
|---|---|---|---|---|
| `ecmwf-era5` | DD5 | 3 | 1 | The 4 TB partial-read path (M5); the shared upstream origin every ERA5-derived record traces to (Q1) |
| `pypsa-eur-weather-cutouts` | DD5 | 3 | 1 | Q1 pair, **depth 1** to ERA5 |
| `global-wind-atlas` | DD5 | 3 | 2 | Q1 pair, **depth 2** to ERA5 via an uncatalogued mesoscale run. Q1 selects both depths, so they must differ |
| `pypsa-eur-grid` | DD1 | 3 | 2 | OSM-derived network with modelled impedances; share-alike licence propagation |
| `global-transmission-database` | DD1 | 3 | 1 | The 800 KB CSV half of the broker shape-invariance test (M5); carries the `og:conceptGap` example (X4) |
| `nrel-nsrdb` | DD5 | 2 | 1 | Two distributions whose access restrictions differ — anonymous S3 and key-gated API |
| `esa-worldcover` | DD10 | 2 | 1 | Geospatial-primary: bbox, CRS and geometry types mandatory at level 1; COG partial read |
| `eia-930` | DD4 | 2 | 1 | Hourly operational data, US balancing authorities |
| `nrel-atb` | DD9, DD6 | 2 | 1 | One record with two domain facets, per PRD §4.1 D3 |
| `gem-global-integrated-power-tracker` | DD2 | 1 | 2 | Form-gated download failing the anonymous-access criterion; supersedes the WRI record |
| `wri-global-power-plant-database` | DD2 | 1 | 3 | `og:supersededBy` link — exercises the Currency grade end to end |
| `wecc-ferc-ceii` | DD1 | 1 | 3 | Reference-only, permanently restricted, catalogued as a documented gap |
| `lbnl-queued-up` | DD3 | 1 | 1 | The DD3 anchor |
| `ember-electricity-review` | DD8 | 1 | 1 | The DD8 anchor; policy data that is structured rather than PDF |
| `eia-natural-gas-prices` | DD7 | 1 | 1 | The DD7 anchor; historical open, forward commercial |

All ten data domains are represented.

## Before deleting one

Several suites depend on specific fixtures for specific reasons, listed above.
`tests/conformance/test_shapes.py` parametrises over the whole directory, so a
new fixture is picked up automatically — but removing one of the ERA5 chain
breaks the Q1 regression, and removing `global-transmission-database` breaks
both the broker shape-invariance test and the only `og:conceptGap` example.

## Adding one

1. Take every fact from `data/seed-sources.yaml` or from the source's own
   documentation. If a value is not stated there, leave it out.
2. Set `completenessLevel` honestly. The conformance suite validates each
   fixture at its declared level, and `highest_passing_level` will tell you what
   the record actually supports.
3. Reference the context by URL, as here; the loader substitutes it locally.
4. Run `pytest tests/conformance`.
