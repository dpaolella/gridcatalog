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
| WP-0.1 | Repository, packaging, tooling, CI | M0 | — | done |
| WP-0.2 | Backend protocols and settings | M0 | WP-0.1 | done |
| WP-1.1 | JSON-LD context and namespace registry | M1 | WP-0.1 | done |
| WP-1.2 | SKOS concept schemes (five, versioned) | M1 | WP-1.1 | done |
| WP-1.3 | SHACL shapes, level-parameterised | M1 | WP-1.1, WP-1.2 | done |
| WP-1.4 | Conformance fixtures and suite | M1 | WP-1.3 | done |
| WP-1.5 | Graph client, named graphs, inference materialisation | M1 | WP-0.2, WP-1.2 | done |
| WP-2.1 | Record model, JSON-LD ⇄ RDF round-trip | M2 | WP-1.1, WP-1.5 | done |
| WP-2.2 | Distribution model, revision history, link health state | M2 | WP-2.1 | done |
| WP-2.3 | Operational store: schema, migrations, repositories | M2 | WP-0.2 | done |
| WP-2.4 | Projector: CONSTRUCT, indexer, one-command reindex | M2 | WP-2.1 | done |
| WP-2.5 | Curated seed loader (`data/seed-sources.yaml` → catalog) | M2 | WP-2.1, WP-1.3 | done |
| WP-2.6 | Operations CLI (`datahub`) | M2 | WP-2.3, WP-2.4, WP-2.5 | done |
| WP-3.1 | Harvest framework: runs, checkpoints, rate limiting | M3 | WP-2.3 | done |
| WP-3.2 | Adapters: ckan, zenodo, datacite | M3 | WP-3.1 | done |
| WP-3.3 | Adapters: stac, yaml_repo, dcat_sparql, oep, cds, curated | M3 | WP-3.1 | done |
| WP-3.4 | Grid-relevance filter (keyword + vocabulary + LLM stage) | M3 | WP-1.2 | done |
| WP-3.5 | Normalizers and per-source YAML mappings | M3 | WP-3.2, WP-3.3, WP-2.1 | done |
| WP-3.6 | LLM enricher with field allow-list and basis tagging | M3 | WP-3.5 | done |
| WP-3.7 | Validation runner and review-queue model | M3 | WP-1.3, WP-2.3 | done |
| WP-4.1 | Search backend protocol + in-memory + OpenSearch | M4 | WP-0.2 | done |
| WP-4.2 | Query builder, facets, entitlement injection | M4 | WP-4.1 | done |
| WP-4.3 | REST read endpoints and OpenAPI 3.1 | M4 | WP-4.2, WP-2.1 | done |
| WP-4.4 | Concepts, domains, submissions, reports endpoints | M4 | WP-4.3 | done |
| WP-5.1 | Access-plan model and path selection | M5 | WP-2.2 | done |
| WP-5.2 | Byte-range and subsetting-protocol planning | M5 | WP-5.1 | done |
| WP-5.3 | Link-health prober, auto-heal, sibling fallback | M5 | WP-2.2 | done |
| WP-6.1 | Principals, tokens, OIDC and federated SSO | M6 | WP-2.3 | done |
| WP-6.2 | Allow-lists, three visibility levels, custodian API | M6 | WP-6.1, WP-4.2 | done |
| WP-6.3 | Rate limiting, audit log, entitlement matrix suite | M6 | WP-6.2 | done |
| WP-7.1 | Concept and unit resolution across four data shapes | M7 | WP-1.2, WP-2.1 | done |
| WP-7.2 | Q1–Q5 named queries and regression suite | M7 | WP-1.5 | done |
| WP-7.3 | Currency grading + relational/scheduled trigger split | M7 | WP-7.1 | done |
| WP-7.4 | Provenance and documentation grading | M7 | WP-7.1 | done |
| WP-7.5 | Golden set: ~60 level-3 records across ten domains | M7 | WP-2.5 | done |
| WP-8.1 | Pair signals and weighted strength from config | M8 | WP-7.1 | done |
| WP-8.2 | Descriptors, typed relations, shared-origin warnings | M8 | WP-8.1, WP-7.2 | done |
| WP-9.1 | Next.js app shell, design system, i18n scaffold | M9 | WP-4.3 | done |
| WP-9.2 | List view: search-while-typing, facets, map, timeline | M9 | WP-9.1 | done |
| WP-9.3 | Detail view: seven tabs | M9 | WP-9.1 | done |
| WP-9.4 | Domains, concepts, submit, report, connect pages | M9 | WP-9.1 | done |
| WP-9.5 | Steward review queue at `/admin/review` | M9 | WP-9.1, WP-3.7 | done |
| WP-10.1 | Python SDK | M10 | WP-4.3, WP-5.1 | done |
| WP-10.2 | MCP server, seven tools, tier gating, grounding tests | M10 | WP-10.1 | done |
| WP-10.3 | Docs, OpenAPI publication, quickstart | M10 | all | done |

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
- **WP-2.6** The `datahub` CLI. Added to the plan during M2 rather than
  discovered later: every task in this milestone is one an operator has to run,
  and a task that lives only in a runbook is a task that gets done differently
  each time. Data on stdout, diagnostics on stderr, an exit code that means
  something — so the same commands work in CI.

