# Controlled vocabularies

Five SKOS concept schemes plus a unit registry and four external crosswalks.
Everything with a governed value space in the metadata schema resolves here;
nothing in the catalog carries a controlled value as free text.

| File | Scheme | Concepts | Used by |
|---|---|---|---|
| `og-data-domain.ttl` | Data domains DD1–DD10 | 10 | `og:dataDomain` (D3) |
| `og-provenance-class.ttl` | How values came to exist | 8 | `og:provenanceClass` (D6) |
| `og-access-restriction.ttl` | Why access is limited | 6 | `og:accessRestriction` (D9) |
| `og-analysis-type.ttl` | Kinds of study | 13 | `og:supportedAnalysis`, `og:excludedAnalysis` (D12) |
| `og-grid-concept.ttl` | Physical, economic and categorical quantities | 160 | `og:concept` (C4) |
| `og-units.ttl` | Unit registry with SI conversions | 42 | `og:unit` (C5) |
| `crosswalks/` | CIM/CGMES, PyPSA, MATPOWER, Sienna | — | Concept resolution, Q5 |

## Three properties that are queried, not just displayed

Vocabulary here carries product behaviour, so an edit to one of these changes
what the system does:

- **`og:structuralNote`** on each data domain holds that domain's honest
  statement of what is genuinely unavailable and why — DD1 on CEII, DD4 on
  behind-the-meter data, DD7 on forward curves. The domain page renders it, so
  it lives in the vocabulary rather than in the UI, and a test asserts each note
  is byte-identical to the one in `data/seed-sources.yaml`.
- **`og:baseProvenanceGrade`** on each provenance class encodes the ceiling a
  dataset of that class can reach on the Provenance facet. The F5 grading table
  is therefore data, not an if-chain.
- **`og:tierCeiling`** on each access restriction encodes the tier framework's
  consequence: Tier 1 requires anonymous free access, so `account-required` caps
  at Tier 2 and `ceii` at Tier 3.

## Governance

Versioning is SemVer per scheme, with `owl:versionInfo` on the scheme node.

- **Patch** — a typo, a new `skos:altLabel`, a clearer definition.
- **Minor** — a new concept, a new non-breaking mapping.
- **Major** — removing a concept, redefining one, or changing a `skos:broader`
  edge.

That last one deserves the weight. Inference means the hierarchy has
query-visible consequences: a careless `skos:broader` edit changes what every
existing query returns, silently and everywhere (ADR-0001). Vocabulary changes
go through the same review as code, with the conformance suite and
`tests/graph/test_inference.py` as the regression gate.

## Adding a concept

1. Put it in the right scheme. A quantity goes in `og-grid-concept.ttl`; a way
   of classifying a whole dataset goes in one of the small schemes.
2. Give it `skos:prefLabel`, `skos:definition` and a `skos:broader` parent.
   A definition that restates the label is not a definition — say what
   distinguishes it from its siblings and from the quantity it is most often
   confused with.
3. If it has a physical dimension, give it `og:defaultUnit` pointing at an entry
   in `og-units.ttl`. If the unit is not there, add it there first.
4. Add `skos:altLabel` for the column names real datasets use. These are the
   input to concept resolution, not documentation — a missing alt-label is a
   field that will not resolve.
5. Run `pytest tests/vocab`.

## Bootstrapping provenance

`og-grid-concept.ttl` was bootstrapped from the Open Energy Ontology and the
Sienna schema rather than authored fresh, per PRD §F7: a third parallel
vocabulary would be a net negative for the ecosystem. Concepts derived from
either carry a `skos:editorialNote` saying so, and `crosswalks/sienna.ttl`
records the correspondence explicitly.

## Known gap: QUDT reconciliation

`og-units.ttl` marks 29 unit IRIs `og:qudtStatus "unverified"` — believed
present in QUDT and used as such, but not checked against a pinned release,
because no QUDT endpoint was reachable when the registry was authored. The
remaining 13 are `og-minted`, permanently: QUDT does not define monetary
compounds like `$/kW-yr` or the US heat-rate unit `Btu/kWh`, and asserting a
QUDT IRI for them would be inventing one.

The local conversion factors are authoritative for the catalog either way, so
nothing is blocked on this. Reconciling the unverified entries against a pinned
QUDT release is a bounded task, tracked in `tests/vocab/test_units.py` as an
explicitly pending check rather than as a silently passing one.
