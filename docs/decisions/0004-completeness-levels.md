# ADR-0004: Completeness level is a promotion gate, not a publication gate

**Status:** Accepted · **Date:** 2026-09-04 · **Source:** PRD §0.2, §6

## Context

The Notion spec treats full field-level metadata as non-optional for every
ingested dataset. At ten-domain scale that requirement is the thing that does
not happen, and the failure mode is not a missing feature — it is a catalog
that quietly stops growing.

## Decision

`og:completenessLevel` ∈ {1, 2, 3} is carried on every record and displayed on
every record.

| Level | Name | Has | Enables |
|---|---|---|---|
| 1 | Discoverable | Dataset-level metadata, ≥1 distribution, license, coverage | Search, filter, access plan, download |
| 2 | Interpretable | Field names, definitions, types, value basis | Schema tab, Provenance and Documentation grading |
| 3 | Linked | Concept IRIs, unit IRIs, field sources | Semantic resolution, inter-dataset links |

A record publishes at level 1. Levels 2 and 3 are promotions, gated by SHACL
shapes that are evaluated *at the target level*: `sh:severity` on a level-3
constraint does not block a level-1 record.

A record below level 2 shows Provenance and Documentation as **"not yet
assessed"**, never as grade D. Absence of assessment is not poor quality, and
conflating the two would systematically defame every harvested record.

## Consequences

- Harvested breadth and curated depth coexist in one catalog with the
  difference visible rather than hidden.
- Shapes are level-parameterised. `datahub.harvest.validate` takes a target
  level and returns violations scoped to it.
- The UI must design the level-1 empty states deliberately (PRD §F3): a schema
  tab that explains its own absence, not an empty table.

## Alternatives considered

- **Publish only complete records.** Preserves a uniform record quality and
  caps the catalog at what a steward can hand-author.
- **Publish everything, no level marker.** Maximises breadth and destroys the
  user's ability to tell a thin record from a complete one.
