"""Resolving a record as the caller is allowed to see it.

One implementation, deliberately. ADR-0006 puts entitlement into query
construction rather than into a filter applied afterwards, and the property
that buys — that a caller cannot distinguish "withheld" from "absent" — only
holds if *every* handler resolves records the same way.

It did not. `routers/datasets.py` went through here; `routers/allowlists.py`
resolved straight off the graph with `records.exists()` and raised a 404 before
entitlement was considered at all, then a 403 once it was. Two distinguishable
refusals is an existence oracle, which for an allow-listed-existence record is
the whole of the secret.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from datahub.api.search.query import build_for_ids
from datahub.errors import NotFound

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from datahub.api.entitlement import Caller
    from datahub.api.search.backend import SearchBackend
    from datahub.api.search.document import SearchDocument


def absent(dataset_id: str) -> NotFound:
    """The same 404 whether the record is missing or withheld.

    A 403 would say "this exists and you cannot have it", which on a record
    whose existence is the restricted part *is* the disclosure. The audit log
    distinguishes the two; the caller cannot.
    """
    return NotFound(f"no dataset {dataset_id!r}", dataset_id=dataset_id)


def entitled_document(
    dataset_id: str, caller: Caller, backend: SearchBackend
) -> tuple[SearchDocument, bool]:
    """Fetch a record through the entitlement predicate.

    Through the index rather than the graph, because the index is where the
    predicate is compiled. Reading the graph first and checking afterwards
    would be the post-filter ADR-0006 forbids, and the check would live in
    every handler rather than in one place.

    Returns the document and whether the caller may see its full metadata; a
    record the caller may not see at all raises :func:`absent`.
    """
    slug = dataset_id.rsplit("/", 1)[-1]
    response = backend.search(build_for_ids((slug,), caller.entitlement))
    if not response.hits:
        raise absent(dataset_id)
    hit = response.hits[0]
    return hit.document, hit.full_metadata
