"""No record carries an access URL nobody can follow (#18, PRD §14.4).

The seed loader used to mint `https://opengrid.org/catalog/no-known-access-path`
for every row with no access path — 34 of 114, all tier 3 pointers with no URL,
no DOI and no secondary access. The UI rendered it as a live "Open at source"
button on 23 of the 66 published records. PRD §14.4 forbids fabricating a URL
outright, and the reason it happened anyway is worth keeping in view: level 1
required at least one distribution, so inventing one was the only way to publish
a record that could not answer "where do I get it".

The constraint moved rather than lapsing. `og:AccessPathShape` asks a dataset
the catalog offers for a distribution, and a reference-only record for
`og:pointerRationale` — why there is none. So these tests come in pairs: nothing
is fabricated, **and** nothing is silently dropped in its place.

The module-scoped fixture loads with `validate=True`, so every assertion is
about records that conformed. A record that stops conforming does not reach the
store and shows up as a count that moved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader

#: The URL that used to be minted. Named here so the assertion below is about
#: this specific fabrication and not about any URL that happens to look odd.
SENTINEL = "https://opengrid.org/catalog/no-known-access-path"


@pytest.fixture(scope="module")
def catalog() -> dict[str, dict]:
    """Every seed record that validated, by id."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    return {
        dataset_id.rsplit("/", 1)[-1]: dataset_node(records.get(dataset_id, graph=graph))
        for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for dataset_id in records.list_ids(graph=graph)
    }


def _distributions(node: dict) -> list[dict]:
    dists = node.get("distribution") or []
    return dists if isinstance(dists, list) else [dists]


def test_no_record_carries_the_fabricated_access_url(catalog) -> None:
    """The bug itself, asserted on the loaded graph rather than the source."""
    offenders = [
        dataset_id
        for dataset_id, node in catalog.items()
        for dist in _distributions(node)
        if isinstance(dist, dict) and dist.get("accessURL") == SENTINEL
    ]
    assert not offenders, (
        f"{len(offenders)} records point at a URL that was invented to satisfy a "
        f"shape, and the Downloads tab renders it as a live link: {sorted(offenders)[:5]}"
    )


def test_no_source_file_constructs_the_sentinel() -> None:
    """A URL nothing builds cannot be fabricated by accident later.

    Cheap, and it catches the reintroduction a graph-level assertion would not:
    a second call site somewhere the seed loader is not.

    Over the AST rather than the text, because `seed.py` still names the URL —
    in the docstring that explains why it is gone, which is worth keeping and is
    not a defect. A docstring is a string constant in a known position, so the
    two cases are distinguishable exactly rather than by a heuristic about
    leading quotes that a reflow would break.
    """
    import ast

    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in sorted((root / "services").rglob("*.py")):
        source = path.read_text()
        if SENTINEL not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        offenders += [
            f"{path.relative_to(root)}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and SENTINEL in node.value
            and id(node) not in docstrings
        ]
    assert not offenders, f"the sentinel URL is built as a value at {offenders}"


def test_a_record_with_no_access_path_says_why(catalog) -> None:
    """The other half: an absent distribution is not an absent explanation.

    Enforced by `og:AccessPathShape`, so a record that failed it would not be in
    this fixture at all. Asserted here anyway because the shape is one line of
    SPARQL and this is the sentence a reader actually sees.
    """
    silent = [
        dataset_id
        for dataset_id, node in catalog.items()
        if not _distributions(node) and not str(node.get("pointerRationale") or "").strip()
    ]
    assert not silent, (
        "these records offer no access path and give no reason, which is "
        f"indistinguishable from nobody having looked: {sorted(silent)}"
    )


def test_the_records_with_no_access_path_are_the_reference_only_ones(catalog) -> None:
    """A real dataset must not lose its distribution to this change.

    The failure mode of the fix, stated positively: dropping the sentinel could
    just as easily have dropped a legitimate access URL, and every record would
    still validate — as a reference-only pointer. It would be silent, and the
    catalog would stop answering "where do I get it" for datasets it can offer.
    """
    without = {dataset_id for dataset_id, node in catalog.items() if not _distributions(node)}
    not_reference_only = {
        dataset_id for dataset_id in without if not catalog[dataset_id].get("referenceOnly")
    }
    assert not not_reference_only, (
        f"a dataset the catalog offers has no access path: {sorted(not_reference_only)}"
    )
    # The seed inventory has 34 such rows. Pinned so that a change which quietly
    # publishes fewer records, or strips more distributions, has to say so here.
    assert len(without) == 34, (
        f"{len(without)} records have no access path; the seed inventory has 34 "
        "tier 3 pointers with no URL, no DOI and no secondary access"
    )


def test_a_dataset_with_an_access_url_still_has_its_distribution(catalog) -> None:
    """The common case, which is most of the catalog."""
    node = catalog["global-transmission-database"]
    dists = _distributions(node)
    assert len(dists) == 1
    assert dists[0]["accessURL"] == "https://zenodo.org/records/15527469"


def test_the_rationale_records_an_absence_rather_than_asserting_a_fact(catalog) -> None:
    """PRD §14.2: a missing field means "not captured", never "does not exist".

    Three of the 34 have no `pointer_rationale` and no `access_barrier` in the
    inventory, so the loader supplies one. It has to say that *the inventory*
    records no reason — not that the dataset cannot be obtained, which nobody
    established.
    """
    node = catalog["nrel-reeds-transmission-network"]
    rationale = str(node.get("pointerRationale") or "")
    assert "no reason" in rationale, rationale
    assert "unexamined" in rationale, (
        "the fallback states an absence in the record, so it must not read as a "
        f"finding about the dataset: {rationale!r}"
    )


def test_a_barrier_the_inventory_did_record_is_used_instead_of_the_fallback(catalog) -> None:
    """Four of the seven have a barrier stated as a concept rather than prose.

    Reaching for the "no reason recorded" fallback there would throw away
    something the inventory does say.
    """
    rationale = str(catalog["wood-mackenzie-wind-solar"].get("pointerRationale") or "")
    assert "commercial-paywall" in rationale, rationale
