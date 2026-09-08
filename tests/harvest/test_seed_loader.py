"""Loading the curated seed inventory (WP-2.5).

The seed file's header is the specification these tests enforce:

> Do not treat the license or tier fields on unverified rows as authoritative.

So the load-bearing assertions are about the *boundary* — that an unreviewed
row cannot reach the catalog graph, and that no record carries a licence the
file did not state. Everything else is arithmetic.
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
from datahub.harvest.adapters.curated import CuratedAdapter
from datahub.harvest.seed import SeedLoader, load_seed

SPDX = "http://spdx.org/licenses/"


@pytest.fixture(scope="module")
def loaded():
    """The whole seed inventory, loaded once and validated for real.

    Module-scoped because a full load validates 113 records against SHACL and
    that is not something to repeat per test. ``validate=True`` is the point of
    the fixture: a record that does not conform never reaches the store, so
    every assertion below is made about records that passed.
    """
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    result = SeedLoader(records).load()
    return records, result


@pytest.fixture(scope="module")
def documents(loaded):
    """Every loaded record, read back out of the store as JSON-LD.

    Read back rather than kept from the write, so these tests see what a
    consumer of the API would see — including anything the round trip through
    RDF loses.
    """
    records, _ = loaded
    out = {}
    for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT):
        for dataset_id in records.list_ids(graph=graph):
            out[dataset_id] = (graph, dataset_node(records.get(dataset_id, graph=graph)))
    return out


# ---- everything loads, and everything that loads validates ---------------


def test_every_seed_row_loads(loaded) -> None:
    _, result = loaded
    assert result.failures == [], f"seed rows failed validation: {result.failures[:5]}"
    # 130 rows, three cross-domain pairs merged into one record each: the EU ETS
    # entry across DD7/DD8, NREL ATB across DD6/DD9 — whose own note says "Model
    # as one dataset with domain facets, not two records" — and the World Bank
    # Pink Sheet across DD7/DD9. The last two merge because the file now gives
    # them a shared `slug`; before that the merge keyed on the name and their
    # names differ, so they published twice (#20).
    assert result.total == 130
    assert result.confirmed + result.drafted == 127


def test_every_record_is_in_the_store(loaded) -> None:
    records, result = loaded
    assert records.count(graph=NamedGraph.CATALOG) == result.confirmed
    assert records.count(graph=NamedGraph.DRAFT) == result.drafted


def test_every_record_declares_the_level_it_was_validated_at(documents) -> None:
    for dataset_id, (_, node) in documents.items():
        assert node["completenessLevel"] == 1, dataset_id


def test_all_ten_domains_are_populated(loaded) -> None:
    _, result = loaded
    assert set(result.by_domain) == {f"DD{n}" for n in range(1, 11)}
    assert sum(result.by_domain.values()) == 130, "the merged record counts under both domains"


# ---- the boundary: unreviewed rows cannot reach the catalog --------------


def test_unverified_rows_are_draft_and_only_draft(documents) -> None:
    """The assertion the module exists for.

    A reviewed record and an unreviewed one look identical to a reader, and
    only one of them has had its licence checked. So the check is made on both
    sides: the review state says draft *and* the record is in the draft graph.
    """
    adapter = CuratedAdapter()
    harvested, _ = adapter.harvest()
    unverified_slugs = {
        r.source_id.rsplit(":", 1)[1] for r in harvested if not r.payload.get("verified")
    }

    for dataset_id, (graph, node) in documents.items():
        slug = dataset_id.rsplit("/", 1)[1]
        if slug not in unverified_slugs:
            continue
        assert node["reviewState"] == "draft", dataset_id
        assert graph is NamedGraph.DRAFT, f"{dataset_id} is unreviewed and in the catalog graph"


def test_no_unreviewed_record_is_in_the_catalog_graph(loaded) -> None:
    records, _ = loaded
    for dataset_id in records.list_ids(graph=NamedGraph.CATALOG):
        node = dataset_node(records.get(dataset_id, graph=NamedGraph.CATALOG))
        assert node["reviewState"] == "confirmed", dataset_id


def test_every_unreviewed_record_says_so_in_its_description(documents) -> None:
    """A user reading a draft record in the UI should not have to know what
    ``reviewState`` means to understand that nobody has checked it."""
    for dataset_id, (_, node) in documents.items():
        if node["reviewState"] != "draft":
            continue
        assert "not been through licence and access-path review" in node["description"], dataset_id
        caveats = node["qualityFlags"]["caveat"]
        caveats = caveats if isinstance(caveats, list) else [caveats]
        assert any("not yet reviewed" in c for c in caveats), dataset_id


# ---- no invented facts ---------------------------------------------------


def test_no_record_invents_a_licence(documents) -> None:
    """Either the licence traces to an SPDX identifier the file's string maps
    to unambiguously, or it is a LicenseRef carrying the original text. Never a
    guess: a reader who sees "CC-BY-4.0" on a dataset nobody checked has been
    actively misled, which is worse than being told nothing (PRD §7.4)."""
    for dataset_id, (_, node) in documents.items():
        licence = node["license"]
        assert licence.startswith(SPDX), dataset_id
        identifier = licence.removeprefix(SPDX)
        if identifier.startswith("LicenseRef-"):
            assert node.get("licenseNote"), f"{dataset_id} has a LicenseRef and no explanation"
        else:
            assert "LicenseRef" not in identifier


def test_unresolvable_licence_strings_keep_their_original_text(loaded) -> None:
    records, _ = loaded
    loader = SeedLoader(records)
    mapped = loader._licence({"license": "CC-BY-4.0"})
    described = loader._licence({"license": "open access, no formal license stated"})
    unknown = loader._licence({"license": "ask Dave, he knows the terms"})
    absent = loader._licence({})

    # Unambiguous: an SPDX identifier and nothing else to say.
    assert mapped == {"license": f"{SPDX}CC-BY-4.0"}
    # Real terms with no SPDX identifier: a LicenseRef and the terms written out.
    assert described["license"] == f"{SPDX}LicenseRef-No-Formal-Licence"
    assert "not the same as public domain" in described["licenseNote"].lower()
    assert described["redistributionAllowed"] is False
    # Unrecognised: the original text kept verbatim, and no guess.
    assert unknown["license"].startswith(f"{SPDX}LicenseRef-Unreviewed-")
    assert "ask Dave, he knows the terms" in unknown["licenseNote"]
    assert unknown["redistributionAllowed"] is False
    # Silent: silence is not permission.
    assert absent["license"] == f"{SPDX}LicenseRef-Unstated"
    assert absent["redistributionAllowed"] is False


def test_a_licence_that_is_not_stated_is_not_permissive(documents) -> None:
    """Absent an explicit grant, default copyright applies. The catalog must
    not let silence read as permission."""
    for dataset_id, (_, node) in documents.items():
        if node["license"] == f"{SPDX}LicenseRef-Unstated":
            assert node.get("redistributionAllowed") is False, dataset_id


def test_provenance_is_never_upgraded_by_a_guess(loaded) -> None:
    """The provenance class caps the Provenance grade (PRD §6), so an invented
    one is an invented quality claim. Unknown strings fall back to `curated`,
    which is true of every row in a curated inventory."""
    records, _ = loaded
    loader = SeedLoader(records)
    assert loader._provenance({"provenance": "reanalysis"})["provenanceClass"].endswith(
        "/reanalysis"
    )
    assert loader._provenance({"provenance": "something nobody has defined"})[
        "provenanceClass"
    ].endswith("/curated")
    assert loader._provenance({})["provenanceClass"].endswith("/curated")


def test_booleans_survive_the_round_trip_as_booleans(documents) -> None:
    """``"false"`` is truthy in Python and in JavaScript. A consumer writing
    ``if record["anonymousAccess"]`` must not read an account-gated dataset as
    openly accessible."""
    seen_false = False
    for dataset_id, (_, node) in documents.items():
        value = node.get("anonymousAccess")
        if value is None:
            continue
        assert isinstance(value, bool), f"{dataset_id}: {value!r} is a {type(value).__name__}"
        seen_false = seen_false or value is False
    assert seen_false, "the corpus should exercise the False case"


# ---- structure -----------------------------------------------------------


def test_every_record_has_an_access_path_or_says_why_it_has_none(documents) -> None:
    """ "Where do I get it" is one of the four questions the catalog exists to
    answer, so a record the catalog offers and cannot locate is a broken record.

    This used to end at `assert node["distribution"]`, and the comment said "a
    tier 3 pointer's distribution is its landing page". For 34 of them the seed
    inventory has no landing page, so the loader minted one —
    `https://opengrid.org/catalog/no-known-access-path` — and this assertion
    passed on a fabricated URL (#18). A reference-only record may now have no
    distribution, and must say why instead; the pair is enforced by
    `og:AccessPathShape` and covered in `test_no_fabricated_access.py`.
    """
    for dataset_id, (_, node) in documents.items():
        if node.get("referenceOnly"):
            assert node.get("distribution") or str(node.get("pointerRationale") or "").strip(), (
                f"{dataset_id} offers nothing and explains nothing"
            )
        else:
            assert node["distribution"], dataset_id


def test_tier_three_records_are_marked_reference_only(documents) -> None:
    tier_three = [d for d, (_, n) in documents.items() if n.get("tier") == 3]
    assert tier_three, "the seed inventory should contain tier 3 pointers"
    for dataset_id in tier_three:
        node = documents[dataset_id][1]
        assert node["referenceOnly"] is True, dataset_id
        assert node["documentationStatus"] == "none"


def test_access_barriers_are_carried_not_dropped(documents) -> None:
    barriered = [d for d, (_, n) in documents.items() if n.get("accessBarrier")]
    assert barriered, "the seed inventory records access barriers"
    for dataset_id in barriered:
        node = documents[dataset_id][1]
        assert not node["accessRestriction"].endswith("/none"), dataset_id


def test_a_cross_domain_dataset_is_one_record_with_two_facets(documents) -> None:
    """The seed file lists EU ETS / EEA EUTL under both DD7 and DD8, and says
    of NREL ATB: "Model as one dataset with domain facets, not two records."
    Writing two would have the second silently replace the first."""
    multi = {
        d: n["dataDomain"]
        for d, (_, n) in documents.items()
        if len(n["dataDomain"] if isinstance(n["dataDomain"], list) else [n["dataDomain"]]) > 1
    }
    assert multi, "the merged cross-domain record should carry two domain facets"
    for domains in multi.values():
        assert len(set(domains)) == len(domains)


def test_a_merge_never_launders_an_unreviewed_row(loaded) -> None:
    """When two rows describe one dataset, the verified one is the base. The
    other way round, a reviewed record would inherit an unreviewed licence."""
    records, _ = loaded
    loader = SeedLoader(records)
    harvested, _ = CuratedAdapter().harvest()
    merged = [(base, extra) for base, extra in loader._merge_cross_domain(harvested) if extra]

    assert merged, "the fixture inventory should contain a cross-domain pair"
    for base, _ in merged:
        assert base.payload.get("verified") is True


# ---- idempotency ---------------------------------------------------------


def test_reloading_changes_nothing(loaded) -> None:
    """Re-harvest is an update, not an insert. A second load that grew the
    catalog would mean the identity key is wrong."""
    records, first = loaded
    before = (
        records.count(graph=NamedGraph.CATALOG),
        records.count(graph=NamedGraph.DRAFT),
    )

    second = SeedLoader(records).load()

    assert second.total == first.total
    assert second.confirmed == first.confirmed
    assert second.drafted == first.drafted
    assert (
        records.count(graph=NamedGraph.CATALOG),
        records.count(graph=NamedGraph.DRAFT),
    ) == before


def test_reloading_does_not_accumulate_triples(loaded) -> None:
    """The blank-node trap (ADR-0008): DELETE DATA cannot match a blank node,
    so before skolemisation every rewrite left the parts it meant to replace
    behind. Checked on a real record rather than in the abstract."""
    records, _ = loaded
    dataset_id = records.list_ids(graph=NamedGraph.CATALOG)[0]
    before = len(records.get_graph(dataset_id))

    document = records.get(dataset_id)
    records.put(document)

    assert len(records.get_graph(dataset_id)) == before


# ---- the convenience entry point ----------------------------------------


def test_load_seed_honours_a_limit(store) -> None:
    bootstrap(store)
    result = load_seed(RecordStore(store), limit=5)
    assert result.total == 5
    assert result.confirmed + result.drafted == 5
    assert not result.failures


def test_summary_reports_failures_rather_than_hiding_them(store) -> None:
    from datahub.harvest.seed import SeedLoadResult

    result = SeedLoadResult(total=3, confirmed=1, drafted=1, by_level={1: 2})
    result.failures.append(("ds/x", "boom"))
    assert "1 failed validation" in result.summary


# ---- silence in the seed file is not a claim (PRD §14.2) -----------------


def test_the_access_posture_never_contradicts_itself(documents) -> None:
    """ "No restriction" and "not retrievable anonymously" cannot both be true.

    They were, on 42 of the 56 verified rows: the two fields were defaulted
    independently and the defaults disagreed. A reader saw a record saying
    nothing stands between them and the dataset, next to a field saying they
    cannot retrieve it without an account.
    """
    contradictions = []
    for dataset_id, (_, node) in documents.items():
        restriction = str(node.get("accessRestriction") or "").rsplit("/", 1)[-1]
        if restriction == "none" and node.get("anonymousAccess") is not True:
            contradictions.append(dataset_id)
    assert not contradictions, (
        f"{len(contradictions)} records claim no access restriction while denying anonymous "
        f"access: {sorted(contradictions)[:5]}"
    )


def test_a_row_that_states_nothing_optional_claims_nothing(loaded) -> None:
    """The case #39 asks for: every optional key absent.

    Two fields cannot simply be left out. `og:anonymousAccess` is `sh:minCount 1`
    at level 1 — it is a Tier 1 criterion an unauthenticated evaluator filters on
    — and `og:accessRestriction` takes one of the six concepts PRD D9 fixes, none
    of which means "not established". So the record makes the conservative
    assumption, and the caveat carries what the schema cannot: that this was
    assumed rather than checked.
    """
    from datahub.harvest.adapters.base import HarvestedRecord

    records, _ = loaded
    bare = HarvestedRecord(
        source_id="seed",
        source="curated",
        payload={
            "name": "A dataset the seed file barely mentions",
            "data_domain": "DD1",
            "tier": 2,
            "verified": True,
        },
    )
    record = SeedLoader(records).to_record(bare)

    assert record["accessRestriction"].endswith("/accountRequired")
    assert record["anonymousAccess"] is False
    assert record["qualityFlags"]["staleness"] == "unknown"
    assert any("has not been checked" in caveat for caveat in record["qualityFlags"]["caveat"]), (
        record["qualityFlags"]["caveat"]
    )

    # Nothing else is invented either. An unstated licence gets the explicit
    # `LicenseRef-Unstated` marker and a note saying so — the same pattern as
    # the access caveat, and the reason it is the right one: a required field
    # that cannot be omitted says "not captured" in words rather than picking a
    # plausible value. A DOI and a summary are optional, so they are simply
    # absent.
    assert record["license"].endswith("LicenseRef-Unstated")
    assert "records no licence" in record["licenseNote"]
    assert record["redistributionAllowed"] is False
    assert "persistentId" not in record
    assert "summary" not in record


def test_a_stated_access_posture_is_not_flagged_as_an_assumption(loaded) -> None:
    """The caveat has to mean something. A row that says `anonymous: false` has
    been checked, and adding "not checked" to it would be the same class of
    untruth in the other direction."""
    from datahub.harvest.adapters.base import HarvestedRecord

    records, _ = loaded
    stated = HarvestedRecord(
        source_id="seed",
        source="curated",
        payload={
            "name": "A dataset whose access the seed file does state",
            "data_domain": "DD1",
            "tier": 2,
            "verified": True,
            "anonymous": False,
        },
    )
    record = SeedLoader(records).to_record(stated)

    assert record["anonymousAccess"] is False
    assert not any("has not been checked" in caveat for caveat in record["qualityFlags"]["caveat"])


def test_no_record_asserts_a_currency_it_was_never_told(documents) -> None:
    """`verified: true` in the seed file means a cataloguer checked the row's
    licence and tier. It was being read as "this dataset is up to date", and 56
    records published `staleness: current` on the strength of it. The seed
    inventory has no currency field at all."""
    asserted = [
        dataset_id
        for dataset_id, (_, node) in documents.items()
        if (node.get("qualityFlags") or {}).get("staleness") not in (None, "unknown")
    ]
    assert not asserted, (
        f"{len(asserted)} records state a staleness the seed file does not: {sorted(asserted)[:5]}"
    )
