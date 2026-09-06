"""``/v1/allowlists/{datasetId}`` — the custodian API (WP-6.2).

PRD §F8, stated twice because it is the thing custodians most often assume
otherwise:

> **The dataset creator manages the allow-list. OpenGrid stores and enforces it
> and never arbitrates its contents.**

So there is no approval step, no review queue for grants, and no OpenGrid
opinion about who should be on a list. What OpenGrid does is enforce whatever
is there — at discovery time and again at plan issuance, per PRD §F10.

**A PUT of the whole list, not a patch.** A diff-based API makes "who can see
this" a question you answer by replaying a history, and the one question a
custodian actually asks is "who is on it right now". Replacing the list makes
that question a GET.

**Only a custodian.** Not a steward, not an admin — the list belongs to the
dataset's custodian, and an admin who could edit it would be arbitrating its
contents, which is exactly what the PRD forbids. An admin can change *who the
custodian is*; that is a different power and it leaves a different audit trail.
"""

from __future__ import annotations

from typing import Annotated, Any

from datahub.api.deps import CallerDep, RecordsDep, SearchDep, SessionDep
from datahub.api.entitlement import Caller, tokens
from datahub.api.entitlement.visibility import absent, entitled_document
from datahub.api.models.repositories import Repositories, audit_out_of_band
from datahub.api.schemas import AllowlistEntryModel, AllowlistResponse, AllowlistUpdate
from datahub.errors import NotAuthenticated, NotEntitled, NotFound
from datahub.logging import get_logger
from fastapi import APIRouter, Path

log = get_logger(__name__)

router = APIRouter(tags=["allowlists"])

DatasetId = Annotated[str, Path(description="The dataset's slug.")]


@router.get(
    "/allowlists/{dataset_id}",
    response_model=AllowlistResponse,
    summary="Who may see this dataset. Custodian only.",
)
def get_allowlist(
    dataset_id: DatasetId,
    caller: CallerDep,
    session: SessionDep,
    records: RecordsDep,
    backend: SearchDep,
) -> AllowlistResponse:
    iri = _custodian_check(dataset_id, caller, session, records, backend)
    entries = Repositories(session).allowlist.principals_for(iri)
    return AllowlistResponse(
        dataset_id=dataset_id,
        entries=[
            AllowlistEntryModel(
                principal_id=row.principal_id,
                principal_email=row.principal_email,
                note=row.note,
                expires_at=row.expires_at,
            )
            for row in entries
        ],
    )


