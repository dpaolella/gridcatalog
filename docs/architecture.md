# Architecture

The one hard constraint, from which everything else follows: **whether the Hub
is in the byte path depends on who published the thing.**

* **Registered with the Hub** — a model, an assumption set, a study filing, a
  reference network. The Hub holds the bytes. It has to: the point of
  registering is an immutable, versioned, citable release that is still there
  in five years, and a pointer at somebody's institutional file share is not
  that. Storage, versioning and archival are the Hub's problem.
* **Hosted elsewhere and already hosted well** — ERA5 at ECMWF, EIA-930 at the
  EIA, a Zenodo deposit. The Hub holds metadata and issues an access plan that
  points at where the bytes actually live. Re-hosting these would be
  duplicating infrastructure that already works, and it is explicitly out of
  scope.

The dividing line is not size, it is custody. A registered object is one the
Hub promises will not move; a catalogued one is a description of something
somebody else promises. Cost scales with what is registered, not with what is
catalogued — which is what keeps a 4 TB reanalysis archive free to describe.

```
                    ┌──────────────────────────────────┐
                    │  Semantic layer                  │
                    │  concept resolution · linkage ·  │
                    │  currency grading                │
                    └────────────────▲─────────────────┘
                                     │ enrich / write back
   humans ──────────► ┌──────────────────────────────────────────┐ ◄────── agents (MCP)
   (Next.js UI)       │  Catalog & metadata                      │         tools (SDK)
                      │  search · schema · provenance · license  │
   evaluators ──────► │                                          │
   (no auth)          └────────────────────┬─────────────────────┘
                                           │ capabilities + access tier
                                           ▼
   humans ──────────► ┌──────────────────────────────────────────┐ ◄────── agents / tools
   "download this"    │  Access broker                           │         "give me this slice"
                      │  access plans · auth · allow-lists       │
                      └────────────────────┬─────────────────────┘
                                           │ issues ACCESS PLAN (small JSON or 302)
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │  external storage                        │
                      │  partner-hosted · public · OpenGrid S3   │
                      └──────────────────────────────────────────┘
                                           │
                      bytes flow directly storage ──► client
                      (this hop deliberately bypasses the Hub)
```

Feeding the catalog from the left is the acquisition pipeline:

```
harvest ──► filter ──► normalize ──► LLM enrich ──► SHACL validate ──► review ──► publish
 (CKAN,     (relevance) (to JSON-LD   (fill gaps,     (block bad       (human      (level 1/2/3)
  STAC,                  DCAT-3)       draft field     records)         confirm)
  Zenodo,                              metadata)
  YAML,
  curated)
```

## Write to the graph, read from the index

The single most important structural decision (ADR-0001). The triple store is
the system of record. **It is not the search backend.**

```
                    writes                          reads
                      │                               │
                      ▼                               │
        ┌───────────────────────────┐                 │
        │  Jena Fuseki / TDB2       │  ── projector ──┼──► OpenSearch
        │  system of record         │   (on commit)   │    denormalized
        │  · all records as RDF     │                 │    search documents
        │  · SKOS concept schemes   │                 │         │
        │  · provenance graph       │                 │         ▼
        │  · SHACL shapes           │                 │   list view, facets,
        └───────────────┬───────────┘                 │   search-while-typing
                        │                             │
                        └── SPARQL ───────────────────┴──► detail view,
                            (graph queries only)           provenance chains,
                                                           links, concepts
```

Every read path goes to the store that is good at it. Faceted
search-while-typing over the whole catalog is a search-engine query and never
touches SPARQL. Provenance traversal, concept inference and link computation are
SPARQL and never touch the index.

This costs two stores to keep consistent, and the projector is the piece most
likely to be the source of a "why is search stale" bug. It is therefore
instrumented: projector lag is a metric, surfaced in the admin UI, with a stated
budget (`DATAHUB_PROJECTOR_LAG_BUDGET_S`, default 60 s).

Reindex-from-scratch is one command and is exercised routinely, because the
index is derived state and treating it as precious is how it drifts.

## Module boundaries

| Module | May contain SPARQL | Talks to |
|---|---|---|
| `services/graph` | yes — the only module that talks to Fuseki | the store |
| `services/projector` | yes — one CONSTRUCT | graph → search index |
| `services/semantic` | yes | graph |
| `services/linksvc` | yes | graph, semantic |
| `services/api` | **no** | graph (via records), search, Postgres |
| `services/harvest` | **no** | third-party sources, graph (via records) |
| `services/mcp` | **no** | the REST API only |
| `sdk/python` | **no** | the REST API only |
| `web` | **no** | the REST API only |

The containment is deliberate (PRD principle 9). A modeler who wants to
contribute will know Python and probably not SPARQL; they must be able to write
a harvester, an API route or an SDK method without learning it.

## Backends

Every infrastructure dependency has a production implementation and an
in-process one with the same semantics (ADR-0002), selected by configuration:

| Concern | Production | In-process |
|---|---|---|
| Graph | Fuseki / TDB2 | rdflib `Dataset` |
| Search | OpenSearch 2.x | BM25 inverted index |
| Relational | PostgreSQL 16 | SQLite |
| Queue | Celery + Redis | eager, synchronous |

The whole default test suite — conformance, the Q1–Q5 regression queries, the
entitlement matrix, broker shape-invariance, MCP grounding — runs with `pytest`
and no container runtime. A parity suite asserts the two sides agree.

## Data flow for the two paths that matter

**A search.** Browser → `/v1/datasets` → query builder injects the entitlement
clause → search backend → denormalised documents → response. No SPARQL, no
graph read. Sub-200 ms is a search-engine problem, and it is solved by a search
engine.

**An access plan.** Client → `POST /v1/datasets/{id}/access-plan` → entitlement
check at plan issuance (not only at discovery) → distribution selection from
metadata: range-request support plus a chunk index gives partial-read, a
declared subsetting protocol gives that protocol, otherwise a redirect →
plan returned carrying licence, attribution and quality grades → **client
fetches bytes directly from storage.** The Hub never sees them.

## What is deliberately absent

- No byte proxying, ever.
- No query-time data transformation. That is the SDK and compute.
- No composite quality score (ADR-0007).
- No workflow execution. Tiers 2 and 3 belong to Workflow Orchestration.
