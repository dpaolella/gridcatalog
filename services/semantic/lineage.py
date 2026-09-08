"""How many layers of assumption sit under a dataset (#52).

`og:provenanceClass` is one hop. It says NREL ATB is *modeled*, and it says a
capacity expansion portfolio built on ATB is *modeled*, and it says a resource
adequacy study built on that portfolio is *modeled*. Three layers, one word,
and they read as peers. The OpenGrid data-landscape framework states the
consequence directly:

    When a state PUC runs an IRP, it stacks at least four layers of embedded
    model assumptions: NREL ATB cost projections → capacity expansion
    portfolio → production cost dispatch → resource adequacy metrics. **Each
    layer looks like "data" to the layer above it.**

`og:derivedFrom` records the edges. This computes the depth over them.

**Unknown is not zero, and that is the whole design.** A record with no
recorded lineage is *not* one hop from measurement; nobody has said what it was
built from. Returning 0 there would be the exact misreading the framework warns
about, dressed up as a number. So a root is only a root when its provenance
class says it observes something, and everything else with no edges is
``None``.

**A cycle is a data error, not a crash.** `A derivedFrom B derivedFrom A` is
wrong but reachable — two records each citing the other as upstream — and the
honest output for both is `None` rather than a `RecursionError` in a build.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Provenance classes that observe the world rather than infer it. A record in
#: one of these with no upstream recorded is genuinely at the bottom of its own
#: stack, so depth 0 is a claim the record supports.
#:
#: `curated` is deliberately absent, and it is the interesting exclusion: a
#: curated compilation is assembled *from* other sources, so a curated record
#: with no `og:derivedFrom` has an unrecorded upstream rather than none. It is
#: also `_provenance`'s fallback in the seed loader, which means treating it as
#: a root would hand depth 0 to every row that simply never stated a class.
OBSERVATIONAL: frozenset[str] = frozenset({"primary", "reanalysis", "osmDerived", "synthetic"})


def depths(
    upstream: Mapping[str, Iterable[str]],
    provenance: Mapping[str, str],
) -> dict[str, int | None]:
    """Assumption depth for every record, keyed the same way as the inputs.

    ``upstream`` maps a record to what it was built from. Entries pointing
    outside ``provenance`` are upstreams the catalog does not hold — a real
    edge to an unknown depth, which makes the result ``None`` rather than
    truncating the chain and understating it.

    ``provenance`` maps a record to its `og:provenanceClass` local name.
    """
    resolved: dict[str, int | None] = {}
    resolving: set[str] = set()

    def depth_of(record: str) -> int | None:
        if record in resolved:
            return resolved[record]
        if record in resolving:
            # A cycle. Every record on it is unresolvable, and saying so is
            # better than any number we could pick.
            return None
        parents = [str(p) for p in upstream.get(record, ())]
        if not parents:
            answer = 0 if provenance.get(record) in OBSERVATIONAL else None
            resolved[record] = answer
            return answer

        resolving.add(record)
        try:
            deepest = 0
            unresolved = False
            for parent in parents:
                if parent not in provenance:
                    unresolved = True  # an upstream the catalog does not hold
                    break
                parent_depth = depth_of(parent)
                if parent_depth is None:
                    unresolved = True
                    break
                deepest = max(deepest, parent_depth)
        finally:
            resolving.discard(record)

        answer = None if unresolved else deepest + 1
        resolved[record] = answer
        return answer

    return {record: depth_of(record) for record in provenance}


__all__ = ["OBSERVATIONAL", "depths"]
