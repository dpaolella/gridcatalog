# Quickstart

Three ways in, depending on who you are. All three call the same REST API, and
none of them reaches past it into the store — a rule enforced in one is enforced
in all three.

---

## A modeller: find a dataset and read it

```bash
pip install "opengrid-datahub[all]"
```

```python
from opengrid import DataHub

hub = DataHub()
ds = hub.search(domain="DD5", region="DE", concept="solar_irradiance")[0]
da = ds.open(time=slice("2019-01", "2019-12"), bbox=[5.9, 45.8, 10.5, 47.8])
```

`ds.open()` asks the Hub for an access plan and then **reads the data in your
process**, from wherever the plan says it lives. The Hub is not in the path.
That is why slicing a 4 TB Zarr to one month moves a few megabytes rather than
4 TB, twice.

Three things worth doing before you use what you found:

```python
ds.quality.currency, ds.quality.currency_label   # ('B', 'Aging')
ds.quality.provenance                            # 'D' — untraced, not "bad data"
ds.completeness_level                            # 3

for field in ds.fields():
    if field.concept_gap_reason:                 # what the catalog could not map, and why
        print(field.local_name, "→", field.concept_gap_reason)

for link in ds.links():
    if not link.independent:                     # a shared upstream you may not have known about
        print(link.correlation_warning)
```

`None` on a quality facet means *not assessed*, not a poor grade. A record below
completeness level 2 carries no field metadata to grade, and treating those the
same would condemn every harvested record for having been harvested.

Full SDK reference: [`sdk/python/README.md`](../sdk/python/README.md).

---

## An agent: connect over MCP

```bash
pip install "opengrid-datahub[mcp]"
DATAHUB_API_URL=https://api.opengrid.org DATAHUB_API_TOKEN=og_pat_… datahub-mcp
```

Seven tools. Six are open to every caller including anonymous, because PRD §F10
says browsing is not gated; `author_workflow` needs tier 1 and **is still
present**, returning a 403 that names the tier. A tool an agent cannot see is a
gap it invents its way around.

| Tool | Does |
|---|---|
| `search_datasets` | Catalog search, entitlement-scoped, payload-capped |
| `get_dataset` | The full record |
| `get_dataset_schema` | Fields, units, concepts — and the gaps, with reasons |
| `explain_connection` | Why two datasets are linked, including correlation warnings |
| `preview_dataset` | The dataset's shape. Not its data |
| `get_access_plan` | Where the data is and how to read it, under your identity |
| `author_workflow` | An inert specification. Nothing executes. **Tier 1** |

Every response is grounded in real catalog metadata. The server has no store
access and no way to compose a record, so it cannot fabricate a dataset — which
matters more than it sounds, because a plausible fabricated dataset is worse
than no answer, and a user cannot tell them apart.

Responses are capped at 100 KB and truncation is reported, never silent: an
agent handed a truncated list that looked complete will tell somebody there are
twelve such datasets when there are four hundred.

---

## An operator: run it locally

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/datahub db upgrade                 # operational schema
.venv/bin/datahub graph bootstrap            # vocabulary, shapes, entailments
.venv/bin/datahub seed load                  # the curated seed catalog
.venv/bin/datahub index reindex              # graph → search index
.venv/bin/datahub semantic run               # resolve concepts, grade quality
.venv/bin/datahub links run                  # compute inter-dataset links
.venv/bin/datahub serve                      # :8000, OpenAPI at /openapi.json
```

Every command takes `--json`, so stdout is parseable and diagnostics go to
stderr. `datahub semantic signals` prints which signals recompute on a write and
which on a schedule, and why — the distinction PRD §F4.3 calls the most likely
correctness bug in the build.

Nothing above needs a container runtime. The in-process backends (rdflib,
an in-memory BM25 index, SQLite, eager jobs) have the same semantics as the
production ones (Fuseki, OpenSearch, PostgreSQL, Celery) and are selected by
configuration — see [ADR-0002](decisions/0002-pluggable-backends.md) and
[`docs/operations.md`](operations.md).

---

## What the catalog will not do

- **Return data.** `/download` is a redirect and `/access-plan` is a document.
  A catalog that starts serving bytes becomes an egress bill and stops being a
  catalog.
- **Fill in a blank.** An absent field means *not captured*, never "this dataset
  has no licence" or "this dataset has no upstream". The distinction is the
  whole point of the completeness levels.
- **Combine the quality facets.** Three grades, independently, and never a
  composite ([ADR-0007](decisions/0007-no-composite-quality-score.md)). A
  dataset can be perfectly current and completely unprovenanced, and averaging
  those destroys the only information you could act on.
- **Tell you a record exists that you may not see.** A 404 for a restricted
  record is byte-identical to a 404 for one that never existed
  ([ADR-0006](decisions/0006-entitlement-at-query-construction.md)).
