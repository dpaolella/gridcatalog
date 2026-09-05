# `opengrid-datahub`

The Python SDK for the OpenGrid Data Hub.

```bash
pip install opengrid-datahub          # search the catalog
pip install "opengrid-datahub[all]"   # …and read what you find
```

## Zero to a dataset

```python
from opengrid import DataHub

hub = DataHub()
ds = hub.search(domain="DD5", region="DE", concept="solar_irradiance")[0]
da = ds.open(time=slice("2019-01", "2019-12"), bbox=[5.9, 45.8, 10.5, 47.8])
```

`ds.open()` asks the Hub for an access plan and then **reads the data here, in
your process**. The Hub is not in the path: it says where the data is and how to
read it, and this package does the reading. That is why slicing a 4 TB Zarr to
one month transfers a few megabytes rather than 4 TB — twice.

## What the catalog tells you that a URL does not

```python
ds = hub.get("ecmwf-era5")

ds.quality.currency, ds.quality.currency_label  # ('B', 'Aging')
ds.quality.provenance  # 'D' — untraced, not "bad"
ds.completeness_level  # 3

for field in ds.fields():
    if field.concept_gap_reason:
        print(field.local_name, "→ no concept:", field.concept_gap_reason)
```

Three quality facets, graded independently and **never combined into a
composite**. A dataset can be perfectly current and completely unprovenanced,
and averaging those destroys the only information you could act on.

`None` on a facet means *not assessed*, which is not a poor grade — a record
below completeness level 2 carries no field metadata to grade.

## Before you combine two datasets

```python
for link in ds.links():
    if not link.independent:
        print(link.correlation_warning)
```

> These two are not independent: both trace back to ECMWF ERA5 reanalysis —
> Global Wind Atlas is 2 hops away and PyPSA-Eur weather cutouts one hop.
> Agreement between them is partly that source agreeing with itself, so
> treating them as corroborating evidence understates uncertainty. Use them
> together for coverage, not for validation.

## Licence and attribution travel with the plan

```python
plan = ds.access_plan(time=slice("2019-01", "2019-02"))
plan.license, plan.attribution, plan.redistribution_allowed
```

In the plan rather than in a page nobody read. A script handed a URL cannot know
it may not redistribute what it downloads.

## Authentication

```python
hub = DataHub(token="og_pat_…")  # or set OPENGRID_TOKEN
```

Anonymous access works for public records — browsing is not gated. A token adds
whatever a custodian has granted you, and a stale token quietly falls back to
anonymous rather than failing: a script that only reads public data keeps
working.

## Absent means "not captured"

A field missing from a record is a gap in what has been catalogued, not a
statement about the dataset. `None` here never means "this dataset has no
licence" or "this dataset has no upstream" — it means nobody has recorded one.