**Milestone done when:** every curated seed dataset is in `og:graph/catalog`,
validates, is retrievable by a Python call, appears in the index within the
stated lag budget, and survives a full reindex byte-identically.

**Where M2 landed.** 113 records (114 rows; the EU ETS / EEA EUTL pair is one
dataset under two domains, per PRD §4.1 D3): 55 confirmed in the catalog graph,
58 draft. The 56/58 verified split in the seed file becomes 55/58 after that
merge. Every row validates at level 1. Two rules are enforced by a branch with
a test on both sides rather than by convention: an unverified row cannot reach
the catalog graph, and no record carries a licence the seed file did not state
— an unmappable licence string becomes a `LicenseRef` with the original text
preserved and `redistributionAllowed: false`, never a guess.

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

**Where M3 landed.** The pipeline is complete and tested end to end; the volume
criterion is **not met and cannot be met from this build environment**, because
outbound access to all eleven harvest sources is denied by policy. What that
changes, precisely:

- The eight adapters are tested against recorded fixtures in
  `tests/fixtures/harvest/`, **written from each API's published response schema
  rather than captured from a live service**. They test paging, cursors,
  checkpoints, deduplication and every derived field — everything on this side
  of the boundary. They cannot test whether a source still returns that shape.
- `tests/harvest/test_live_sources.py` closes that gap and is skipped by
  default (`-m network`). Four assertions per source: it answers, the fields the
  mapping reads are still present, a live record normalises, and we are not
  being rate-limited. Run these first when a harvest starts returning less than
  it did last week.
- The recall audit the criterion asks for is built and runnable now:
  `datahub harvest audit` reports accept/reject rates per stage and the most
  recent rejections with their reasons. Against real sources it is the first
  thing to read.

Two design decisions worth recording, both about what the pipeline refuses to
invent:

- **Data domain is inferred; provenance class is not.** A domain is a filing
  decision — wrong means a record is in the wrong drawer, a steward fixes it in
  seconds — so it is derived from term signatures and marked
  `og:inferredAssignment` with a basis. A provenance class caps the Provenance
  grade, so a wrong one is the catalog asserting something false about how the
  numbers came to exist. It is set only where the source's own words determine
  it, and otherwise left absent, which costs the record level 1 and sends it to
  a steward.
- **The enricher's allow-list is enforced after the model answers, not in the
  prompt** (ADR-0005). `tests/harvest/test_enrich.py` uses a model that
  deliberately returns licences, access URLs, byte sizes and identifiers, and
  asserts that none of them reaches a record.

---

## M4 — Search and API

PRD §F8, §F3.

- **WP-4.1 / WP-4.2** Index mapping, query builder, facets, sort, pagination,
  search-while-typing, mandatory entitlement clause (ADR-0006).
- **WP-4.3 / WP-4.4** The endpoint set in PRD §F8, OpenAPI 3.1 as the canonical
  contract everything else calls.

**Milestone done when:** a search across all ten domains returns correctly
faceted results within the latency budget at catalog scale.

**Where M4 landed.** Fourteen paths, OpenAPI 3.1, RFC 9457 problem details from
one handler. `datahub serve` runs it; `datahub openapi` emits the contract
without starting a server, so a client build or a CI contract check does not
need a running service.

Three things worth recording:

