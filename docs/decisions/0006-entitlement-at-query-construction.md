# ADR-0006: Entitlement enforced at query construction

**Status:** Accepted · **Date:** 2026-09-04 · **Source:** PRD §F8, §F10, §11

## Context

Three visibility levels, of which the third is hard:

| Visibility | Non-entitled user sees | Entitled user sees |
|---|---|---|
| `public` | Everything except the bytes | Bytes too |
| `restricted-metadata` | Stub only | Full record and plan |
| `allowlisted-existence` | Nothing at all | Full record and plan |

"Nothing at all" includes result counts, facet counts, pagination totals,
aggregations, sitemaps, `Retry-After` timing differences and 404-vs-403
distinctions. Post-filtering a result set leaks existence through every one of
those channels.

## Decision

The entitlement predicate is compiled into the query, at both stores:

- **Search:** every query is built through `datahub.api.search.query.build()`,
  which takes a `Principal` and injects a mandatory visibility filter clause.
  There is no code path that constructs a search request without one.
- **Graph:** every catalog read goes through `datahub.graph.records` helpers
  that take a `Principal` and add a `FILTER` / `VALUES` restriction to the
  pattern. Facet counts are computed post-filter by the engine, so a restricted
  record contributes to no count.
- **Not-found semantics:** an `allowlisted-existence` record returns `404` to a
  non-entitled caller — the same response as a genuinely absent id — and the
  audit log records the distinction server-side.

Enforced by `tests/entitlement/` as a full matrix over
{anonymous, authenticated, allow-listed, custodian} × {three visibilities} ×
every endpoint, with explicit assertions on counts and pagination totals.

## Consequences

- Two chokepoints to review rather than every call site.
- A new endpoint that bypasses the builders fails the matrix test, which
  enumerates routes from the OpenAPI schema rather than a hand-written list.

## Alternatives considered

- **Filter after retrieval.** Simpler, and leaks existence through counts.
