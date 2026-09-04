# JSON-LD context

`opengrid-datahub.jsonld` is the record contract. Every catalog record is
written against it, and every component that reads a record — the projector,
the API, the SDK, the MCP server — depends on the terms it defines.

212 terms. `tests/test_jsonld_context.py` asserts coverage of every PRD §4
requirement id from a table, so a field that is missing fails rather than going
unnoticed.

## Choices worth knowing about

**Standard terms over `og:` terms, always.** Where DCAT 3, Dublin Core Terms,
PROV-O or schema.org has a term, the context uses it: `dcat:distribution`,
`dcat:accessURL`, `dct:license`, `prov:wasDerivedFrom`. An `og:` duplicate of a
standard term is a term nobody else's tooling understands, and a test enforces
this for the twenty terms most likely to be reinvented.

**`@container: @set` on every multi-valued term.** A record with one data
domain and a record with two must not differ in shape. Without this they do,
and every consumer has to normalise before it can read anything.

**`@type: @id` on every IRI-valued term.** A term expecting an IRI and typed as
a plain literal produces a string where a link belongs, and the absence is only
discovered when a traversal returns nothing.

That coercion has one sharp edge, worth knowing before it bites: because
`dct:license` is `@type: @id`, free text like `"CC BY 4.0"` does not fail to
parse — it becomes a *relative* IRI resolved against the document base, which
is why the SHACL shape requires an absolute one. The invalid fixture
`license-as-free-text` exists to keep that guard in place.

**`og:bbox` is `@container: @list`.** A bounding box is minLon, minLat, maxLon,
maxLat in that order; an unordered set of four numbers is four numbers. The
cost is an `rdf:List` of blank nodes, which is awkward in SPARQL — acceptable,
because spatial filtering is served from the search index, not from the graph.

**`og:updateCadence` is an ISO 8601 duration string**, not an IRI. The Currency
grade compares elapsed time against it, so it has to be machine-comparable, and
a controlled vocabulary of cadences is one nobody would maintain.
`dct:accrualPeriodicity` remains available and IRI-typed for consumers who want
the Dublin Core frequency vocabulary.

## Deferred terms

PRD §4.1 defers D20 (graph schema) and §4.3 defers C11–C16 (ranges, join
candidates, geo-join keys, `sameConceptAs` at field level, hierarchical shape,
graph edges). Their terms **are** defined here, because data cannot carry a
field before a term for it exists — but no SHACL shape enforces them and no UI
reads them. When they are un-deferred, the work is shapes and UI, not schema.

## Changing it

The context is `@protected`, so redefining a term is an error rather than a
silent override. Adding a term is a minor version; changing what an existing
term expands to, or its container or type, is breaking — every stored record
was written against the old meaning.
