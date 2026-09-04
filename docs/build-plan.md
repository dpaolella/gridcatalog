# Data Hub 1.0 — Build Plan

The PRD (`README.md`, verbatim copy at `docs/prd.md`) is the contract. This
document is the decomposition of that contract into work packages that can be
scheduled, assigned and closed independently.

Every work package has: a scope boundary, the files it owns, a done-when that
is a test rather than an opinion, and its dependencies. **File ownership is
exclusive.** Two work packages never edit the same file; where they must meet,
they meet at a protocol defined in a foundation package.

Milestone numbering follows PRD §10. Work-package numbering is `WP-<milestone>.<n>`.

---

## Status board

| # | Work package | Milestone | Depends on | State |
|---|---|---|---|---|
| WP-0.1 | Repository, packaging, tooling, CI | M0 | — | todo |
| WP-0.2 | Backend protocols and settings | M0 | WP-0.1 | todo |
| WP-1.1 | JSON-LD context and namespace registry | M1 | WP-0.1 | todo |
| WP-1.2 | SKOS concept schemes (five, versioned) | M1 | WP-1.1 | todo |
| WP-1.3 | SHACL shapes, level-parameterised | M1 | WP-1.1, WP-1.2 | todo |
| WP-1.4 | Conformance fixtures and suite | M1 | WP-1.3 | todo |
| WP-1.5 | Graph client, named graphs, inference materialisation | M1 | WP-0.2, WP-1.2 | todo |
| WP-2.1 | Record model, JSON-LD ⇄ RDF round-trip | M2 | WP-1.1, WP-1.5 | todo |
| WP-2.2 | Distribution model, revision history, link health state | M2 | WP-2.1 | todo |
| WP-2.3 | Operational store: schema, migrations, repositories | M2 | WP-0.2 | todo |
| WP-2.4 | Projector: CONSTRUCT, indexer, one-command reindex | M2 | WP-2.1 | todo |
| WP-2.5 | Curated seed loader (`data/seed-sources.yaml` → catalog) | M2 | WP-2.1, WP-1.3 | todo |
| WP-3.1 | Harvest framework: runs, checkpoints, rate limiting | M3 | WP-2.3 | todo |
| WP-3.2 | Adapters: ckan, zenodo, datacite | M3 | WP-3.1 | todo |
| WP-3.3 | Adapters: stac, yaml_repo, dcat_sparql, oep, curated | M3 | WP-3.1 | todo |
| WP-3.4 | Grid-relevance filter (keyword + vocabulary + LLM stage) | M3 | WP-1.2 | todo |
| WP-3.5 | Normalizers and per-source YAML mappings | M3 | WP-3.2, WP-3.3, WP-2.1 | todo |
| WP-3.6 | LLM enricher with field allow-list and basis tagging | M3 | WP-3.5 | todo |
| WP-3.7 | Validation runner and review-queue model | M3 | WP-1.3, WP-2.3 | todo |
| WP-4.1 | Search backend protocol + in-memory + OpenSearch | M4 | WP-0.2 | todo |
| WP-4.2 | Query builder, facets, entitlement injection | M4 | WP-4.1 | todo |
| WP-4.3 | REST read endpoints and OpenAPI 3.1 | M4 | WP-4.2, WP-2.1 | todo |
| WP-4.4 | Concepts, domains, submissions, reports endpoints | M4 | WP-4.3 | todo |
| WP-5.1 | Access-plan model and path selection | M5 | WP-2.2 | todo |
| WP-5.2 | Byte-range and subsetting-protocol planning | M5 | WP-5.1 | todo |
| WP-5.3 | Link-health prober, auto-heal, sibling fallback | M5 | WP-2.2 | todo |
| WP-6.1 | Principals, tokens, OIDC and federated SSO | M6 | WP-2.3 | todo |
| WP-6.2 | Allow-lists, three visibility levels, custodian API | M6 | WP-6.1, WP-4.2 | todo |
| WP-6.3 | Rate limiting, audit log, entitlement matrix suite | M6 | WP-6.2 | todo |
| WP-7.1 | Concept and unit resolution across four data shapes | M7 | WP-1.2, WP-2.1 | todo |
| WP-7.2 | Q1–Q5 named queries and regression suite | M7 | WP-1.5 | todo |
| WP-7.3 | Currency grading + relational/scheduled trigger split | M7 | WP-7.1 | todo |
| WP-7.4 | Provenance and documentation grading | M7 | WP-7.1 | todo |
| WP-7.5 | Golden set: ~60 level-3 records across ten domains | M7 | WP-2.5 | todo |
| WP-8.1 | Pair signals and weighted strength from config | M8 | WP-7.1 | todo |
| WP-8.2 | Descriptors, typed relations, shared-origin warnings | M8 | WP-8.1, WP-7.2 | todo |
| WP-9.1 | Next.js app shell, design system, i18n scaffold | M9 | WP-4.3 | todo |
| WP-9.2 | List view: search-while-typing, facets, map, timeline | M9 | WP-9.1 | todo |
| WP-9.3 | Detail view: seven tabs | M9 | WP-9.1 | todo |
| WP-9.4 | Domains, concepts, submit, report, connect pages | M9 | WP-9.1 | todo |
| WP-9.5 | Steward review queue at `/admin/review` | M9 | WP-9.1, WP-3.7 | todo |
| WP-10.1 | Python SDK | M10 | WP-4.3, WP-5.1 | todo |
| WP-10.2 | MCP server, seven tools, tier gating, grounding tests | M10 | WP-10.1 | todo |
| WP-10.3 | Docs, OpenAPI publication, quickstart | M10 | all | todo |

