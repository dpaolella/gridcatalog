"""A reader must be able to find the records that have a schema (#46).

"Show me the datasets I can actually interpret" is the first question a
modeller asks a catalog of 444 records, and no control answered it.
Completeness level is the wrong instrument in both directions:

    level 1  437     no field metadata *by definition* (PRD 6)
    level 2    2     every field carries localName, label, definition,
                     dataType and valueBasis -- a probe supplies three of
                     those five and cannot supply the other two
    level 3    5

So a record with 273 genuinely probed fields sits at level 1, indistinguishable
through every control the site offers from the 410 records with none.
`field_count` was projected and indexed from M4 and appeared in no facet, range
or sort map, so nothing could ask for it -- the same defect as `og:caveat`
(#55) and `spatialResolutionInMeters` (#53).
"""

from __future__ import annotations

import pytest
from datahub.api.search.document import FACET_FIELDS, RANGE_FIELDS, SORT_FIELDS, range_path
from datahub.projector.build import _field_count_bucket


@pytest.mark.parametrize(
    ("count", "bucket"),
    [(0, "none"), (1, "1-9"), (9, "1-9"), (10, "10-49"), (49, "10-49"), (50, "50+"), (273, "50+")],
)
def test_bucket_boundaries(count: int, bucket: str) -> None:
    assert _field_count_bucket(count) == bucket


def test_field_count_is_reachable_as_a_facet_a_range_and_a_sort() -> None:
    """The defect was reachability, so this is the assertion that states it."""
    assert "field_count_bucket" in FACET_FIELDS
    assert "field_count" in RANGE_FIELDS
    assert "field_count" in SORT_FIELDS
    assert range_path("field_count") == "field_count"


def test_the_bucket_is_a_keyword_in_the_opensearch_mapping() -> None:
    """A field the projector writes and the mapping does not declare fails the
    *whole* bulk write under `dynamic: strict`, not just that field. This is the
    class of bug #67 was filed for and the reason it is asserted per field."""
    from datahub.api.search.opensearch_backend import INDEX_MAPPING

    properties = INDEX_MAPPING["mappings"]["properties"]
    assert properties["field_count_bucket"]["type"] == "keyword"
    assert properties["field_count"]["type"] == "integer"


def test_completeness_level_cannot_substitute_for_it() -> None:
    """The misreading the issue exists to correct, pinned as an executable claim.

    If these two ever became equivalent the facet would be redundant. They are
    not: a probed record reaches a high field count and stays at level 1,
    because level 2 needs a definition and a value basis on *every* field.
    """
    from datahub.api.search.document import SearchDocument

    probed = SearchDocument(
        id="probed",
        iri="https://example.invalid/probed",
        title="A probed record",
        completeness_level=1,
        field_count=273,
        field_count_bucket=_field_count_bucket(273),
    )
    bare = SearchDocument(
        id="bare",
        iri="https://example.invalid/bare",
        title="A record with no schema",
        completeness_level=1,
        field_count=0,
        field_count_bucket=_field_count_bucket(0),
    )
    assert probed.completeness_level == bare.completeness_level
    assert probed.field_count_bucket != bare.field_count_bucket, (
        "the two records the reader most needs to tell apart are still identical "
        "through every control the site offers"
    )


def test_a_negative_field_count_minimum_is_a_400_not_a_500() -> None:
    from datahub.api.search.backend import Entitlement
    from datahub.api.search.query import BadSearchRequest, SearchParams, build

    with pytest.raises(BadSearchRequest):
        build(SearchParams(field_count_min=-1), Entitlement())
