# ADR-0008: No blank nodes in the store

**Status:** Accepted · **Date:** 2026-09-04 · **Extends:** ADR-0001

## Context

Records arrive as JSON-LD, and JSON-LD produces blank nodes for every nested
object without an `@id`: a `dct:temporal` period, a `og:qualityFlags` block, the
cells of an `rdf:List`. That is idiomatic JSON-LD and it is fine in a document
you parse once.

It is not fine in a store you update. Three failures follow, and the first was
observed rather than predicted — fourteen of fifteen fixtures failed a
write-read-write round trip before this decision was made:

1. **`DELETE DATA` cannot match a blank node.** A blank node's label is local to
   the parse that produced it. Reading a record back and writing it again
   inserts new blank nodes without deleting the old ones, so the record grows a
   little on every write and a round trip is silently lossy.
2. **The semantic layer cannot address a nameless node.** It writes grades and
   resolutions to `og:graph/computed` *about parts of a record* — a grade per
   variable, a resolution per field. A part with no name cannot be the subject
   of a statement in another graph.
3. **A diff between two record versions is unreadable** when half the nodes are
   labelled `_:Nb3f2…` differently on each side. Revision history and steward
   review both depend on legible diffs.

## Decision

**Every blank node is replaced with an IRI before it reaches the store.**
RDF 1.1 §3.5 sanctions exactly this. `datahub.graph.skolem.skolemize` runs on
every write.

Names are derived from **position**, not from content or a counter:

| Blank node | Becomes |
|---|---|
| the object of `dct:temporal` on `ds/era5` | `ds/era5#temporal` |
| the object of `og:qualityFlags` | `ds/era5#qualityFlags` |
| a second object of the same predicate | `…#qualityFlags-2` |
| an `rdf:rest` chain | `…#bbox_1`, `…#bbox_2`, … |
| unreachable from any named node | `…#b-<content hash>` |

Position rather than content, because a content hash renames a node whenever
anything inside it changes, which destroys diffs. Position rather than a
counter, because a counter renames every node after an insertion. Adding a
field does not rename the temporal extent.

The names are also legible in a SPARQL console, which matters more than it
sounds: `ds/era5#temporal` tells a reader what they are looking at.

## A consequence worth stating: `og:bbox` is no longer a list

The bounding box was an ordered `rdf:List`, which was defensible — a box is four
numbers in a fixed order. Skolemising the list cells broke rdflib's JSON-LD
serializer, which assumes list cells are blank nodes, and that forced the
question of whether a list was the right model at all.

It was not. `og:bbox` is now four scalars — `og:bboxMinLon`, `og:bboxMinLat`,
`og:bboxMaxLon`, `og:bboxMaxLat` — plus a derived `og:bboxWKT` for DCAT-AP
interoperability. The list bought ordering nobody needed and cost three things
that were wanted: a SPARQL spatial filter is now a comparison rather than a list
walk, serialisation no longer depends on blank-node cells, and the semantic
layer can address a single coordinate.

## A second consequence: numeric terms are `xsd:double`, not `xsd:decimal`

rdflib's JSON-LD parser applies `@type` coercion by stamping the datatype onto
the value it already parsed. A JSON number coerced to `xsd:decimal` therefore
arrives as a decimal-typed literal holding a Python `float` — internally
inconsistent, and rejected by SHACL's datatype check with a message pointing at
a value that looks perfectly fine. Close to undebuggable.

Every term whose values arrive as JSON numbers is declared `xsd:double`, which
is what a JSON number is. `tests/test_jsonld_context.py` forbids `xsd:decimal`
in the context outright, and `records.normalise_literals` repairs any
value/datatype inconsistency that reaches the parser anyway.

## Consequences

- Round trips are lossless and idempotent; `tests/graph/test_records.py`
  asserts it across the whole fixture corpus and over three successive writes.
- Records diff cleanly, which review and revision history both depend on.
- The store contains IRIs that no external party minted. They are under the
  record's own namespace and carry a `#` fragment, so they cannot collide with
  anything, and `skolem.is_skolem` identifies them.
- Records exported for other consumers keep the skolem IRIs. That is correct —
  they are stable identifiers for real parts of the record — and a consumer who
  wants blank nodes can discard the fragments.

## Alternatives considered

- **Keep blank nodes; replace whole named graphs per record.** One graph per
  record makes `DROP GRAPH` viable, but multiplies graph count by the catalog
  size and gives up the entitlement story that having exactly one catalog graph
  provides (ADR-0006).
- **Canonicalise (URDNA2015) and hash.** Stable for identical content, and
  renames every node in a record whenever any part of it changes.
