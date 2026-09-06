# The harvested catalog

One JSON-LD file per record, written by `datahub record export` and read back
by `datahub record load`. **This directory is the catalog's system of record**
for everything that did not come from `../seed-sources.yaml`.

That is not an aesthetic choice. There is no server and no database: the site
is a static export that `.github/workflows/pages.yml` rebuilds from scratch in
a runner on every deploy, and the graph, the search index and the operational
store are created there and thrown away with the container. A harvester writing
to them is writing to something nobody keeps. Git is what persists, so git is
where records live (WP-11.8, [ADR-0012](../../docs/decisions/0012-auto-promotion.md)).

## Layout

```
data/catalog/<harvest source>/<slug>.jsonld
```

Partitioned by source so a diff is readable and a single source can be
re-harvested without touching the others.

## Reading a diff

The export is deterministic — sorted keys, two-space indent, trailing newline,
and a file is only rewritten when its bytes actually change. So a weekly
harvest of four thousand records where twelve changed produces a twelve-file
diff, not a four-thousand-file reserialisation. If you see a diff, something
really changed.

`og:reviewState` is the field to look at first:

| Value | Means |
|---|---|
| `confirmed` | a person checked the licence and the access path |
| `auto-confirmed` | the pipeline substantiated it and nobody has |

Both are published. They are never conflated, and the UI shows which is which.

## Do not hand-edit these files

A re-harvest rewrites them. Corrections belong upstream — in the source, in a
normaliser mapping, or as a steward confirmation that the pipeline preserves —
so that they survive the next run. The one exception is deleting a file to
force a record out of the catalog, and even that comes back unless the record
also stops being harvested.
