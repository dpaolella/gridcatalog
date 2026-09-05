"""``/v1/review`` — the steward queue (WP-9.5).

PRD §7.6. The queue is ordered so that **high-leverage records get reviewed
first**: most inbound links, then most complete. A record twelve others cite is
worth a steward's hour before one nothing points at.

**Steward only, and the refusal is a 403 rather than a 404.** Everywhere else
in this API a caller who may not see something is told the thing does not
exist, because knowing it exists is itself a disclosure. Here it is not: the
existence of a review queue is public knowledge, the queue's *contents* are
not, and a 403 tells an honest steward with the wrong session what to do about
it. A 404 would send them looking for a typo.

**Confirming is not editing.** A steward confirms fields; the record itself is
edited through the catalog. That separation is what lets re-harvest work: a
changed source value under a confirmed field flags the record for re-review
rather than overwriting a person's decision.
"""

from __future__ import annotations

from typing import Annotated

from datahub.api.deps import CallerDep, SessionDep
from datahub.api.models.repositories import Repositories
from datahub.api.schemas import ReviewConfirm, ReviewItem, ReviewQueueResponse
from datahub.errors import NotAuthenticated, NotEntitled, NotFound
from datahub.logging import get_logger
from fastapi import APIRouter, Path, Query

log = get_logger(__name__)

router = APIRouter(prefix="/review", tags=["review"])


def _steward(caller: CallerDep, session: SessionDep) -> None:
    if session is None:
        raise NotAuthenticated("the review store is unreachable")
    if caller.is_anonymous:
        raise NotAuthenticated("sign in as a steward to see the review queue")
    if not caller.entitlement.is_steward:
        raise NotEntitled(
            "the review queue is for stewards. This is a 403 rather than a 404 because the "
            "queue's existence is not a secret — only its contents are, and a steward with "
            "the wrong session should be told to change sessions, not to check for a typo."
        )


@router.get("", response_model=ReviewQueueResponse, summary="Records awaiting review")
def queue(
    caller: CallerDep,
    session: SessionDep,
    state: Annotated[str, Query(description="draft, in-review, confirmed or flagged.")] = "draft",
    data_domain: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ReviewQueueResponse:
    """The next batch, highest leverage first."""
    _steward(caller, session)
    repos = Repositories(session)
    items = repos.review.next_batch(limit=limit, state=state, data_domain=data_domain)
    return ReviewQueueResponse(
        state=state,
        items=[ReviewItem.from_row(item) for item in items],
        total=len(items),
    )


@router.post(
    "/{dataset_id}/confirm",
    response_model=ReviewItem,
    summary="Confirm a record, and the fields you checked",
)
def confirm(
    dataset_id: Annotated[str, Path()],
    body: ReviewConfirm,
    caller: CallerDep,
    session: SessionDep,
) -> ReviewItem:
    """Mark a record reviewed.

    ``confirmed_fields`` is a union with whatever was confirmed before, not a
    replacement: a second review confirming three more fields must not
    un-confirm the first review's work, and a steward who reviewed one tab
    should not have to re-check the others to avoid losing them.
    """
    _steward(caller, session)
    repos = Repositories(session)
    item = repos.review.confirm(
        dataset_id,
        reviewed_by=caller.principal_id or "unknown",
        confirmed_fields=body.confirmed_fields,
        notes=body.notes,
    )
    if item is None:
        raise NotFound(f"no review item for {dataset_id!r}", dataset_id=dataset_id)

    repos.audit.record(
        action="review.confirm",
        outcome="granted",
        resource_kind="dataset",
        resource_id=dataset_id,
        principal_id=caller.principal_id,
        reason=f"{len(body.confirmed_fields)} field(s) confirmed",
    )
    log.info("record confirmed", dataset=dataset_id, steward=caller.principal_id)
    return ReviewItem.from_row(item)