@router.put(
    "/allowlists/{dataset_id}",
    response_model=AllowlistResponse,
    summary="Replace the allow-list. Custodian only.",
)
def put_allowlist(
    dataset_id: DatasetId,
    body: AllowlistUpdate,
    caller: CallerDep,
    session: SessionDep,
    records: RecordsDep,
    backend: SearchDep,
) -> AllowlistResponse:
    """Replace the whole list.

    Revocations happen before grants. If both orders were possible, replacing a
    list that grants and revokes the same principal would depend on dict
    ordering — and the safe resolution of that ambiguity is "revoked", not
    "granted".
    """
    iri = _custodian_check(dataset_id, caller, session, records, backend)
    repos = Repositories(session)

    wanted = {_key(entry): entry for entry in body.entries if _key(entry)}
    current = {
        _key(
            AllowlistEntryModel(principal_id=row.principal_id, principal_email=row.principal_email)
        ): row
        for row in repos.allowlist.principals_for(iri)
    }

    removed = 0
    for key, row in current.items():
        if key not in wanted and row.principal_id:
            repos.allowlist.revoke(iri, row.principal_id)
            removed += 1

    added = 0
    for key, entry in wanted.items():
        if key in current:
            continue
        repos.allowlist.grant(
            iri,
            granted_by=caller.principal_id or "unknown",
            principal_id=entry.principal_id,
            principal_email=entry.principal_email,
            expires_at=entry.expires_at,
            note=entry.note,
        )
        added += 1

    repos.audit.record(
        action="allowlist.replace",
        outcome="granted",
        resource_kind="dataset",
        resource_id=iri,
        principal_id=caller.principal_id,
        reason=f"{added} added, {removed} revoked",
    )
    log.info("allow-list replaced", dataset=iri, added=added, removed=removed)
    session.flush()

    # The index carries entitled principals, so a grant that never reaches it
    # is a grant that does not work. Reprojecting here rather than waiting for
    # the next reindex: a custodian who adds a colleague expects them to be
    # able to search for the dataset immediately.
    _reproject(iri, records, session)

    return get_allowlist(dataset_id, caller, session, records, backend)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _custodian_check(
    dataset_id: str, caller: Caller, session: Any, records: RecordsDep, backend: SearchDep
) -> str:
    """Resolve the dataset and confirm the caller is its custodian.

    Not a steward, not an admin. The list belongs to the dataset's custodian,
    and an admin who could edit it would be arbitrating its contents — exactly
    what PRD §F8 forbids.

    The record is resolved from the graph, not through the entitlement
    predicate, and that is deliberate: a custodian must be able to manage the
    list of a record whose existence is restricted, and such a record is
    invisible to the predicate until somebody is on the list. Resolving through
    it locked custodians out of exactly the datasets the endpoint exists for.

    What entitlement decides here is the *shape of the refusal*. This used to
    raise 404 for a record that was not there and 403 for one that was but was
    not yours, so any signed-in stranger could tell the two apart — an existence
    oracle for precisely the records whose existence is the secret (PRD §F7,
    ADR-0006). A caller who may not know the record exists now gets the same 404
    as for one that does not.

    For a record whose existence *is* public, 403 remains the honest answer: it
    discloses nothing the catalog does not already publish, and it tells a
    mistyping custodian something useful.
    """
    if session is None:
        raise NotAuthenticated("the allow-list store is unreachable")
    if caller.is_anonymous:
        raise NotAuthenticated("sign in as the dataset's custodian")
    tokens.require_scope(caller, "custodian:manage")

    iri = _iri(dataset_id, records)
    repos = Repositories(session)
    if repos.custodians.may_manage(iri, caller.principal_id):
        return iri

    # Also allow the principal the record itself names as custodian, so a
    # dataset whose custodianship has not been mirrored into the operational
    # store is still manageable by the person the catalog says owns it.
    from datahub.graph.records import dataset_node

    try:
        named = dataset_node(records.get(iri)).get("custodian")
    except Exception:
        named = None
    if named and (named == caller.principal_id or named in caller.entitlement.custodian_of):
        return iri

    # Out of band: this function is about to raise, and an exception rolls the
    # request's transaction back — so an audit row written on that session
    # would disappear along with the refusal it records.
    audit_out_of_band(
        action="allowlist.read",
        outcome="refused",
        resource_kind="dataset",
        resource_id=iri,
        principal_id=caller.principal_id,
        reason="not the custodian",
    )
    if not _may_know_it_exists(dataset_id, caller, backend):
        # Same answer as for a record that is not there. The audit row above
        # distinguishes the two; the caller cannot.
        raise absent(dataset_id)

    raise NotEntitled(
        "only this dataset's custodian may see or change its allow-list",
        dataset_id=dataset_id,
    )


def _iri(dataset_id: str, records: RecordsDep) -> str:
    """Resolve the slug against the graph, 404 if there is no such record."""
    iri = str(records._iri(dataset_id))
    if not records.exists(iri):
        raise absent(dataset_id)
    return iri


def _may_know_it_exists(dataset_id: str, caller: Caller, backend: SearchDep) -> bool:
    """Whether this caller is allowed to learn that the record is there at all.

    Through the index, because that is where the entitlement predicate is
    compiled (ADR-0006). Used only to choose between two refusals — never to
    grant anything.
    """
    try:
        entitled_document(dataset_id, caller, backend)
    except NotFound:
        return False
    return True


def _key(entry: AllowlistEntryModel) -> str | None:
    if entry.principal_id:
        return f"id:{entry.principal_id}"
    if entry.principal_email:
        return f"email:{entry.principal_email.lower()}"
    return None


def _reproject(iri: str, records: RecordsDep, session: Any) -> None:
    """Push the change into the search index, on this request's own session.

    The session matters twice over. It must exist, because
    ``entitled_principals`` is read from the operational store during
    projection — a projector built without one writes a document with an empty
    allow-list, and the grant just recorded silently does not work. And it must
    be *this* session: opening a second one blocks on the write lock this
    request is holding, which on SQLite is "database is locked" and on
    PostgreSQL is a stall until the statement timeout.

    Never fatal: a grant that is recorded but not yet indexed becomes visible
    at the next reindex, whereas refusing the write because the index is down
    would lose it entirely.
    """
    from contextlib import nullcontext

    try:
        from datahub.api.deps import search_backend
        from datahub.projector import Projector

        Projector(records, search_backend(), session_factory=lambda: nullcontext(session)).project(
            iri
        )
    except Exception as exc:
        log.warning("allow-list change not yet indexed", dataset=iri, error=str(exc))
