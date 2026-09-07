"""The in-memory search backend is a real index, so it is tested like one."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from datahub.api.search import (
    BBoxFilter,
    Entitlement,
    RangeFilter,
    SearchDocument,
    SearchRequest,
    SortSpec,
)
from datahub.api.search.document import ConceptRef, QualityBadges, SpatialCoverage, TemporalCoverage


def doc(**kwargs) -> SearchDocument:
    base = {"id": "x", "iri": "urn:x", "title": "T", "review_state": "confirmed"}
    return SearchDocument(**{**base, **kwargs})


@pytest.fixture
def corpus(search_backend):
    docs = [
        doc(
            id="era5",
            iri="urn:era5",
            title="ECMWF ERA5 reanalysis",
            summary="Hourly global atmospheric reanalysis",
            license_id="Copernicus-1.2",
            data_domains=[ConceptRef(iri="c:DD5", label="Renewable resource & weather")],
            spatial=SpatialCoverage(bbox=[-180, -90, 180, 90], place_labels=["Global"]),
            temporal=TemporalCoverage(
                start=datetime(1940, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC)
            ),
            quality=QualityBadges(provenance="A", documentation="A", currency="A"),
            completeness_level=3,
            anonymous_access=True,
        ),
        doc(
            id="pypsa-eur",
            iri="urn:pypsa",
            title="PyPSA-Eur grid dataset",
            summary="Pre-built OSM-derived European transmission network",
            license_id="ODbL-1.0",
            data_domains=[ConceptRef(iri="c:DD1", label="Network topology & parameters")],
            spatial=SpatialCoverage(bbox=[-11, 34, 32, 72], place_labels=["Europe"]),
            temporal=TemporalCoverage(
                start=datetime(2023, 1, 1, tzinfo=UTC), end=datetime(2024, 1, 1, tzinfo=UTC)
            ),
            quality=QualityBadges(provenance="B", documentation="B", currency="A"),
            completeness_level=2,
        ),
        doc(id="undated", iri="urn:u", title="Undated inventory", license_id="CC0-1.0"),
        doc(
            id="hidden",
            iri="urn:h",
            title="Restricted interconnection study",
            visibility="allowlisted-existence",
            entitled_principals=["alice"],
        ),
        doc(id="unpublished", iri="urn:d", title="Draft record", review_state="draft"),
    ]
    search_backend.index(docs)
    return search_backend


def search(backend, **kwargs):
    kwargs.setdefault("entitlement", Entitlement.anonymous())
    return backend.search(SearchRequest(**kwargs))


def test_draft_records_are_invisible(corpus) -> None:
    assert "unpublished" not in {h.document.id for h in search(corpus).hits}


def test_steward_sees_drafts(corpus) -> None:
    res = search(
        corpus, entitlement=Entitlement(principal_id="s", is_steward=True, include_unconfirmed=True)
    )
    assert "unpublished" in {h.document.id for h in res.hits}


def test_allowlisted_existence_hidden_from_anonymous(corpus) -> None:
    res = search(corpus)
    assert "hidden" not in {h.document.id for h in res.hits}
    assert res.total == 3


def test_allowlisted_existence_visible_to_entitled(corpus) -> None:
    res = search(corpus, entitlement=Entitlement(principal_id="alice"))
    assert "hidden" in {h.document.id for h in res.hits}
    assert res.total == 4


def test_facet_counts_exclude_invisible_records(corpus) -> None:
    """Existence must not leak through a facet count (ADR-0006)."""
    res = search(corpus, facets=("license",))
    licenses = {f.value for f in res.facets["license"]}
    assert licenses == {"Copernicus-1.2", "ODbL-1.0", "CC0-1.0"}
    assert sum(f.count for f in res.facets["license"]) == 3


def test_prefix_search_while_typing(corpus) -> None:
    for prefix in ("e", "er", "era", "era5"):
        hits = [h.document.id for h in search(corpus, q=prefix).hits]
        assert "era5" in hits, prefix


def test_exact_term_outranks_prefix_expansion(corpus) -> None:
    res = search(corpus, q="reanalysis")
    assert res.hits[0].document.id == "era5"


def test_filter_by_domain(corpus) -> None:
    res = search(corpus, filters={"data_domain": ["c:DD1"]})
    assert [h.document.id for h in res.hits] == ["pypsa-eur"]


def test_unknown_filter_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown filter field"):
        SearchRequest(entitlement=Entitlement.anonymous(), filters={"nope": ["x"]})


def test_bbox_intersects_not_contains(corpus) -> None:
    germany = BBoxFilter(5.9, 47.3, 15.0, 55.1)
    ids = {h.document.id for h in search(corpus, bbox=germany).hits}
    assert {"era5", "pypsa-eur"} <= ids


def test_bbox_absent_still_matches(corpus) -> None:
    """No declared extent means 'not captured', not 'does not overlap'."""
    ids = {h.document.id for h in search(corpus, bbox=BBoxFilter(0, 0, 1, 1)).hits}
    assert "undated" in ids


def test_temporal_overlap(corpus) -> None:
    window = RangeFilter(gte=datetime(2023, 6, 1, tzinfo=UTC), lte=datetime(2023, 7, 1, tzinfo=UTC))
    ids = {h.document.id for h in search(corpus, temporal=window).hits}
    assert {"era5", "pypsa-eur", "undated"} == ids

    old = RangeFilter(gte=datetime(1900, 1, 1, tzinfo=UTC), lte=datetime(1901, 1, 1, tzinfo=UTC))
    assert {h.document.id for h in search(corpus, temporal=old).hits} == {"undated"}


def test_sort_missing_values_last_in_both_directions(corpus) -> None:
    asc = [h.document.id for h in search(corpus, sort=(SortSpec("temporal_start"),)).hits]
    desc = [
        h.document.id
        for h in search(corpus, sort=(SortSpec("temporal_start", descending=True),)).hits
    ]
    assert asc[-1] == "undated"
    assert desc[-1] == "undated"


def test_pagination_totals_are_entitlement_scoped(corpus) -> None:
    page = search(corpus, limit=1, offset=0)
    assert page.total == 3 and len(page.hits) == 1


def test_delete_removes_from_postings(corpus) -> None:
    assert corpus.delete(["era5"]) == 1
    assert search(corpus, q="reanalysis").total == 0
    assert corpus.get("era5") is None


def test_reindex_is_idempotent(corpus) -> None:
    before = search(corpus, q="grid").total
    corpus.index([corpus.get("pypsa-eur")])
    assert search(corpus, q="grid").total == before


def test_indexing_a_concept_with_no_label_does_not_crash(search_backend) -> None:
    """`ConceptRef.label` is `str | None`, and the None case is not an edge.

    Its own docstring says so: a record can carry a concept IRI this deployment
    holds no label for — a crosswalk target, or a concept added to the
    vocabulary after the record was written — and "returning the IRI with no
    label is honest; inventing a label from the IRI's last segment is not."

    The keyword field concatenated those labels with `" ".join`, which raises
    `TypeError: sequence item 0: expected str instance, NoneType found`. Not a
    degraded index — the `index()` call itself dies, so one unlabelled concept
    anywhere in the corpus takes down the reindex that meets it.
    """
    search_backend.index(
        [
            doc(
                id="unlabelled",
                iri="urn:unlabelled",
                title="A record citing a concept we hold no label for",
                data_domains=[ConceptRef(iri="c:DD1", label="Network topology & parameters")],
                concepts=[ConceptRef(iri="c:not-in-this-vocabulary")],
            )
        ]
    )
    search_backend.refresh()

    found = search(search_backend, q="citing", limit=10)
    assert [hit.document.id for hit in found.hits] == ["unlabelled"]


def test_an_unlabelled_concept_is_skipped_rather_than_indexed_as_its_iri(search_backend) -> None:
    """The fix must not paper over the hole by indexing the IRI as text.

    `c:not-in-this-vocabulary` is not a word anyone searches for, and making it
    matchable means a query for "vocabulary" hits a record whose only connection
    to the word is an internal identifier.
    """
    search_backend.index(
        [
            doc(
                id="unlabelled",
                iri="urn:unlabelled",
                title="Quiet record",
                concepts=[ConceptRef(iri="c:not-in-this-vocabulary")],
            )
        ]
    )
    search_backend.refresh()

    found = search(search_backend, q="vocabulary", limit=10)
    assert [hit.document.id for hit in found.hits] == []


def test_a_failed_index_leaves_nothing_the_next_one_cannot_clean_up(
    search_backend, monkeypatch
) -> None:
    """An `index()` that raises must not corrupt the index it half-wrote.

    `_doc_terms` is what `_remove_postings` reads to find a document's postings
    in time proportional to that document rather than to the vocabulary. It was
    assigned *after* the posting loop finished, so a raise partway through left
    the record in `_docs` with postings in `_postings` and no ownership entry —
    and the next `index()` of the same id saw it in `_docs`, called
    `_remove_postings`, got `()` back and removed nothing. The orphaned tokens
    stayed matchable forever, so the record answered to words it no longer had.

    Reachable: `_field_text` raised `TypeError` on any concept with no label
    until the fix above. This asserts the recovery rather than that one cause.
    """
    real = type(search_backend)._field_text

    def explode_on_keywords(document, name):
        if name == "keywords":
            raise RuntimeError("a field this index cannot read")
        return real(document, name)

    monkeypatch.setattr(type(search_backend), "_field_text", staticmethod(explode_on_keywords))
    with pytest.raises(RuntimeError):
        search_backend.index(
            [doc(id="halfway", iri="urn:halfway", title="Ephemeral zzyzx placeholder")]
        )

    monkeypatch.undo()
    search_backend.index([doc(id="halfway", iri="urn:halfway", title="Corrected title")])
    search_backend.refresh()

    assert [hit.document.id for hit in search(search_backend, q="zzyzx").hits] == [], (
        "the record still answers to a word from the write that failed"
    )
    assert [hit.document.id for hit in search(search_backend, q="Corrected").hits] == ["halfway"]