- **Entitlement is the index's job, not the handler's.** Every read — detail,
  schema, quality, distributions, download — goes through the search index with
  the caller's predicate compiled in, and only then reads the record. Handlers
  have no way to ask who the caller is, which is the point: a handler that
  could ask would eventually decide.
- **The corpus grew two records** (`caiso-nodal-lmp-restricted`,
  `utility-load-shapes-allowlisted`). Until M4 the fixtures contained no
  restricted-visibility record at all, so the three visibility levels of PRD
  §F8 were untested end to end — the most security-sensitive rule in the
  system, exercised by nothing. `restricted-metadata` now appears in search as
  a marked stub; `allowlisted-existence` returns the same 404 as an absent
  record, field for field.
- **The index carries no access URLs, deliberately.** A search response should
  not haul every URL in the catalog to a client that wanted ten titles, so
  `/distributions` and `/download` read the record once entitlement is
  established — the same split `/schema` uses.

Deferred with their milestones, and asserted absent so the gap stays
deliberate: `/access-plan` (M5), `/links` (M8), `/allowlists` (M6). The first
and third have since arrived with their milestones and moved out of the set;
`/links` is still asserted absent.

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

**Where M5 landed.** Both halves of the done-criterion hold. The identical-shape
half is asserted directly (`test_one_shape_whatever_the_size`); the
transfers-only-the-slice half is the client's to execute, which is the design —
the Hub is not in the read path, so the plan states the mode and the byte
mechanics and the reader does the transfer.

Three restraints are what make the prober safe to point at sources nobody
asked:

- **HEAD, or a single-byte range where HEAD is refused. Never a download.** A
  prober that fetched what it checked would move terabytes a week and be
  indistinguishable from abuse.
- **Three consecutive failures, not one.** A single failure is a hiccup, a
  certificate renewal, a deploy. Excluding on the first would make link health
  flap, and a signal that flaps is one nobody trusts.
- **Auto-heal only on a stable *permanent* redirect,** to the same target every
  time. A 302 is the source telling us not to remember it, and three 301s to
  three different hosts is a load balancer, not a move.

Two things the tests forced into the open:

- Healing was **half implemented**: the revision row was written and the record
  was not, so every subsequent probe re-healed and one move produced four
  revision rows. PRD §F1.12 says the redirect "auto-updates the stored URL *and*
  writes a revision-history entry"; doing only the second half also puts a
  false statement in the audit trail. A prober constructed without a record
  store now declines to heal rather than claiming it did.
- **An `s3://` URI is not an unreachable link.** Its health is not knowable over
  HTTP, and recording it as unreachable would exclude working data — including
  ERA5, the largest thing in the corpus — from every access plan.

---

## M6 — Auth and entitlement

PRD §F10. Runs in parallel from M4.

- **WP-6.1** Principals, personal access tokens and federated sign-in.
  Authorization Code with PKCE against GitHub, Google or Microsoft; a session
  cookie for the browser and a bearer token for the SDK and the MCP server.
  Only a keyed hash of a token is ever stored.
- **WP-6.2** The three visibility levels and the custodian API. `PUT` replaces
  the whole allow-list; there is no approval step, because **the dataset creator
  manages the list and OpenGrid never arbitrates its contents** (PRD §F8).
- **WP-6.3** Per-caller rate limits, the authorization audit log, and the
  entitlement matrix suite that asserts the milestone's done-criterion endpoint
  by endpoint.

**Milestone done when:** a non-entitled user cannot detect the existence of an
allow-listed-existence dataset through any endpoint, including result counts and
pagination.

**Where M6 landed.** The done-criterion is asserted rather than reasoned about:
`tests/api/test_entitlement_matrix.py` walks every endpoint that names a dataset
and checks that an allow-listed-existence record is invisible to a
non-entitled caller in each of them — the record read, the search results, the
**result count**, the facet counts, the pagination arithmetic, `/schema`,
`/quality`, `/distributions`, `/download` and `/access-plan`. The 404 for a
record that exists is byte-identical to the 404 for one that never did,
including the request-id-bearing problem document, because a distinguishable
refusal is an existence oracle.

Four decisions worth recording, each of which the implementation argued for:

- **A bad token is anonymous, not a 401.** A stale token in a script that reads
  public data keeps working, and a 401 would confirm to whoever presented it
  that the token was once real. The presentation is logged; the response says
  nothing.
