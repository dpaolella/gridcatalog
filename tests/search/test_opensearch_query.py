"""The OpenSearch request body, built without an OpenSearch.

`build_query` is a pure function from a `SearchRequest` to a request body, and
that is worth testing here rather than only in the container-backed job: the
job takes three minutes to start its services, and a wrong field path is
visible in the body itself. What the container job adds is proof that
OpenSearch *accepts* the body, which is a different claim and is why
`tests/parity` still asserts the sort end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import Entitlement, SearchRequest, SortSpec
from datahub.api.search.document import SORT_FIELDS
from datahub.api.search.opensearch_backend import INDEX_MAPPING, build_query


def _body(**kwargs: object) -> dict:
    return build_query(SearchRequest(entitlement=Entitlement.anonymous(), **kwargs))  # type: ignore[arg-type]


def test_sorting_by_title_uses_the_keyword_subfield() -> None:
    """`title` is analysed text, and an analysed field cannot be sorted.

    OpenSearch refuses with "Fielddata is disabled on text fields by default",
    which reached the caller as a 500 — from a parameter the API documents and
    the UI now sends. The mapping has carried `title.raw` all along, and the
    default sort already used it; only the explicit sort resolved through
    `SORT_FIELDS`, which is the in-memory backend's document path and knows
    nothing about sub-fields.
    """
    body = _body(sort=(SortSpec(field="title"),))
    assert body["sort"] == [{"title.raw": {"order": "asc", "missing": "_last"}}]


def test_the_default_sort_and_an_explicit_one_agree() -> None:
    """They disagreed, which is how this survived: the default was right."""
    assert _body()["sort"] == _body(sort=(SortSpec(field="title"),))["sort"]


def test_every_sortable_field_resolves_to_something_the_mapping_can_sort() -> None:
    """The general form of the bug: a `SORT_FIELDS` entry naming an analysed
    field is a 500 waiting for the first caller to ask for it. Checked against
    the mapping rather than against a list, so a field added as `text` later is
    caught here instead of in production."""
    properties = INDEX_MAPPING["mappings"]["properties"]  # type: ignore[index]

    unsortable = []
    for field in SORT_FIELDS:
        path = _body(sort=(SortSpec(field=field),))["sort"][0]
        (resolved,) = path
        root, _, sub = resolved.partition(".")
        spec = properties.get(root, {})
        if sub:
            spec = spec.get("fields", {}).get(sub) or spec.get("properties", {}).get(sub, {})
        if spec.get("type") == "text":
            unsortable.append((field, resolved))

    assert not unsortable, f"sort fields resolving to an analysed text field: {unsortable}"


def test_every_projected_field_is_declared_in_the_mapping() -> None:
    """`dynamic: strict` means an undeclared field fails the whole bulk write.

    Not the one field, and not the one document — the entire batch. So adding
    something to `SearchDocument` without adding it here takes the index down
    for every record, and it surfaces as `strict_dynamic_mapping_exception` in
    a container-backed test rather than anywhere near the change that caused it.

    That has now happened twice. `og:usageEvidence` added `usage_evidence`,
    `usage_evidence_count` and `has_usage_evidence` to the projected document
    and not to the mapping, and CI's Integration job was red across five runs.
    Checked against what `to_source` actually emits rather than against a
    list, so the next field is caught by this assertion at unit-test speed and
    with no Docker.
    """
    from datahub.api.search.document import SearchDocument
    from datahub.api.search.opensearch_backend import to_source

    declared = set(INDEX_MAPPING["mappings"]["properties"])  # type: ignore[index]
    # What the write path actually sends, not what the model declares. The two
    # differ by design: `to_source` derives fields the document does not store,
    # and checking `model_fields` would call one of them dead weight while
    # missing the day a derived field is added without a mapping — which is the
    # failure this test exists for, in the direction it actually arrives from.
    projected = set(to_source(SearchDocument(id="probe", iri="urn:probe", title="Probe")))

    assert not (projected - declared), (
        "projected but not declared, so `dynamic: strict` rejects every bulk write: "
        f"{sorted(projected - declared)}"
    )
    assert not (declared - projected), (
        "declared in the mapping and never projected, which is dead weight in the "
        f"index and usually a rename that only landed on one side: {sorted(declared - projected)}"
    )


def test_an_indexed_document_reads_back_as_the_document_that_was_written() -> None:
    """`from_source` undoes `to_source`, including the fields it derives.

    The index carries values the model does not have — `spatial.envelope` for
    geo queries, `domain_coverage` for the catalog's crossing — and
    `SearchDocument` forbids extras, so anything the write path adds and the
    read path does not remove makes *every* read from OpenSearch raise.

    That is not hypothetical. The envelope was stripped on read because it was
    written and stripped in the same change; `domain_coverage` was added later
    with only the write half, and six integration tests failed on
    `ValidationError: domain_coverage — Extra inputs are not permitted` for four
    commits. Nothing else caught it: the in-memory backend round-trips through
    JSONL and never sees `to_source`, so the entire non-integration suite, the
    browser suite and the static export were green throughout.

    Round-tripped here rather than asserted against a list of field names, so
    the check is the property itself and the next derived field is covered
    without anyone remembering to add it.
    """
    from datahub.api.search.document import SearchDocument, SpatialCoverage
    from datahub.api.search.opensearch_backend import from_source, to_source

    document = SearchDocument(
        id="probe",
        iri="urn:probe",
        title="Probe",
        completeness_level=2,
        spatial=SpatialCoverage(bbox=[-8.0, 49.9, 1.8, 58.7]),
    )

    source = to_source(document)
    assert "domain_coverage" in source, "the crossing is what the catalog aggregates on"
    assert "envelope" in source["spatial"], "the envelope is what a bbox query matches"

    assert from_source(source) == document
    # And the inverse must not mutate what it was handed: the search path reads
    # every hit out of one response body, and popping from it in place would
    # corrupt the rest of the page.
    assert "domain_coverage" in source


def test_the_opensearch_backend_reads_back_what_it_wrote() -> None:
    """`get` and `search` reconstruct a document from what `index` stored.

    The round-trip above covers the two functions; this covers the two callers,
    which is where it actually broke. Both read paths stripped the geo envelope
    by hand and neither knew about `domain_coverage`, so every query against a
    real index raised — and the only thing exercising those lines needed three
    containers, so it stayed broken across four commits while everything a
    developer runs locally stayed green.

    A stub client rather than a container: what is being checked here is this
    module's own translation between the model and the index, which is
    deterministic and needs no server. What genuinely needs OpenSearch — that
    the query bodies mean what we think, that the mapping accepts the writes —
    stays in the integration job.
    """
    from datahub.api.search.document import SearchDocument, SpatialCoverage
    from datahub.api.search.opensearch_backend import OpenSearchBackend

    class StubClient:
        """Enough of the client to store a `_source` and hand it back."""

        def __init__(self) -> None:
            self.stored: dict[str, dict] = {}

        # `index()` goes through `opensearchpy.helpers.bulk`, which is not
        # installed as a stub here; the backend's own `_source` construction is
        # what this exercises, so the action list is replayed directly.
        def get(self, index: str, id: str, ignore: list[int] | None = None) -> dict:
            source = self.stored.get(id)
            return {"found": source is not None, "_source": source or {}}

        def search(self, index: str, body: dict) -> dict:
            hits = [{"_source": source, "_score": 1.0} for source in self.stored.values()]
            return {
                "hits": {"total": {"value": len(hits)}, "hits": hits},
                "aggregations": {},
            }

    from datahub.api.search.opensearch_backend import to_source

    document = SearchDocument(
        id="probe",
        iri="urn:probe",
        title="Probe",
        completeness_level=3,
        spatial=SpatialCoverage(bbox=[-8.0, 49.9, 1.8, 58.7]),
    )
    client = StubClient()
    client.stored[document.id] = to_source(document)

    backend = OpenSearchBackend("http://stub", "probe-index", client=client)

    assert backend.get("probe") == document
    assert backend.get("absent") is None

    from datahub.api.search.backend import Entitlement, SearchRequest

    response = backend.search(SearchRequest(entitlement=Entitlement.anonymous()))
    assert [hit.document for hit in response.hits] == [document]
