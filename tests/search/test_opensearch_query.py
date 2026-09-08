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
    Checked against `SearchDocument.model_fields` rather than a list, so the
    next field is caught by this assertion at unit-test speed and with no
    Docker.
    """
    from datahub.api.search.document import SearchDocument

    declared = set(INDEX_MAPPING["mappings"]["properties"])  # type: ignore[index]
    projected = set(SearchDocument.model_fields)

    assert not (projected - declared), (
        "projected but not declared, so `dynamic: strict` rejects every bulk write: "
        f"{sorted(projected - declared)}"
    )
    assert not (declared - projected), (
        "declared in the mapping and never projected, which is dead weight in the "
        f"index and usually a rename that only landed on one side: {sorted(declared - projected)}"
    )
