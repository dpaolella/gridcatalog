"""What belongs to a record is written down twice, in two languages.

`datahub.graph.records.CONTAINMENT_PREDICATES` is the boundary the store uses
to gather and replace a record. `services/projector/construct.rq` walks its own
property path to read one. Both answer "what travels with this record", and
they had already drifted before anybody noticed: adding
`og:questionClassPartition` to the Python list left the query walking past it,
so a reference model round-tripped through the store came back with four
partition IRIs that resolved to nothing and projected an empty partition.

Silent in both directions, and invisible to a unit test that projects a fixture
graph directly — that graph has never been through the store, so every node is
still reachable. It took running the real pipeline to see it.
"""

from __future__ import annotations

import re
from pathlib import Path

from datahub.graph.records import CONTAINMENT_PREDICATES

CONSTRUCT = Path(__file__).resolve().parents[2] / "services" / "projector" / "construct.rq"


def walked_predicates() -> set[str]:
    """The IRIs in the containment property path of `construct.rq`.

    Read out of the query rather than duplicated here, for the same reason the
    facet-parity test parses `page.tsx`: a third copy of the list is a third
    thing to keep in step.
    """
    source = CONSTRUCT.read_text()
    match = re.search(r"\?\?root \((.*?)\)\* \?s \.", source, re.S)
    assert match, "no containment property path in construct.rq"
    return set(re.findall(r"<([^>]+)>", match.group(1)))


def test_the_projector_never_walks_past_the_record_boundary() -> None:
    """Everything the query follows must be a containment predicate.

    A predicate the projector walks and the store does not consider containment
    is a path out of the record and into a neighbour — the projected document
    would carry another record's fields, and `put` would not remove them when
    this record changed.
    """
    declared = {str(p) for p in CONTAINMENT_PREDICATES}
    extra = sorted(walked_predicates() - declared)
    assert not extra, (
        "construct.rq walks predicates the store does not treat as containment, "
        f"so the projector can read outside the record: {extra}"
    )


def test_the_partition_is_walked() -> None:
    """The specific regression, pinned by name.

    A general "these two lists are equal" assertion would be wrong: the query
    deliberately walks a *subset*, because it reads only what a search document
    needs and a record contains more than that. So the guard against the
    opposite failure — a containment predicate the projector needs and does not
    walk — has to name the ones the document actually depends on.
    """
    walked = walked_predicates()
    for needed in (
        "https://schema.opengrid.org/ns#questionClassPartition",
        "https://schema.opengrid.org/ns#hasField",
        "https://schema.opengrid.org/ns#usageEvidence",
        "https://schema.opengrid.org/ns#qualityGrade",
        "http://www.w3.org/ns/dcat#distribution",
    ):
        assert needed in walked, (
            f"`SearchDocument` carries data reached through {needed}, and "
            "construct.rq does not walk it — the field will project empty from "
            "a record that has one"
        )
