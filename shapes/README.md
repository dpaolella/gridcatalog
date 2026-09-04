# SHACL shapes

`opengrid-datahub.ttl` holds the metadata contract as constraints. It is applied
on every write to `og:graph/draft` and on promotion to `og:graph/catalog`;
violations block a record from the review queue's ready state (PRD §4.5).

## Level parameterisation

A record publishes at completeness level 1 and is promoted to 2 and 3 as detail
arrives (ADR-0004). A level-3 constraint must therefore not block a level-1
record.

Every property shape carries `og:appliesAtLevel`. The runner builds a shapes
graph for a target level by **excising** everything above it — detaching
property shapes from their parents and removing node shapes entirely, including
their blank-node subgraphs.

There is deliberately no list in the runner of which constraint applies at which
level. The annotation is the mechanism, so adding a constraint at a level is a
one-line change to this file.

```python
runner = ValidationRunner()
runner.validate(graph, target_level=1)   # publication gate
runner.validate(graph, target_level=3)   # promotion gate
runner.highest_passing_level(graph)      # what the record actually supports
```

Excision must be complete, not partial. An orphaned `sh:SPARQLTarget` left
behind in the graph sends pySHACL's advanced mode into a non-terminating scan —
it hangs rather than erroring, which is the worst way for this to fail.
`tests/test_validation_runner.py::test_level_filtering_leaves_no_orphaned_sparql_targets`
keeps that discovered.

## The four shapes PRD §4.5 names

| Rule | Shape | Why it matters |
|---|---|---|
| A field marked `estimated` or `modeled` with no `og:derivedFromField` and no `og:fieldSource` fails | `og:ModelledFieldShape` | Stops a modelled number passing as an observation |
| A geospatial-primary dataset without `og:bbox`, `og:nativeCRS`, `og:geometryTypes` fails **at level 1** | `og:GeospatialPrimaryShape` | Coordinates without a CRS are not locations |
| A field with neither `og:concept` nor a gap marker fails **at level 3** | `og:FieldShape`'s `sh:or` | Rule X4: silence is indistinguishable from not having looked |
| A distribution advertising range requests with no `og:chunkIndexMethod` fails | `og:RangeRequestShape` | The broker would issue a partial-read plan that does not work |

Beyond those, the shapes enforce the honesty rules as constraints rather than as
documentation (X3, X4), the three-facet quality model with no composite
(ADR-0007), the unused Currency grade C, and the rule that a hosted copy cannot
be published without a named refresh owner (PRD §F2).

## Messages

`sh:resultMessage` already names the focus node and path, so `sh:message` is
written to carry the **remedy**. Not "violates minCount" but:

> This field is marked estimated or modeled but records no origin. Add
> `og:fieldSource` (where the input came from) or `og:derivedFromField` (which
> source field it was computed from), or change `og:valueBasis` to measured.

PRD §10 makes "an invalid record is rejected with a message pointing at the
failing triple" the M1 done-criterion, and a steward's time is the scarce
resource the message is spending.

## Advanced mode

Constraints conditional on a *value* rather than on a class use SPARQL-based
targets, so pySHACL runs with `advanced=True`. The runner sets it; do not turn
it off, and do not add a `sh:prefixes` pointing at a bare namespace IRI — it
must reference a node carrying `sh:declare`, and pointing it elsewhere hangs
validation.

## Adding a constraint

1. Put it on the right shape, with `og:appliesAtLevel` set to the level it
   should first apply at.
2. Write `sh:message` as an instruction to a steward.
3. Add a **valid** fixture that satisfies it and an **invalid** fixture that
   does not, with an entry in `tests/fixtures/invalid/expected-violations.yaml`
   naming the constraint component and path. Asserting only that the invalid
   record fails would let a constraint that fires for the wrong reason pass.
4. `pytest tests/conformance`.
