# ADR-0009: Refusals are audited outside the request transaction

**Status:** Accepted · **Date:** 2026-09-04 · **Extends:** ADR-0006

## Context

PRD §F10 requires every authorization decision to be recorded, and the decisions
that matter most are the refusals. A refused request is the one a security
review reconstructs, the one a custodian asks about, and the only evidence that
an enforcement rule fired at all.

In a FastAPI service the natural place to write that row is the request's
database session — the same session the handler is already using. It is also
wrong, in a way that leaves no trace of being wrong:

1. A refusal is raised as an exception.
2. The session dependency rolls back on an exception, because a half-written
   request must not be committed.
3. The audit row was written on that session, so it rolls back with the refusal
   it records.

The result is an audit log that is complete for every allowed request and empty
for every refused one — the exact inverse of what it is for. Nothing fails, no
error is logged, and the table looks healthy.

The obvious fix — write the row on a second connection — introduces the second
half of the problem. Resolving the caller touches the presented token's
`last_used_at`, so by the time a handler refuses, the request is holding a write
transaction. A second connection writing an audit row then waits on the request
that is refusing it: on SQLite that surfaces as `database is locked` after the
busy timeout, and on PostgreSQL as a stall until the statement timeout. The row
is still not written, and now the refusal is slow as well.

## Decision

**Two rules, and they only work together.**

1. **An audit row for a refusal is written in its own transaction**, through
   `repositories.audit_out_of_band`, which opens a session, writes, commits and
   never raises. A refusal that failed to log is still a refusal; turning an
   authorization decision into a 500 because the audit table is unreachable
   would be the worse failure.

2. **A request does not carry a write transaction it does not need.** The
   caller-resolution dependency commits the `last_used_at` touch as soon as it
   makes it, rather than leaving it open for the life of the request. This is
   correct on its own terms — a token *was* used even if the request it
   authenticated is then refused, so the fact does not belong to the request's
   outcome — and it is what makes rule 1 work.

The browser session path follows from the same reasoning and writes nothing at
all: a session row that recorded its own last use would put every browser `GET`
back into a write transaction and undo rule 2 for the majority of traffic.

## Consequences

- Refusals appear in `authorization_events` with the principal, the resource,
  the action and the reason, and they survive the rollback of the request they
  describe. `tests/api/test_allowlists.py::test_a_refusal_is_audited_too`
  asserts it.
- Handlers must call `audit_out_of_band` — not `Repositories(session).audit` —
  on any path that is about to raise. The distinction is easy to get wrong and
  impossible to notice, so it is stated in the docstring of both.
- Two writes where there was one, on the refusal path only. A refusal is rare
  and already the slowest interesting path; the cost is not worth optimising.
- SQLite deployments run in WAL mode (`models/base._configure_sqlite`), so a
  reader does not block the out-of-band writer. WAL is the right default for a
  single-node deployment anyway; here it is also load-bearing.

## Alternatives considered

- **Buffer the row and write it after the request completes.** Correct in
  principle, and the mechanism is a `ContextVar` or `request.state`. FastAPI
  runs synchronous dependencies in a thread pool with a *copied* context, so a
  `ContextVar` set in one dependency is not visible to the handler; and
  threading `request.state` down to every guard means every function that can
  refuse takes a `Request`. Both trade a clear rule for a subtle one.
- **Commit the audit row on the request session before raising.** This commits
  whatever else the handler had already written, which on a refusal path is
  precisely the partial work the rollback exists to discard.
- **Log refusals to the structured log only.** The structured log is not
  queryable per principal or per dataset, is rotated on a different schedule,
  and is not what PRD §F10 asks for.