- **Only a custodian may touch an allow-list — not a steward, not an admin.**
  An admin who could edit the list would be arbitrating its contents, which is
  precisely what PRD §F8 forbids. An admin can change *who the custodian is*;
  that is a different power and it leaves a different audit trail.
- **A `PUT` of the whole list, not a patch.** A diff-based API makes "who can
  see this" a question you answer by replaying a history. Replacing the list
  makes it a `GET`. Revocations are applied before grants, so a body that both
  grants and revokes the same principal resolves to revoked rather than to
  whichever the dict ordering happened to yield.
- **Rate limiting is a router dependency, not middleware.** Middleware runs
  before FastAPI resolves dependencies, so it sees no caller and charges every
  authenticated request to its IP address. That errs safe — the anonymous
  budget is the tightest — and is still wrong: PRD §F9 wants agent traffic on a
  *larger* budget, and an agent throttled to the anonymous rate cannot work.

Three things the tests forced into the open, all of the same shape — a security
control that appears to work and does not:

- **A grant that never reached the index is a grant that does not work.**
  Entitlement is compiled into the query (ADR-0006), which means allow-list
  membership lives on the search document as `entitled_principals`. The
  projector was being constructed without a session, so every document got an
  empty list — and every entitlement test would have passed, for the wrong
  reason, because "invisible to everyone" also satisfies "invisible to the
  non-entitled".
- **The audit log was empty for exactly the events it exists for.** A refusal is
  raised as an exception, an exception rolls the request's transaction back, and
  an audit row written on that session rolls back with the refusal it records.
  It now goes out on its own transaction — which then deadlocked against the
  request's own write lock, because resolving the caller touches the token's
  `last_used_at`. A token *was* used even if the request it authenticated is
  then refused, so that touch is now committed as soon as it is made and the
  request stops carrying a write transaction it does not need.
- **The session cookie was set and never read.** Sign-in completed, the cookie
  came back, the redirect landed — and every subsequent request resolved to
  anonymous, so a browser would have shown signed-in chrome over signed-out
  data. The cookie resolves to a caller now, through the same `live()` lookup
  that makes expiry and revocation conditions of the query rather than checks
  after it. Unlike the token path it writes nothing, deliberately: a session
  that recorded its own last use would put every browser `GET` in a write
  transaction.

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

**Where M7 landed.** All three clauses hold and each is asserted against real
records rather than a unit fixture: ERA5's `ssrd` and NSRDB's `GHI` reach the
same concept IRI (`test_the_first_done_criterion_holds_across_records`); EIA-930
goes A → B between two passes with the record untouched and only the clock moved
(`test_a_lapsed_dataset_regrades_with_no_write_event`); Q1 has returned both
depths of the ERA5 chain since M1.

The resolution ladder is PRD §F4's, in order — prefLabel, altLabel, similarity
above a threshold, gap marker — with four rules that decide what it *refuses* to
do, which is where the value is:

- **A unit from another quantity kind blocks the match.** A column called `ghi`
  holding megawatts is not irradiance. Agreeing on the name while disagreeing on
  the physics is how a plausible wrong answer gets made, and it is the one
  failure a reader will not catch. A *convertible* unit is not a mismatch: kW
  against a concept in MW resolves, with the factor recorded.
- **A near-tie is a gap, not a coin flip.** `capacity` is an altLabel of three
  concepts. A resolver that took the first would be right some of the time and
  confident always. The margin rule does more safety work than the threshold
  does, because the lexical scale is compressed.
- **Breaking a tie needs evidence the tied rung could not see.** Only a
  definition can separate concepts whose labels tied; re-scoring the name
  against the same labels produces an answer that looks reasoned and is not.
- **Fields resolve only to the grid-concept scheme.** The catalog holds five
  schemes and four of them describe *datasets*. Without this restriction a
  column called `D` resolves to the data domain `DD4` on an altLabel match —
  which it did, until the golden set caught it.

The similarity rung is a seam, not a model dependency: a protocol with a
deterministic offline default (token overlap, containment-weighted against
definitions so a forty-token definition does not out-rank a five-word one) and
an embedding implementation that takes any injected embed function. Each backend
declares **its own** threshold, because 0.5 is a strong token-overlap signal and
0.5 between two sentence embeddings is nearly noise; one global threshold would
make the resolver either reckless or inert depending on which was configured.

Two things the tests forced into the open:

