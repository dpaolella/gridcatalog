"""Invented richness attaches only to invented entities (#85).

Two labelling problems, and both make a polished demo actively misleading if
left alone.

**Curation reads as free.** A catalog in which every record is sourced, graded
and complete invites exactly one conclusion: that this happened by itself. It
did not, and whether assessment is staffed, self-reported or automated is an
open question with a headcount attached — so a demo that answers it by
implication answers it in the direction that costs somebody the budget.
`og:curationBasis` makes the answer a field.

**"Synthetic" already means something.** The vocabulary defines it as values
generated to be representative of a real system while corresponding to no real
asset — TAMU's networks, genuinely published by a real institution. It is the
wrong word for a record invented to illustrate a demo, and one badge for both
lets "we made this up" borrow the credibility of "somebody published this".
`og:demonstration` is the second marker.

These are corpus properties rather than code properties, so they are asserted
over the fixtures rather than over a function.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

RECORDS = sorted((ROOT / "tests" / "fixtures" / "records").glob("*.jsonld"))
REGISTRY = sorted((ROOT / "tests" / "fixtures" / "registry").glob("*.jsonld"))
ALL = RECORDS + REGISTRY

BASES = {"staff-assessed", "self-reported", "machine-extracted"}

#: Words a record uses about itself when it is admitting to being invented.
INVENTED = re.compile(r"\b(invented|fictional|fictitious)\b", re.I)


def root_node(path: Path) -> dict:
    document = json.loads(path.read_text())
    nodes = document.get("@graph", [document])
    return nodes[0]


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_every_record_says_who_assessed_it(path: Path) -> None:
    """The line that turns the trap into the argument.

    With it, a viewer reads the ideal state as an achievable mix of staff work,
    publisher self-report and machine extraction. Without it they read it as
    magic, and the demo becomes evidence that no curation budget is needed.
    """
    node = root_node(path)
    basis = node.get("curationBasis")
    assert basis in BASES, f"{path.stem} has curationBasis {basis!r}, expected one of {BASES}"


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_an_assessment_date_implies_somebody_assessed_it(path: Path) -> None:
    """`assessedAt` and `rubricVersion` describe a person's work. On a
    machine-extracted record they claim a review that did not happen."""
    node = root_node(path)
    if node.get("assessedAt") or node.get("rubricVersion"):
        assert node.get("curationBasis") == "staff-assessed", path.stem


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_a_record_that_calls_itself_invented_is_marked_as_one(path: Path) -> None:
    """Prose and field must agree, and the field is the one that renders.

    A description saying "every value in this record is invented" is worth
    nothing to a reader scanning a list of twenty rows, and nothing at all to
    the API. This catches the direction that matters: prose that admits it,
    with no marker to show for it.
    """
    node = root_node(path)
    text = json.dumps(node)
    if INVENTED.search(text):
        assert node.get("demonstration") is True, (
            f"{path.stem} describes itself as invented and carries no og:demonstration"
        )


def test_the_marker_is_not_inferred_from_the_prose() -> None:
    """The other direction, and why the list is explicit.

    `coalition-assumptions-v1` is as invented as the filing it forks and never
    uses the word — it says "one value changed from the parent set". A corpus
    marked by grepping descriptions had five of the six registry records right
    and left the sixth reading as a real assumption document. So the marker is
    authored, and this asserts the case that would have been missed.
    """
    node = root_node(ROOT / "tests" / "fixtures" / "registry" / "coalition-assumptions-v1.jsonld")
    assert node.get("demonstration") is True
    assert not INVENTED.search(json.dumps(node)), (
        "this fixture is the one that proves the point; if it now says "
        "'invented' in prose, pick another silent one or drop this test"
    )


@pytest.mark.parametrize("path", ALL, ids=lambda p: p.stem)
def test_demonstration_is_never_spelled_as_a_provenance_class(path: Path) -> None:
    """The two must not collapse into one badge.

    `pc:synthetic` is a real, published dataset generated to be statistically
    or topologically representative of a system it does not contain. A record
    OpenGrid invented is not that, and labelling it so would borrow a real
    institution's credibility for something nobody published.
    """
    node = root_node(path)
    if node.get("demonstration") is True:
        provenance = str(node.get("provenanceClass") or "")
        assert not provenance.endswith("/synthetic"), (
            f"{path.stem} is a demonstration record wearing the synthetic badge"
        )


def test_the_corpus_is_a_mix_and_not_a_uniform_claim() -> None:
    """The property the whole issue is about, asserted once over the corpus.

    If every record ever comes back with the same basis and none is marked as a
    demonstration, the fields have become decoration: present, uniform, and
    carrying no information a reader could act on. That is the state this
    catalog was in before — `og:harvestSource` reads "curated" on all
    twenty-six records — and it is worth failing on rather than shipping.
    """
    nodes = [root_node(p) for p in ALL]
    demonstrations = [n for n in nodes if n.get("demonstration") is True]

    assert demonstrations, "no record is marked as a demonstration; the marker is decoration"
    assert len(demonstrations) < len(nodes), (
        "every record is a demonstration, which would make the catalog a mock-up"
    )