---

## M0 — Foundations

Not a PRD milestone. It exists because the eight packages that follow it are
built in parallel and need their meeting points fixed first.

### WP-0.1 Repository, packaging, tooling, CI
Repository layout per PRD §9. `pyproject.toml` mapping `services/` to the
`datahub` import name (ADR-0003). Ruff, mypy, pytest configuration. GitHub
Actions running lint, type-check and the full suite. Apache-2.0 licence.
**Done when:** `pytest` collects and passes on a clean checkout with no
container runtime.

### WP-0.2 Backend protocols and settings
`GraphStore`, `SearchBackend`, `JobQueue` protocols with production and
in-process implementations (ADR-0002). `datahub.config.Settings` as the single
source of runtime configuration.
**Done when:** the parity suite asserts identical behaviour from both graph
backends and both search backends on the same fixture corpus.

---

## M1 — Schema, vocabulary and validation

PRD §4, §F7. **Carries the storage risk.** Before committing past M1 the golden
set is loaded and Q1–Q5 are run; a negative result reopens ADR-0001 (PRD §10).

- **WP-1.1** `schemas/opengrid-datahub.jsonld` — the context. Every term in
  PRD §4.1, §4.2, §4.3 plus the four fields this build adds.
- **WP-1.2** `vocab/` — `og-data-domain.ttl` (DD1–DD10 with structural notes),
  `og-provenance-class.ttl`, `og-analysis-type.ttl`, `og-access-restriction.ttl`,
  `og-grid-concept.ttl` (the physical-quantity scheme, bootstrapped from OEO and
  Sienna rather than authored fresh), plus crosswalks to CIM/CGMES, PyPSA,
  MATPOWER and Sienna using correct SKOS match strength per X2.
- **WP-1.3** `shapes/opengrid-datahub.ttl` — level-parameterised shapes
  including the four named in PRD §4.5 and the X3/X4 honesty constraints.
- **WP-1.4** `tests/fixtures/` valid and invalid records; `tests/conformance/`
  asserting each invalid fixture fails with the *expected* violation, not merely
  that it fails.
- **WP-1.5** `services/graph/` — client, named-graph helpers, entailment
  materialisation on vocabulary change.

**Milestone done when:** the conformance suite passes; an invalid record is
rejected with a message naming the failing triple; Q3 returns narrower concepts
with no enumeration anywhere in code.

---

## M2 — Catalog core and projector

PRD §F1, §3.1.

- **WP-2.1** Record CRUD, JSON-LD ⇄ RDF round-trip that is lossless under test.
- **WP-2.2** Distribution as a first-class object with its own access
  restriction, capabilities and link-health block; revision history per
  distribution URL (source, timestamp, old, new, automated/manual).
- **WP-2.3** PostgreSQL/SQLite operational tables: users, sessions, allow-lists,
  harvest runs, review queue, reports, submissions, probe history. Alembic.
- **WP-2.4** Projector: `construct.rq`, incremental index on commit, full
  reindex as one command, projector lag exposed as a metric.
- **WP-2.5** Curated seed loader: 114 seed datasets across ten domains from
  `data/seed-sources.yaml`, carrying `verified: false` through to
  `og:reviewState` so unreviewed rows cannot be mistaken for confirmed ones.

**Milestone done when:** every curated seed dataset is in `og:graph/catalog`,
validates, is retrievable by a Python call, appears in the index within the
stated lag budget, and survives a full reindex byte-identically.

---

## M3 — Harvest pipeline

PRD §7.

- **WP-3.1** Framework: idempotent re-harvest keyed on `sourceId`, run records
  with counts/errors/duration, polite rate limiting, resumable checkpoints,
  `python -m datahub.harvest --source <id> --limit <n>`.
- **WP-3.2 / WP-3.3** Eight adapters. Each independently runnable and tested
  against recorded fixtures so the suite does not depend on third-party uptime.
- **WP-3.4** Two-stage relevance filter. **Every rejection logged with its
  reason** so recall is auditable. Errs toward inclusion.
