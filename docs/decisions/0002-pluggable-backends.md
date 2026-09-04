# ADR-0002: Pluggable graph, search, queue and relational backends

**Status:** Accepted · **Date:** 2026-09-04 · **Extends:** ADR-0001

## Context

ADR-0001 commits to Fuseki/TDB2 and the PRD stack commits to OpenSearch,
PostgreSQL and Celery+Redis. All four are correct production choices and all
four are containers. If every module imports a client for a running container,
three things follow that are bad for this project:

1. The test suite cannot run without infrastructure, so it will be run less.
2. A contributor cannot get to a working catalog on a laptop in one command,
   which raises the cost of exactly the contributions the project needs most.
3. The storage-risk experiment the PRD mandates before committing past M1
   ("load the golden set, run Q1–Q5") becomes an ops task instead of a test.

## Decision

Each infrastructure dependency is reached through a narrow protocol with two
implementations: the production backend, and an in-process backend with the
same semantics.

| Concern | Protocol | Production | In-process |
|---|---|---|---|
| Graph | `datahub.graph.store.GraphStore` | `FusekiStore` (HTTP SPARQL 1.1) | `RdflibStore` (rdflib `Dataset`, memory or on-disk) |
| Search | `datahub.api.search.backend.SearchBackend` | `OpenSearchBackend` | `InMemorySearchBackend` (inverted index + facets) |
| Relational | SQLAlchemy 2.0 | PostgreSQL 16 | SQLite |
| Queue | `datahub.queue.JobQueue` | Celery + Redis | `EagerQueue` (synchronous) |

Selection is by configuration (`DATAHUB_GRAPH_BACKEND`, `DATAHUB_SEARCH_BACKEND`,
…), never by import site. No module outside `datahub.graph` may construct a
SPARQL client, and no module outside `datahub.api.search` may construct a
search client.

Both graph backends speak SPARQL 1.1 — including property paths, named graphs
and `SERVICE` — so Q1, Q2, Q3 and Q5 execute identically against either. Q4
(federation) requires outbound network from the store and is marked
`@pytest.mark.network`.

## Consequences

- The full conformance suite, the Q1–Q5 regression suite, the entitlement
  matrix, the broker invariance tests and the grounding tests all run with
  `pytest` and no containers.
- A parity suite runs the same behavioural assertions against both backends,
  marked `integration` for the container-backed side. Divergence is a test
  failure, not a surprise in production.
- rdflib is materially slower than TDB2 and has no reasoner. Inference
  materialisation (`og:graph/inferred`) is therefore performed by an explicit
  SPARQL-based forward-chaining pass over the SKOS scheme rather than by
  relying on a store-side reasoner, which keeps behaviour identical on both
  backends. Jena's reasoner remains available in production as an optimisation,
  not as a correctness dependency.
- Performance numbers (the M4 sub-200 ms criterion) are only meaningful against
  the production backends. Benchmarks state which backend produced them.

## Alternatives considered

- **Testcontainers for everything.** Honest but slow, and unusable in
  environments without a container runtime — including this build environment,
  where the registry is unreachable.
- **Mocks.** A mock SPARQL store cannot execute a property path, so it cannot
  test the thing the storage decision exists for.
