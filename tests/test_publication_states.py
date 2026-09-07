"""One definition of "published", and nothing may keep its own copy.

Adding `auto-confirmed` (ADR-0012) meant teaching the system a second state
that publishes. `graphs.record_graph` learned it and five other places did
not, so 392 harvested records sat in the catalog graph and were skipped by the
projector with "not confirmed" — present, correct, and invisible.

That is the third time in this work that adding a thing missed one of several
places checking for it. This module is the check that costs nothing to keep.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICES = REPO_ROOT / "services"

#: Where the string is legitimately a literal rather than a membership test:
#: the place that mints it, the places that ask "did a *person* confirm this",
#: and the seed loader, whose rows are confirmed by review and never
#: auto-promoted.
ALLOWED = {
    "graph/graphs.py",  # the definition itself
    "graph/records.py",  # `promote` stamps the state a steward's review earns
    "harvest/seed.py",  # a verified seed row is human-reviewed, not auto
    "harvest/promote/policy.py",  # "already confirmed by a person" is the check
    "cli.py",  # a count in a summary line, and `record promote`'s docstring
    "api/schemas.py",  # a field default
}

#: A comparison against the bare string, which is what goes wrong. Matches
#: `== "confirmed"`, `!= "confirmed"` and `review_state == 'confirmed'`.
COMPARISON = re.compile(r"""(==|!=)\s*['"]confirmed['"]|['"]confirmed['"]\s*(==|!=)""")


def _modules() -> list[Path]:
    return sorted(p for p in SERVICES.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(SERVICES)))
def test_no_module_decides_publication_by_string_comparison(path: Path) -> None:
    """Use `PUBLISHED_STATES`, not `== "confirmed"`.

    A record is published if its review state is in that set. Two states are,
    and a module comparing against one of them silently drops the other — with
    no error, no warning, and a catalog that is simply missing most of itself.
    """
    relative = str(path.relative_to(SERVICES))
    if relative in ALLOWED:
        return
    offending = [
        f"{relative}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if COMPARISON.search(line)
    ]
    assert not offending, (
        "compare against datahub.graph.graphs.PUBLISHED_STATES instead:\n  "
        + "\n  ".join(offending)
    )


def test_both_published_states_reach_the_catalog_graph() -> None:
    from datahub.graph.graphs import PUBLISHED_STATES, NamedGraph, record_graph

    assert {"confirmed", "auto-confirmed"} == PUBLISHED_STATES
    for state in PUBLISHED_STATES:
        assert record_graph(state) is NamedGraph.CATALOG, state
    for state in ("draft", "in-review", "flagged"):
        assert record_graph(state) is NamedGraph.DRAFT, state


def test_the_shapes_allow_every_state_the_code_can_write() -> None:
    """A state the code writes and the shapes reject is a record that cannot
    be saved — found only when a real harvest tries to save one.

    Read out of the graph rather than out of the text, so reformatting the
    Turtle cannot make this pass or fail for the wrong reason.
    """
    from datahub.graph.graphs import PUBLISHED_STATES
    from datahub.namespaces import OG
    from rdflib import Graph
    from rdflib.collection import Collection
    from rdflib.namespace import SH

    shapes = Graph()
    shapes.parse(REPO_ROOT / "shapes" / "opengrid-datahub.ttl", format="turtle")

    allowed: set[str] = set()
    for constraint in shapes.subjects(SH.path, OG.reviewState):
        for listed in shapes.objects(constraint, SH["in"]):
            allowed |= {str(v) for v in Collection(shapes, listed)}

    assert allowed, "no sh:in on og:reviewState; the shape stopped constraining it"
    missing = PUBLISHED_STATES - allowed
    assert not missing, f"the code writes {sorted(missing)}, which the shapes reject"
