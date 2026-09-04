# ADR-0001: Triple store as the system of record

**Status:** Accepted · **Date:** 2026-09-04 · **Source:** PRD §0.3, §3.1, §3.2

## Context

The Notion schema spec left the storage substrate open: "a triple store or a
JSON-LD-aware document store with materialized views." Three capabilities the
product depends on are native to RDF and would otherwise be hand-built:
unbounded-depth provenance traversal over mixed edge types, inference over the
SKOS concept scheme, and federation against external SPARQL endpoints.

## Decision

Apache Jena Fuseki 5.x with TDB2 is the system of record. Catalog records are
RDF, authored and exchanged as JSON-LD.

**The triple store is not the search backend.** A projector flattens confirmed
records into denormalized search documents on commit. Every read path goes to
the store that is good at it: faceted search-while-typing is a search-engine
query and never touches SPARQL; provenance traversal, concept inference and
link computation are SPARQL and never touch the search index.

Named graphs:

| Graph | Contents |
|---|---|
| `og:graph/catalog` | Published, steward-confirmed dataset records |
| `og:graph/draft` | Harvested and enriched records pending review |
| `og:graph/vocab` | SKOS concept schemes, versioned |
| `og:graph/inferred` | Materialised entailments, regenerated on vocab change |
| `og:graph/computed` | Semantic-layer output: links, grades, resolutions |

## Consequences

Standing obligations, not one-time costs:

- Continuous access to SPARQL/SHACL/RDF-modeling fluency on the team.
- Fuseki operational competence: TDB2 backup, restore, compaction, upgrades.
  An uncompacted TDB2 store degrades quietly rather than failing loudly.
- Concept-scheme governance. A careless `skos:broader` edit changes what every
  existing query returns. Vocabulary changes go through code review with the
  conformance suite as the regression gate.
- SPARQL stays contained (see ADR-0003 and PRD principle 9) so a contributor
  who knows Python and not RDF can still write a harvester, an API route or an
  SDK method.

**Exit path, documented rather than denied:** records are JSON-LD, so they
export losslessly to a document store. What would be lost is inference and
federation, not the records.

## Alternatives considered

- **Document store with materialised views.** Cheaper to hire for; would require
  hand-built transitive closure, a hand-maintained concept expansion list, and a
  replication pipeline in place of federation.
- **Property graph (Neo4j).** Good at traversal, weak at the shared-vocabulary
  and federation story that motivates the choice.