- **WP-3.5** Normalizers driven by `normalizers/mappings/*.yaml`, editable
  without touching code; records which fields came from source versus were left
  empty, which sets the initial completeness level.
- **WP-3.6** Enricher: closed field allow-list, structured output, one field
  group per call, `og:enrichmentBasis` on every drafted value (ADR-0005).
- **WP-3.7** pySHACL runner; failures go to `flagged`, never to the review queue.

**Milestone done when:** 2,000+ candidate records harvested, normalized and
validated, with a per-source recall audit.

---

## M4 — Search and API

PRD §F8, §F3.

- **WP-4.1 / WP-4.2** Index mapping, query builder, facets, sort, pagination,
  search-while-typing, mandatory entitlement clause (ADR-0006).
- **WP-4.3 / WP-4.4** The endpoint set in PRD §F8, OpenAPI 3.1 as the canonical
  contract everything else calls.

**Milestone done when:** a search across all ten domains returns correctly
faceted results within the latency budget at catalog scale.

---

## M5 — Access broker

PRD §F1 (10–14), §F8.

- **WP-5.1** Access plan of one uniform shape across five orders of magnitude of
  dataset size. Path selection is metadata-driven, never per-dataset code.
  License, attribution and quality grades travel in the payload.
- **WP-5.2** Byte-range plans from `og:chunkIndexMethod`; subsetting-protocol
  plans preferred over a full redirect where declared.
- **WP-5.3** Probing by HEAD or single-byte range, never a full download.
  Three consecutive failures ⇒ excluded from plans, live sibling substituted,
  custodian notified. A stable 3xx self-heals and writes revision history.

**Milestone done when:** a 4 TB Zarr and an 800 KB CSV return plans of identical
shape, and a slice request against the Zarr transfers only the slice.

---

## M6 — Auth and entitlement

PRD §F10. Runs in parallel from M4.

**Milestone done when:** a non-entitled user cannot detect the existence of an
allow-listed-existence dataset through any endpoint, including result counts and
pagination.

---

## M7 — Semantic layer and grading

PRD §F4, §F5.

- **WP-7.1** Resolution across all four data shapes: per-column (tabular),
  per-variable (hierarchical), per-layer (geospatial), per node/edge property
  (graph). Strategy: exact name+unit → `skos:altLabel` → embedding similarity
  above threshold → gap marker. Never guess past the threshold.
- **WP-7.2** `services/semantic/queries/` Q1–Q5, named and tested.
- **WP-7.3** The trigger split. Relational signals recompute on related-record
  change; self-contained signals (Currency) recompute on a schedule. **Getting
  this split wrong is the most likely correctness bug in the build** — a dataset
  does not become stale because someone edited it.
- **WP-7.4** Provenance and Documentation grading, per-variable for hierarchical
  datasets, geometry columns evaluated separately from attribute columns.
- **WP-7.5** The golden set. Curated during M3, frozen after.

**Milestone done when:** two differently-named fields for the same quantity
resolve to one concept IRI; a lapsed-cadence dataset re-grades on the next batch
with no write event; Q1 returns the shared ERA5 origin for the Global Wind Atlas
/ PyPSA-Eur cutout pair at correct depth.

---

## M8 — Inter-dataset links

PRD §F6. Weights in config, not code.

**Milestone done when:** a known correlated pair from the golden set surfaces
with a warning naming the shared upstream source and stating the modeling
consequence in plain language — and with its strength *reduced, not zeroed*.

---

## M9 — Web UI

PRD §F3. Seven detail tabs, three quality badges never combined, empty states
designed explicitly, link graph capped at twelve neighbours with "show more".

**Milestone done when:** a modeler goes from landing page to a correct access
plan for a DD5 dataset in under 60 seconds, and an unauthenticated evaluator can
read all three quality grades.

---

## M10 — MCP and SDK

PRD §F9.

Tier-gated tools stay **present in the interface for all callers** and return
403 per call; hiding them makes the agent hallucinate around the gap.

**Milestone done when:** an agent can search, inspect, explain a connection and
receive an access plan without ever receiving bulk data, and an out-of-tier tool
returns 403 rather than being absent.

---

## Sequencing

```
M0 ─► M1 ─► M2 ─┬─► M3 ─┬─► M7 ─► M8 ─┐
                │       │             ├─► M9 ─► M10
                └─► M4 ─┴─► M5 ───────┤
                        └─► M6 ───────┘
```

M1–M5 are the critical path. M6 runs in parallel from M4. M7 and M8 require
level-3 records, so the golden set is curated during M3.

## Carried-forward open questions

PRD §12 lists twelve. Four need TC or MC input and are **not** decided by
implementation default; they are tracked as issues labelled `needs-decision` and
the code paths they touch carry a `# PRD §12 Qn` marker so the eventual decision
has a single place to land. The eight engineering questions are decided in
flight and each resolution is recorded as an ADR.
