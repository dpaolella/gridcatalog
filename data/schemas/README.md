# Persisted schemas

One JSON-LD sidecar per record, written by `datahub schema export` and read
back by `datahub schema load`. Each file carries **`og:hasField` and nothing
else** — no licence, no access path, no title.

## Why a sidecar rather than a record

`datahub schema probe` reads a dataset's own description of its shape: a Zarr
store's consolidated metadata, a CSV header, a Frictionless datapackage. It
works — ERA5 publishes 273 variables with a long name and a unit on each — but
until [#45](https://github.com/dpaolella/gridcatalog/issues/45) it ran only
inside `pages.yml`, wrote into a graph the runner discards, and never reached
git. So the repository held **4** fields for ERA5 while the site showed **273**,
every deploy re-fetched them, and the published catalog could not be
reproduced from a checkout.

The obvious fix — export those records to `data/catalog` like harvested ones —
is the one thing that must not happen. Most probed records are `curated`: the
build regenerates them from `../seed-sources.yaml` and the golden set, and
`pages.yml` loads `data/catalog/*/` **last**, so a frozen whole-record copy
overrides the regenerated one and every upstream correction stops reaching the
site. That is [#18](https://github.com/dpaolella/gridcatalog/issues/18)'s
regression, it was live, and 91 files were deleted in `d0ef0a5` because of it.

A sidecar cannot cause that, because it does not carry the fields that would
be overridden. It only adds.

## Merging

`schema load` shares `merge_fields` with the live probe, so both merge by one
rule: **an existing field is kept exactly as it is, and a field the record does
not have is added beside it.** ERA5's hand-authored `ssrd` states its
accumulation basis, its QUDT unit and the caveat that reading it as W/m² 
overstates irradiance by 3600×. A sidecar that knows only its long name never
displaces that.

Idempotent, so a second load is a no-op, and additive, so a source that is
unreachable on the morning of a build costs the fields it would have *added*
and never the ones already committed.

## Layout

```
data/schemas/<slug>.jsonld
```

Flat, and keyed by dataset slug rather than by harvest source, because a
schema belongs to the dataset and not to whoever catalogued it. A record whose
slug is absent simply has no persisted schema yet.

## Do not hand-edit these files

A re-probe rewrites them. A field that needs a concept IRI, a unit or a caveat
belongs on the record — in the golden set or in a normaliser mapping — where
the merge rule above protects it permanently.
