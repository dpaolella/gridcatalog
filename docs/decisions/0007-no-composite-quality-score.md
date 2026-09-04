# ADR-0007: Three independent quality facets, never a composite

**Status:** Accepted · **Date:** 2026-09-04 · **Source:** PRD §F3, §F5, principle 3

## Context

Every catalog product eventually feels pressure to reduce quality to one number,
because one number sorts. The three facets here measure genuinely different
things: whether values are traceable, whether fields are documented, and whether
the vintage is current. A dataset can be A/D/A. Averaging that produces a "B"
that describes nothing real.

## Decision

`provenance`, `documentation` and `currency` are computed, stored, transported
and displayed independently. There is no composite field in the record, in the
API response, in the search document, or in the access plan.

Ordering by quality is not offered. Filtering by a facet threshold is.

The one place a quality signal is combined with others is the link-strength
formula (PRD §F6), where `quality_contribution` is a 0.10-weighted input to a
*relatedness* score — not a quality score — and where the combination is
explicitly explained to the user in the pairing's reasons.

Enforced by a schema test: no response model may contain a field matching
`/(overall|composite|total|combined).*(score|grade|quality)/i`, and the search
document mapping is asserted against an explicit allow-list of fields.

## Consequences

- A leaderboard of "best datasets" cannot be built from the API, deliberately.
- Grades are only ever absolute (derived from recorded facts about the record),
  never relative to other catalog entries.
