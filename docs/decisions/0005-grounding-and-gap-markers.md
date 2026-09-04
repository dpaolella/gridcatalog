# ADR-0005: Grounding, gap markers and enrichment provenance

**Status:** Accepted · **Date:** 2026-09-04 · **Source:** PRD §4.4 (X3, X4), §7.4, §F9, principles 2 and 4

## Context

Two components generate text that looks like catalog metadata: the harvest
enricher and the MCP server. Both are LLM-mediated. A fabricated license or a
plausible non-existent dataset is worse than a missing field, because it is
indistinguishable from a real one at the point of use.

## Decision

**Three enforcement layers, none of which depends on model cooperation.**

1. **Field allow-list in the enricher.** `ENRICHABLE_FIELDS` is a closed set:
   summary, data domain, provenance class, supported/excluded analysis, coverage
   facets, field labels and definitions, candidate concept mappings. Everything
   else — identifiers, licenses, access URLs, byte sizes, provenance links — is
   copied from the source or left empty. Enforced by filtering the model's
   structured output against the allow-list before merge, not by prompting.

2. **Enrichment provenance on every drafted value.** Each enriched field carries
   `og:enrichmentBasis "inferred"` with model id and prompt version. This is
   crosswalk honesty rule X3 and is a SHACL constraint: a field whose value came
   from enrichment and carries no basis fails validation.

3. **Explicit gap markers.** A field with no confident concept mapping carries
   `og:conceptGap` with a stated reason. It is never silently omitted. This is
   X4, also a SHACL constraint at level 3. A missing field means "not captured",
   never "does not exist".

For the MCP server the equivalent control is a **grounding assertion in the test
suite**: every dataset id, field id and concept IRI appearing in any MCP tool
response must resolve in the catalog. Any that does not is a test failure.

## Consequences

- The enricher cannot improve coverage of the fields it is forbidden to touch,
  which is the point. Those fields stay empty and the completeness level says so.
- Prompt versions are content-addressed and recorded, so a bad prompt's output
  is identifiable and revocable in bulk.

## Alternatives considered

- **Prompt-level instruction only.** Fails the "no guardrail may depend on the
  agent's cooperation" principle for exactly the same reason it fails for MCP.
