# Crosswalks

Mappings from `og-grid-concept` to the four external schemes a grid modeler is
most likely to already be working in.

## The four rules these files exist to obey (PRD §4.4)

**X1 — authored once, shared, versioned.** A mapping lives here, never on a
dataset record. A record says "this column means *nominal voltage*"; it never
says "this column is also CIM `BaseVoltage.nominalVoltage`". If it did, the same
mapping would be re-asserted, and eventually contradicted, on every dataset that
carries the column.

**X2 — the right match predicate.** `skos:exactMatch` is reserved for genuine
identity of **quantity, unit *and* granularity**. Getting this wrong is not a
tidiness problem; it is a modeling hazard, because a nodal voltage and a zonal
average voltage look identical at the header level and an `exactMatch` between
them tells a downstream tool it may substitute one for the other.

The working test, applied to every statement in these files:

| Question | If no |
|---|---|
| Same physical quantity? | `skos:relatedMatch` at best, usually nothing |
| Same unit, or trivially convertible with no basis change? | `skos:closeMatch` + `og:unitDiffers` |
| Same spatial or temporal granularity? | `skos:closeMatch` + `og:granularityDiffers` |
| Same measurement basis (instantaneous / interval / limit)? | `skos:closeMatch` + `og:basisDiffers` |
| All four yes? | `skos:exactMatch` |

Per-unit versus ohms is a *basis* difference, not a unit difference: converting
needs a base power and base voltage that the field alone does not carry. Those
are `closeMatch`.

**X3 — inferred is flagged.** A mapping taken from published documentation
carries `dct:source`. A mapping inferred from a field name, from a model's
behaviour or from convention carries `og:enrichmentBasis "inferred"` and a
`skos:editorialNote` saying on what basis. There is no third, unmarked category.

**X4 — no confident mapping means a gap marker.** An `og:` concept with no
defensible counterpart in a scheme gets an explicit `og:mappingGap` statement
naming the concept and the reason. Leaving it out would be indistinguishable
from not having looked.

## The four schemes

| File | Targets | Namespace | Notes |
|---|---|---|---|
| `cim-cgmes.ttl` | IEC 61970 CIM 100 / CGMES 3.0 | `http://iec.ch/TC57/CIM100#` | Published RDF vocabulary; mappings cite it |
| `pypsa.ttl` | PyPSA component attributes | minted, see below | Per-unit conventions make most matches close |
| `matpower.ttl` | MATPOWER case format columns | minted, see below | No published vocabulary at all |
| `sienna.ttl` | NREL Sienna / PowerSystems.jl | minted, see below | The scheme `og-grid-concept` was bootstrapped from; highest exactMatch density |

PyPSA, MATPOWER and Sienna publish schemas as documentation and code, not as
RDF. Rather than skip them — they are what the users of this catalog actually
run — each column or field is given a locally-minted IRI under
`https://schema.opengrid.org/crosswalk/<scheme>/`. That IRI is a *stable handle
for a documented external term*, not a claim that the external project publishes
it. `dct:source` on every scheme node points at the documentation the terms were
read from.

## Adding a mapping

1. Find the `og:` concept. If it does not exist, add it to `og-grid-concept.ttl`
   first — a crosswalk must not be the place a concept is introduced.
2. Apply the X2 test above and pick the predicate honestly. When in doubt,
   `closeMatch`; the cost of an over-weak mapping is a missed suggestion, the
   cost of an over-strong one is a wrong substitution.
3. Cite or flag (X3).
4. Run `pytest tests/vocab/test_crosswalks.py`. It enforces X2 mechanically:
   every `exactMatch` pair must agree on `og:defaultUnit`, or carry an explicit
   `og:unitDiffers` justification. That test is query Q5 (PRD §4.6) written as a
   regression test — the same audit, run at commit time rather than on demand.