- **A rationale that embeds a clock-derived number defeats `lastComputedAt`.**
  The Currency rationale said "and now 32 days past". That text changes every
  day without the dataset changing, so every pass wrote a new timestamp and the
  freshness lag the field exists to expose meant nothing. The due date is in the
  evidence; a reader can subtract.
- **"None of the fields carries a definition" was not what the grader had
  found.** Documentation D fired whenever no field passed the *whole* checklist,
  and reported it as a missing definition. A record whose fields are all defined
  and none of which states a unit is partially documented — B — and grading it D
  says something untrue about work its authors did.

The golden set (WP-7.5) is the fixture corpus plus a frozen expectations file,
and it is **17 records against a ~60 target**. That gap is stated rather than
closed, because every fact in every record comes from the seed inventory or the
dataset's own documentation, and inventing 43 records to hit a number would
produce a regression suite that regression-tests fiction. What the file adds is
the part that makes the corpus a regression set: what each field must resolve to,
by which rung, and what each facet must grade, pinned to a fixed `as_of` so a
Currency expectation does not fail the build on a Tuesday.

Two findings it recorded rather than smoothed over, both flagged for the
curation pass: EIA-930's `NG` resolves to a concept labelled "Electricity
consumption" because the vocabulary lists `net_generation` among its altLabels —
defensible as a quantity, confusing as a label, and a vocabulary decision rather
than a resolver defect. And `influx_direct` in the PyPSA-Eur cutouts is a miss:
it belongs on direct normal irradiance, no altLabel covers the name, and the
field carries no definition for the similarity rung to read.

---

## M8 — Inter-dataset links

PRD §F6. Weights in config, not code.

- **WP-8.1** Six signals and one penalty, combined by weights read from
  `config/link-weights.yaml`, mapped onto a 5-point scale with a deterministic
  tie-break.
- **WP-8.2** A complementarity descriptor, a typed relation, the joinable keys
  and shared workflow tags, and the shared-origin warning.

**Milestone done when:** a known correlated pair from the golden set surfaces
with a warning naming the shared upstream source and stating the modeling
consequence in plain language — and with its strength *reduced, not zeroed*.

**Where M8 landed.** The done-criterion is
`test_the_known_correlated_pair_surfaces_with_a_warning`: Global Wind Atlas and
the PyPSA-Eur weather cutouts surface as a pairing, the warning names ECMWF ERA5
and says what it means for a study, and the score goes 0.287 → 0.137 — floored
at tier 1 and still in the list. Reduced, not zeroed, not hidden.

The warning is the piece worth reading twice, because the easy version of it is
useless:

> These two are not independent: both trace back to ECMWF ERA5 reanalysis —
> Global Wind Atlas is 2 hops away and PyPSA-Eur weather cutouts one hop.
> Agreement between them is partly that source agreeing with itself, so
> treating them as corroborating evidence understates uncertainty. Use them
> together for coverage, not for validation.

"Correlated" is a word a modeller reads past. Being told that the agreement
they are about to treat as corroboration is partly one dataset agreeing with
itself is not. The depth is in the sentence because it is the difference
between a warning that matters and one that does not, and a warning that fired
identically at one hop and at six would be ignored within a week — leaving the
place where a real one would go already occupied.

Four decisions:

- **Descriptors are built from evidence, never from the score.** Nothing in
  `describe.py` reads the strength number. A descriptor that varied with the
  ranking would be describing the ranking, and a user who noticed would stop
  reading them.
- **A pairing with nothing to say is not surfaced.** PRD §F6 says a bare
  numeric score *should fail review*; it fails a test instead. The one
  exception is a correlated pair, which is always kept — the warning is the
  reason, and it is the pairing a user most needs.
- **Candidates come from the index, not a cross join.** Three cheap queries —
  shared concept, shared domain, shared supported analysis — plus lineage
  neighbours. The last matters on its own: two datasets from the same origin
  may have no concept in common (a wind atlas and a weather cutout describe
  different quantities), and generating candidates by similarity alone would
  systematically miss exactly the pairs the warning exists for.
- **`/links` computes at request time with the caller's entitlement compiled
  into candidate generation.** Reading a stored list and filtering it would be
  the post-filter ADR-0006 forbids, and would leak an allow-listed record's
  existence through a suggestion that then vanished. The batch pass, by
  contrast, runs with full visibility: a restricted record that got no links
  because the batch could not see it would have none to show its own custodian
  either, which is the same leak pointing the other way.

