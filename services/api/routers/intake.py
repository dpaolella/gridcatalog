"""``/v1/submissions`` and ``/v1/reports`` — inbound from users (WP-4.4).

PRD §F3. Two forms, both deliberately low-friction and both anonymous-capable,
because the person who notices a broken link or a missing dataset is usually
not the person with an account.

**Submissions are fire-and-forget.** The PRD is explicit: receipt is confirmed
and no status is tracked back to the submitter. That is a choice about what
this system promises — a status field implies an SLA on triage, and a stale
"received" badge three months later is worse than saying up front that we will
look at it.

**Reports capture the exact thing flagged.** A report against a record is much
harder to act on than one against a field or a distribution, so the target is
recorded structurally rather than left in prose.
"""

from __future__ import annotations

import hashlib
from typing import Any

from datahub.api.deps import CallerDep, SessionDep, SettingsDep
from datahub.api.entitlement import tokens
from datahub.api.models.operational import IssueReport, Submission
from datahub.api.models.repositories import Repositories
from datahub.api.schemas import (
    ReportReceipt,
    ReportRequest,
    SubmissionReceipt,
    SubmissionRequest,
)
from datahub.errors import DataHubError, RateLimited
from datahub.logging import get_logger
from fastapi import APIRouter, Request, status

log = get_logger(__name__)

router = APIRouter(tags=["intake"])

#: Per-hour caps per client, for the anonymous case. Generous enough that a
#: person filing several genuine reports in a sitting is unaffected, low enough
#: that an unattended script is not free.
SUBMISSION_LIMIT = 20
REPORT_LIMIT = 60
WINDOW_S = 3600


class IntakeUnavailable(DataHubError):
    """The operational store is unreachable, so the form cannot be accepted.

    A 503 rather than a cheerful receipt: telling someone their submission was
    received when it was dropped is worse than telling them to try later.
    """

    status_code = 503
    code = "intake_unavailable"


@router.post(
    "/submissions",
    response_model=SubmissionReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Tell us about a dataset we do not have",
)
def create_submission(
    payload: SubmissionRequest,
    caller: CallerDep,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> SubmissionReceipt:
    """Accept an intake form.

    202 rather than 201: nothing has been created that the submitter can go and
    look at, and saying "created" would imply otherwise. What has happened is
    that the form is in a queue a human reads.
    """
    # Anonymous is allowed here by design (PRD §F3: no login required), but a
    # caller who chose to present a narrower token is held to it.
    tokens.require_scope(caller, "catalog:write", allow_anonymous=True)
    _require(session)
    _rate_limit(session, request, "submission", SUBMISSION_LIMIT)

    repos = Repositories(session)
    row = repos.submissions.add(
        Submission(
            title=payload.title,
            description=payload.description,
            originator=payload.originator,
            data_domain=payload.data_domain,
            license_text=payload.license_text,
            submitter_contact=payload.submitter_contact,
            access_urls=[str(u) for u in payload.access_urls],
            format_hint=payload.format_hint,
            approximate_size=payload.approximate_size,
            update_cadence=payload.update_cadence,
            documentation_urls=[str(u) for u in payload.documentation_urls],
            ip_hash=_client_hash(request, settings),
        )
    )
    log.info("submission received", submission=row.id, principal=caller.principal_id)

    return SubmissionReceipt(
        id=row.id,
        received_at=row.created_at,
        message=(
            "Received. A steward will triage it. We do not track submission status back to "
            "you — if it is accepted it will appear in the catalog, and you are welcome to "
            "search for it."
        ),
    )


@router.post(
    "/reports",
    response_model=ReportReceipt,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Report a problem with a record, a field or a link",
)
def create_report(
    payload: ReportRequest,
    caller: CallerDep,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> ReportReceipt:
    """Accept an issue report.

    Anonymous is allowed and is the common case: the person who notices that a
    download 404s is whoever tried to download it. Requiring an account here
    would mean the reports that matter most never arrive.
    """
    # Anonymous is allowed here by design (PRD §F3: no login required), but a
    # caller who chose to present a narrower token is held to it.
    tokens.require_scope(caller, "catalog:write", allow_anonymous=True)
    _require(session)
    _rate_limit(session, request, "report", REPORT_LIMIT)

    repos = Repositories(session)
    row = repos.reports.add(
        IssueReport(
            dataset_id=payload.dataset_id,
            target_kind=payload.target_kind,
            target_id=payload.target_id,
            issue_type=payload.issue_type,
            comment=payload.comment,
            reporter_user_id=caller.principal_id,
            reporter_contact=payload.reporter_contact,
            ip_hash=_client_hash(request, settings),
        )
    )
    # Reports against the same target are grouped rather than deduped, so a
    # target flagged eleven times reads as eleven (PRD §12.11 carries the
    # surface-vs-dedupe choice forward).
    open_count = repos.reports.counts_by_target(payload.dataset_id).get(payload.target_id, 1)
    log.info("issue reported", report=row.id, dataset=payload.dataset_id, kind=payload.issue_type)

    return ReportReceipt(
        id=row.id,
        received_at=row.created_at,
        dataset_id=payload.dataset_id,
        open_reports_on_target=open_count,
        message=(
            "Thank you. A steward reviews reports against the records they affect. "
            "Broken-link reports also feed the automated prober."
        ),
    )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _require(session: Any) -> None:
    if session is None:
        raise IntakeUnavailable(
            "the intake store is unreachable; nothing was recorded. Please try again later."
        )


def _rate_limit(session: Any, request: Request, kind: str, limit: int) -> None:
    """A per-client hourly cap on anonymous writes.

    Fixed-window counters in the operational store rather than Redis, because
    this is the fallback path and it must not need a second service to work. A
    limiter that is itself unavailable does not block the write: the point is
    to stop an unattended script, not to be a gate that fails closed on a
    person filing a genuine report.
    """
    key = f"intake:{kind}:{_client_key(request)}"
    try:
        allowed, count = Repositories(session).limits.hit(key, window_s=WINDOW_S, limit=limit)
    except Exception as exc:
        log.warning("rate limiter unavailable", error=str(exc))
        return
    if not allowed:
        raise RateLimited(
            f"too many {kind}s from this client in the last hour",
            limit=limit,
            window_seconds=WINDOW_S,
            observed=count,
        )


def _client_key(request: Request) -> str:
    caller = getattr(request.state, "caller", None)
    if caller is not None and caller.principal_id:
        return f"user:{caller.principal_id}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _client_hash(request: Request, settings: Any) -> str | None:
    """A hashed client address.

    Hashed, not stored raw: the requirement is to distinguish clients — to spot
    a flood, to group a person's reports — not to retain addresses. Keyed with
    the deployment's secret so the hashes are not comparable across
    deployments and a leaked table is not a list of who reported what.
    """
    if request.client is None:
        return None
    return hashlib.sha256(f"{settings.secret_key}:{request.client.host}".encode()).hexdigest()
