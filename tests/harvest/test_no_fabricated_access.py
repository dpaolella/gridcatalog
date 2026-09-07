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


# ---------------------------------------------------------------------------
# The same rule, applied to documentation (#25)
# ---------------------------------------------------------------------------


def test_no_seed_record_claims_documentation_by_an_external_standard(catalog) -> None:
    """An empty column is not evidence of anything (PRD §14.2).

    `_documentation_status` returned `external-standard-only` for any row
    without a `note` — a specific positive claim, *this dataset's fields are
    documented by reference to an external standard*, asserted on 48 published
    records because the seed file has no documentation column at all.

    It is also the one value the Documentation grade special-cases: at level 2
    it caps the facet at C regardless of the fields the record carries. So the
    guess did not merely say something untrue, it would have overridden the
    evidence once there was any.
    """
    claimed = sorted(
        dataset_id
        for dataset_id, node in catalog.items()
        if node.get("documentationStatus") == "external-standard-only"
    )
    assert not claimed, (
        "the seed loader cannot know this — it comes from a curated record where "
        f"somebody checked: {claimed[:6]}"
    )


def test_a_record_with_nothing_recorded_says_so_where_the_reader_looks(catalog) -> None:
    """`none` is still a claim, so the caveat has to carry the truth.

    The enum has no "not established" member and `og:documentationStatus` is
    required at level 1, so the record must say something. What keeps that
    honest is the caveat beside it.
    """
    node = catalog["eia-860-annual-electric-generator-report"]
    assert node.get("documentationStatus") == "none"
    flags = node.get("qualityFlags") or {}
    caveats = flags.get("caveat") or []
    caveats = caveats if isinstance(caveats, list) else [caveats]
    assert any("records an absence, not a finding" in str(c) for c in caveats), caveats


def test_a_row_the_inventory_describes_still_counts_as_partial(catalog) -> None:
    """The change must not flatten every seed row to `none`: a `note` is real
    documentation, written by a cataloguer, and saying so is not a guess."""
    node = catalog["global-transmission-database"]
    assert node.get("documentationStatus") == "partial"


def test_no_record_says_its_publisher_stopped_unless_the_inventory_does(catalog) -> None:
    """`fragmented` is not `discontinued` (#27).

    `ar:discontinued` means *the publisher has stopped producing the dataset*.
    Every row the seed file marks `fragmented` is the opposite: a live subject
    with no canonical source — interconnection study results, data-centre load
    projections, ELCC studies by ISO — produced continuously as per-jurisdiction
    PDFs with incompatible methodologies. Six published records asserted that
    their publishers had stopped.

    No row in the inventory carries a barrier that means discontinued, so the
    concept should appear on no seed record at all. It stays in the vocabulary
    for curated records, where somebody establishes it.
    """
    claimed = sorted(
        dataset_id
        for dataset_id, node in catalog.items()
        if str(node.get("accessRestriction") or "").endswith("/discontinued")
    )
    assert not claimed, f"the seed inventory does not establish this for {claimed}"


def test_an_unmapped_barrier_is_still_carried_in_words(catalog) -> None:
    """Dropping the wrong mapping must not drop the fact.

    D9 has no member for "no canonical source", so the restriction falls back to
    the conservative default — and the reason has to survive somewhere the
    reader sees it, or the fix has traded a false statement for a missing one.
    """
    node = catalog["elcc-studies-by-iso"]
    assert node.get("accessBarrier") == "fragmented"
    flags = node.get("qualityFlags") or {}
    caveats = flags.get("caveat") or []
    caveats = caveats if isinstance(caveats, list) else [caveats]
    text = " ".join(str(c) for c in caveats)
    assert "fragmented" in text, text
    assert "has not been checked" in text, (
        "the restriction on this record is a default, and nothing says so: " + text
    )


def test_no_published_record_carries_text_written_for_a_cataloguer(catalog) -> None:
    """`SeedLoader.CURATOR_ONLY` names the keys. This is what enforces it (#29).

    Five published descriptions used to carry sentences addressed to whoever was
    cataloguing the row, including "Confirm with counsel before shipping the
    extraction" — an unresolved legal question rendered as a dataset's public
    description. Those sentences moved to `curator_note`, and the exclusion is
    by construction: nothing builds a record by iterating a row's keys.

    By construction is the strong form and the invisible one. The next person to
    add a reader-facing field can reintroduce it in one line without noticing,
    so the constant is inert documentation unless something reads it. This does,
    against every published record and every value under those keys.
    """
    import yaml
    from datahub.harvest.seed import SeedLoader

    inventory = yaml.safe_load(Path("data/seed-sources.yaml").read_text())
    rows = [row for block in inventory["seed_datasets"].values() for row in block["datasets"]]
    private = [
        (row.get("slug") or row["name"], key, str(row[key]).strip())
        for row in rows
        for key in SeedLoader.CURATOR_ONLY
        if str(row.get(key) or "").strip()
    ]
    assert private, "no curator-only text in the inventory, so this test asserts nothing"

    published = " ".join(str(node) for node in catalog.values())
    leaked = [
        f"{name} ({key}): {text[:60]}" for name, key, text in private if text[:40] in published
    ]
    assert not leaked, f"text written for a cataloguer reached a published record: {leaked}"