Two things the tests forced into the open:

- **`from datahub.linksvc import describe` binds a function or a module
  depending on import order.** The package re-exports a function named
  `describe` and contains a module named `describe`, and the service got the
  function. An `AttributeError` on the first call, which is the good version of
  this bug; the bad version is a package where both are callable.
- **Deduplicating candidates by slug while testing IRIs never matches.** The
  correlated pair appeared twice in its own list — once as a similarity
  candidate, once as a lineage neighbour.

The quality contribution to link strength needs its own note against ADR-0007.
It is a number derived from three grades, which is what the ADR forbids — but
it describes a *pairing's usefulness as a suggestion*, never a dataset. It is
computed inside the ranker, consumed there, and discarded: not written to a
record, not projected into the index, not returned by the API, and asserted
absent from all three.

---

## M9 — Web UI

PRD §F3. Seven detail tabs, three quality badges never combined, empty states
designed explicitly, link graph capped at twelve neighbours with "show more".

**Milestone done when:** a modeler goes from landing page to a correct access
plan for a DD5 dataset in under 60 seconds, and an unauthenticated evaluator can
read all three quality grades.

**Where M9 landed.** Both clauses are Playwright tests against a real stack —
the FastAPI app on one port, the built Next app on another, the fixture corpus
behind both. The first lands on an access path in about three seconds; the
second reads all three grades with no session at all, and asserts there is no
composite anywhere on the page.

**Mocking the API here would have missed the only bugs this suite found.** Both
were shape mismatches between the UI's types and what the API actually returns:
`link_health` is an object where the type said string, and a facet bucket's
value is a number for completeness level and a boolean for anonymous access
where the type said string — which surfaced as `a.split is not a function` on
the landing page. A mocked API would have been mocked to match the types.

Four decisions:

- **The landing page is the search.** A landing page that is not already a
  search makes the first thing a modeller does a click, and this milestone is
  measured in seconds.
- **Search state lives in the URL**, debounced and `replace`d rather than
  pushed. A search a user can send to a colleague is worth more than one that is
  fractionally faster, and eight keystrokes should leave one history entry
  rather than eight.
- **All seven tabs render and hide with CSS** rather than mounting on click, so
  find-in-page reaches content the user has not clicked to. A tab that must be
  opened before Ctrl-F can see it is a tab whose content is effectively missing.
- **No map library.** A world graticule as inline SVG is a few hundred bytes and
  works offline; a tile map is a third-party request on every catalog row and a
  dependency on somebody else's uptime, for a picture that only has to say
  "roughly here".

The empty states are the part worth reading. Each says what happened rather than
what is missing: an empty schema tab names the completeness level and what
arrives at level 2; an unmapped field carries the stated reason it could not be
mapped; and a restricted record and an absent one produce the *same page* and
the same 404, with copy that says so plainly — a reader who understands why
cannot be misled, and one who does not is not misled either.

Two things this milestone pushed back into the API:

- **`/quality` now carries each grade's rationale**, read from the computed
  graph. The UI had a "why this grade" affordance and the field was always
  null, which is PRD §F5's *every grade derives from recorded facts* being true
  and unverifiable at the same time.
- **`/domains` now returns the concept IRI**, not only the `DD5`-style id. A
  client that has to synthesise an IRI has hardcoded a namespace, and it will be
  wrong the first time one changes.

Localisation is architectural readiness, not a second locale: every string is in
`src/messages/en.json` and every date, number, byte size and cadence goes
through `src/lib/format.ts`. Cheap now, and a search-and-destroy through every
component later.

---

## M10 — MCP and SDK

PRD §F9.

Tier-gated tools stay **present in the interface for all callers** and return
403 per call; hiding them makes the agent hallucinate around the gap.

- **WP-10.1** `sdk/python` — search and filter returning native objects,
  access-plan retrieval and execution, lazy xarray and pandas readers, auth.
- **WP-10.2** The fastmcp server: seven tools, tier gating, grounding tests.
- **WP-10.3** `docs/api.md` generated from OpenAPI, and a quickstart.

**Milestone done when:** an agent can search, inspect, explain a connection and
receive an access plan without ever receiving bulk data, and an out-of-tier tool
returns 403 rather than being absent.

