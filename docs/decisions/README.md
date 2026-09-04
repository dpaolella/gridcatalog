# Architecture Decision Records

One file per decision, numbered, immutable once accepted. A decision that is
later reversed gets a new ADR that supersedes the old one; the old one is
marked `Superseded by ADR-nnnn` and left in place.

Format: Context → Decision → Consequences → Alternatives considered.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-triple-store-as-system-of-record.md) | Triple store as the system of record | Accepted |
| [0002](0002-pluggable-backends.md) | Pluggable graph, search, queue and relational backends | Accepted |
| [0003](0003-python-package-layout.md) | `services/` is the `datahub` Python package | Accepted |
| [0004](0004-completeness-levels.md) | Completeness level is a promotion gate, not a publication gate | Accepted |
| [0005](0005-grounding-and-gap-markers.md) | Grounding, gap markers and enrichment provenance | Accepted |
| [0006](0006-entitlement-at-query-construction.md) | Entitlement enforced at query construction | Accepted |
| [0007](0007-no-composite-quality-score.md) | Three independent quality facets, never a composite | Accepted |
| [0008](0008-no-blank-nodes-in-the-store.md) | No blank nodes in the store | Accepted |
| [0009](0009-audit-outside-the-request-transaction.md) | Refusals are audited outside the request transaction | Accepted |
