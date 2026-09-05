# Ingestion spikes

Two throwaway scripts that produce the numbers in
[`../ingestion-plan.md`](../ingestion-plan.md). They are **evidence, not
production code**: nothing under `services/` imports them, they are outside
`mypy`'s and CI's paths, and they should be deleted once WP-11.1 and WP-11.4
land and the same numbers come out of `make ingestion-report`.

Both are written to be re-run rather than trusted. Every number in the plan is
one command away from being checked, and a plan whose numbers cannot be
re-derived six months from now is a plan nobody can argue with.

## `schema_probe.py`

Reads a dataset's own schema surface — Zarr consolidated metadata, a CSV header
via a range request, a Frictionless `datapackage.json`, a STAC collection's
`cube:variables` — and reports how many `og:Field` nodes it yields, how many
carry a unit, and how many bytes it cost.

```
$ python docs/ingestion/spikes/schema_probe.py --targets docs/ingestion/spikes/targets.json
ECMWF ERA5 (ARCO)          zarr-v2-consolidated   273 fields  273 with units  273 with labels   129.7 KB  ok
ERA5 single-level (ARCO)   zarr-v2-consolidated    44 fields   44 with units   44 with labels   584.4 KB  ok
WRI Global Power Plant DB  csv-header              36 fields    0 with units    0 with labels    64.0 KB  ok
```

The catalog publishes 4 fields for ERA5 and 0 for the WRI database.

Needs outbound HTTPS to `storage.googleapis.com` and
`raw.githubusercontent.com`. Add targets by editing `targets.json`; the
`surface` key selects the extractor.

## `replay_normalize.py`

Replays the payloads a harvest run stored in `raw_records` back through the
same normaliser and the same SHACL validator, three times: as the pipeline
stands, with two mechanical repairs applied to the payload, and with the two
remaining blocking fields filled to measure the ceiling.

```
$ python docs/ingestion/spikes/replay_normalize.py --database var/site/ops.sqlite3

as built:                                 0 queued, 1199 flagged
   1199  og:updateCadence
    998  og:provenanceClass
    724  og:dataDomain
     11  dcat:distribution
      1  dct:license

with repairs:                           172 queued, 1027 flagged
    998  og:provenanceClass
    724  og:dataDomain
     11  dcat:distribution

ceiling: repairs + both fields filled:  1188 queued,   11 flagged
     11  dcat:distribution
```

Requires a prior harvest run to have populated `raw_records`:

```bash
git clone --depth 1 https://github.com/awslabs/open-data-registry var/harvest/aws_open_data
python -m datahub.harvest --source aws_open_data
```

Offline once that has run. Takes about fifteen minutes for three arms over
1,199 records, which is itself a finding: SHACL validation is the pipeline's
throughput floor (WP-11.7).