**Where M10 landed.** Both halves are asserted:
`test_an_agent_can_go_from_search_to_access_plan` walks search → record →
schema → plan and checks the plan says *where* the data is rather than
containing any, and `test_an_out_of_tier_tool_returns_403_rather_than_being_absent`
checks `author_workflow` is in the tool table for every caller and refuses per
call, naming the tier it needs.

**Grounding is structural, not a prompt** ([ADR-0010](decisions/0010-grounding-by-boundary.md)).
PRD §F9 calls it the single most important correctness property, and the
mechanism is the architecture boundary:
`services/mcp` may not contain SPARQL and talks to the REST API only. A server
that could read the store could also compose, summarise and infer, and each of
those is a place where something plausible and untrue gets produced. A server
that can only forward what the API returned cannot fabricate a dataset, because
it has no way to make one. `test_no_tool_invents_a_dataset` checks every id in
every response against the catalog itself.

The same reasoning shows up in three smaller places:

- **An unlinked pair gets "the catalog records no connection", not a sentence.**
  A plausible paragraph about two datasets that are not linked is the
  fabrication failure wearing its most convincing hat.
- **A workflow's datasets are fetched, not assumed.** A specification naming a
  dataset that does not exist would be handed to a user as a plan.
- **A field with no concept carries its stated reason.** An agent told "no
  concept covers a compiler's self-assessed confidence class" does not invent
  one.

**Truncation is reported.** The 100 KB cap trims the longest list in a payload —
not the envelope, which is where the total count lives — and says the response
is incomplete in words aimed at a model: *do not report a count or a conclusion
from it.* An agent handed a truncated list that looked complete will say there
are twelve such datasets when there are four hundred, and will be confident.

**The SDK holds no second copy of any rule.** Not the entitlement predicate, not
the quality tables, not the link weights — a second copy would eventually
disagree with the first, and the one that disagreed would be the one a user was
standing behind when they published a figure. Two tests enforce it: one greps
the package for entitlement vocabulary, one for SPARQL and store imports.

`ds.open()` executes the plan in the caller's process. The base install is a
search client with one dependency; a reader that is missing names the package
to install rather than raising an ImportError three frames down, because
"unusable" with no remedy is a dead end and the remedy is one `pip install`.

Three things the tests forced into the open:

- **A synchronous httpx client cannot drive an ASGI app through a transport.**
  The app is async and the transport hands back an async stream. Both clients
  now accept a ready-made `httpx.Client` — which is also where a real caller
  puts retries, a proxy or a custom auth flow.
- **`/distributions` returns a bare list and both clients assumed an
  envelope.** An `AttributeError` deep inside a tool an agent was mid-call on.
- **The API ignores query parameters it does not recognise**, as HTTP APIs do.
  Right for HTTP and wrong for a client library: a mistyped filter silently
  widens a search and the caller trusts the results. The SDK validates filter
  names against the API's own OpenAPI document, so the check cannot drift from
  what the API takes.

---

## Where V1 stands

All forty-five work packages are done, and every milestone's done-criterion is
an assertion in the suite rather than a claim in this document. The two worth
naming, because they are the ones a reader will want to check:

- **No composite quality score exists anywhere.** Asserted in the search
  document's field list, in the API's response models, in the SDK's types and
  in the browser (ADR-0007).
- **A record a caller may not see is indistinguishable from one that does not
  exist** — through the record read, the result count, the facet counts, the
  pagination arithmetic, every detail endpoint, the MCP tools, the SDK and the
  UI, which returns the same page and the same 404 for both.

Three things are deliberately short of the PRD's V1 target, and each is stated
where it matters rather than left to be discovered:

- **The golden set is 17 records against ~60.** Every fact in every record
  comes from the seed inventory or the dataset's own documentation; inventing
  the remaining forty-three would produce a regression suite that
  regression-tests fiction. `data/golden-set/README.md` says so and says how to
  grow it.
- **The catalog itself is the seed inventory, not a harvested catalog.** The
  harvest adapters are complete and tested against recorded fixtures; running
  them against live sources needs outbound network this build environment does
  not have.
- **Two vocabulary findings are recorded rather than fixed** — EIA-930's `NG`
  resolving to a concept labelled "Electricity consumption", and
  `influx_direct` missing direct normal irradiance. Both are vocabulary
  decisions, not code defects, and both are frozen in the golden set so the
  curation pass has to look at them.

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
