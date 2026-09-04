"""Query objects over the operational store (WP-2.3).

Every query against Postgres lives here. Not for the sake of a pattern: the
rules that matter are *conditions on rows*, and a condition written twice is a
condition that will eventually be written two ways.

Three of them in particular:

* **An allow-list entry is active only if it is unrevoked and unexpired.**
  Expressed once, in :meth:`AllowlistRepository._active`. An entitlement
  check that forgot the expiry would grant access to a principal whose grant had
  lapsed, and nothing would ever notice.
* **Re-harvest matches on ``(source_id, source_record_id)``** and nothing else
  (PRD §7.6). Matching on anything derived — a title, a URL — makes re-harvest
  create duplicates the first time a source edits a field.
* **A steward-confirmed field is never silently overwritten.** A changed source
  value under a confirmed field flags the record for re-review instead, which
  is :meth:`ReviewQueueRepository.record_conflict`.

Repositories take a Session and never open one. The transaction boundary
belongs to the caller — a harvest run that wrote raw records, relevance
decisions and a run summary must commit them together or not at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from datahub.api.models.base import utcnow
from datahub.api.models.operational import (
    AccessPlanIssue,
    AllowlistEntry,
    ApiToken,
    AuthorizationEvent,
    Custodianship,
    DistributionHealth,
    DistributionRevision,
    HarvestRun,
    HostedCopyOwner,
    Identity,
    IssueReport,
    ProbeResult,
    ProjectorState,
    RateLimitBucket,
    RawRecord,
    RelevanceDecision,
    ReviewQueueItem,
    Submission,
    User,
)
from datahub.logging import get_logger
from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.orm import Session

log = get_logger(__name__)


def affected(result: Result[Any]) -> int:
    """How many rows a DML statement touched.

    ``Session.execute`` is typed as returning ``Result``, which has no
    ``rowcount``; a DML execution actually returns a ``CursorResult``, which
    does. Narrowed once here rather than ignored at six call sites.
    """
    return cast("CursorResult[Any]", result).rowcount


class Repository[T]:
    """A typed query object over one table."""

    model: type[T]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, row_id: str) -> T | None:
        return self.session.get(self.model, row_id)

    def add(self, row: T) -> T:
        self.session.add(row)
        self.session.flush()
        return row

    def count(self, *criteria: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        if criteria:
            stmt = stmt.where(*criteria)
        return int(self.session.execute(stmt).scalar_one())

    def _all(self, stmt: Select[Any]) -> Sequence[T]:
        return self.session.execute(stmt).scalars().all()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class UserRepository(Repository[User]):
    model = User

    def by_email(self, email: str) -> User | None:
        return self.session.execute(
            select(User).where(User.email == email.lower())
        ).scalar_one_or_none()

    def by_identity(self, provider: str, subject: str) -> User | None:
        return self.session.execute(
            select(User)
            .join(Identity, Identity.user_id == User.id)
            .where(Identity.provider == provider, Identity.subject == subject)
        ).scalar_one_or_none()

    def upsert_federated(
        self,
        provider: str,
        subject: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> User:
        """Find or create the user behind a federated login.

        Matching is on ``(provider, subject)`` and never on email: an email is
        reassignable and a subject is not, so matching on email would let a
        reused address inherit the previous holder's allow-list grants.
        """
        user = self.by_identity(provider, subject)
        if user is not None:
            user.last_seen_at = utcnow()
            return user

        # The address goes on the user only if no other user holds it.
        # ``User.email`` is unique, and two people can legitimately present the
        # same verified address to two providers over time — an address is
        # reassignable. Insisting would turn that into a 500 on sign-in. The
        # address is still recorded on the Identity, which is where a
        # per-provider address belongs; the user simply has no primary one
        # until a human resolves the collision.
        primary = email.lower() if email else None
        if primary and self.by_email(primary) is not None:
            log.warning(
                "federated email already held by another user",
                provider=provider,
                email=primary,
            )
            primary = None

        user = User(email=primary, display_name=display_name)
        self.session.add(user)
        self.session.flush()
        self.session.add(Identity(user_id=user.id, provider=provider, subject=subject, email=email))
        self.session.flush()
        log.info("user created", user=user.id, provider=provider)
        return user

    def touch(self, user_id: str) -> None:
        self.session.execute(update(User).where(User.id == user_id).values(last_seen_at=utcnow()))


class ApiTokenRepository(Repository[ApiToken]):
    model = ApiToken

    def by_hash(self, token_hash: str) -> ApiToken | None:
        """Look up a presented token, and only if it is still usable.

        Revocation and expiry are conditions of the lookup rather than checks a
        caller makes afterwards, because a caller that forgets one has built an
        authentication bypass.
        """
        now = utcnow()
        return self.session.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked_at.is_(None),
                or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
            )
        ).scalar_one_or_none()

    def for_user(self, user_id: str) -> Sequence[ApiToken]:
        return self._all(
            select(ApiToken)
            .where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )

    def revoke(self, token_id: str) -> bool:
        return bool(
            affected(
                self.session.execute(
                    update(ApiToken)
                    .where(ApiToken.id == token_id, ApiToken.revoked_at.is_(None))
                    .values(revoked_at=utcnow())
                )
            )
        )

    def mark_used(self, token_id: str) -> None:
        self.session.execute(
            update(ApiToken).where(ApiToken.id == token_id).values(last_used_at=utcnow())
        )


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------


class AllowlistRepository(Repository[AllowlistEntry]):
    """The allow-list. **OpenGrid stores and enforces it; it never arbitrates
    its contents** (PRD §F8)."""

    model = AllowlistEntry

    @staticmethod
    def _active(now: datetime) -> tuple[Any, ...]:
        """What makes an entry active. Defined once and reused everywhere.

        An entitlement check that dropped the expiry clause would keep granting
        access to a principal whose grant had lapsed, and no test of the happy
        path would catch it.
        """
        return (
            AllowlistEntry.revoked_at.is_(None),
            or_(AllowlistEntry.expires_at.is_(None), AllowlistEntry.expires_at > now),
        )

    def is_allowed(self, dataset_id: str, principal_id: str | None, email: str | None) -> bool:
        if principal_id is None and email is None:
            return False
        identity = [
            c
            for c in (
                AllowlistEntry.principal_id == principal_id if principal_id else None,
                AllowlistEntry.principal_email == email.lower() if email else None,
            )
            if c is not None
        ]
        stmt = select(AllowlistEntry.id).where(
            AllowlistEntry.dataset_id == dataset_id,
            or_(*identity),
            *self._active(utcnow()),
        )
        return self.session.execute(stmt.limit(1)).scalar_one_or_none() is not None

    def datasets_for(self, principal_id: str | None, email: str | None) -> list[str]:
        """Every dataset a principal is allow-listed on.

        Returned as a list so entitlement can be **compiled into the SPARQL
        query** as a VALUES clause rather than applied to its results
        (ADR-0006). Post-filtering leaks existence through result counts, and a
        count is enough to confirm that a dataset exists.
        """
        if principal_id is None and email is None:
            return []
        identity = [
            c
            for c in (
                AllowlistEntry.principal_id == principal_id if principal_id else None,
                AllowlistEntry.principal_email == email.lower() if email else None,
            )
            if c is not None
        ]
        rows = self.session.execute(
            select(AllowlistEntry.dataset_id)
            .where(or_(*identity), *self._active(utcnow()))
            .distinct()
        ).scalars()
        return sorted(rows)

    def entitled_principals(self, dataset_id: str) -> list[str]:
        """The identifiers of everyone currently allow-listed on a dataset.

        User ids and email addresses in one list, because the index matches a
        caller against it by either: a principal who was granted access by
        address before they had an account must still match after they sign in,
        and re-resolving the grant at index time would silently drop them until
        someone re-saved the record.

        Projected into the search index rather than looked up per hit, because
        entitlement is compiled into the query (ADR-0006) — a lookup per result
        turns a filter over a page into a page of round trips.
        """
        rows = self.session.execute(
            select(AllowlistEntry.principal_id, AllowlistEntry.principal_email).where(
                AllowlistEntry.dataset_id == dataset_id, *self._active(utcnow())
            )
        ).all()
        return sorted({value for row in rows for value in row if value})

    def principals_for(self, dataset_id: str) -> Sequence[AllowlistEntry]:
        return self._all(
            select(AllowlistEntry)
            .where(AllowlistEntry.dataset_id == dataset_id, *self._active(utcnow()))
            .order_by(AllowlistEntry.created_at)
        )

    def grant(
        self,
        dataset_id: str,
        *,
        granted_by: str,
        principal_id: str | None = None,
        principal_email: str | None = None,
        expires_at: datetime | None = None,
        note: str | None = None,
    ) -> AllowlistEntry:
        entry = AllowlistEntry(
            dataset_id=dataset_id,
            principal_id=principal_id,
            principal_email=principal_email.lower() if principal_email else None,
            granted_by=granted_by,
            expires_at=expires_at,
            note=note,
        )
        return self.add(entry)

    def revoke(self, dataset_id: str, principal_id: str) -> bool:
        return bool(
            affected(
                self.session.execute(
                    update(AllowlistEntry)
                    .where(
                        AllowlistEntry.dataset_id == dataset_id,
                        AllowlistEntry.principal_id == principal_id,
                        AllowlistEntry.revoked_at.is_(None),
                    )
                    .values(revoked_at=utcnow())
                )
            )
        )


class CustodianshipRepository(Repository[Custodianship]):
    model = Custodianship

    def for_dataset(self, dataset_id: str) -> Sequence[Custodianship]:
        return self._all(select(Custodianship).where(Custodianship.dataset_id == dataset_id))

    def may_manage(self, dataset_id: str, user_id: str) -> bool:
        return (
            self.session.execute(
                select(Custodianship.id)
                .where(Custodianship.dataset_id == dataset_id, Custodianship.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def contacts_for(self, dataset_id: str) -> list[str]:
        """Who to tell when a link dies. Empty is a real answer, and the caller
        has to handle it: an unowned dataset with a dead link is a fact for the
        operations dashboard, not an exception."""
        rows = self.session.execute(
            select(Custodianship.contact_email).where(
                Custodianship.dataset_id == dataset_id,
                Custodianship.contact_email.is_not(None),
            )
        ).scalars()
        return sorted({r for r in rows if r})


class HostedCopyRepository(Repository[HostedCopyOwner]):
    """PRD §F2: *every hosted copy has a named refresh owner assigned before
    launch, not after.*"""

    model = HostedCopyOwner

    def owner_of(self, distribution_id: str) -> HostedCopyOwner | None:
        return self.session.execute(
            select(HostedCopyOwner).where(HostedCopyOwner.distribution_id == distribution_id)
        ).scalar_one_or_none()

    def has_owner(self, distribution_id: str) -> bool:
        return self.owner_of(distribution_id) is not None

    def overdue(self, *, now: datetime | None = None) -> Sequence[HostedCopyOwner]:
        return self._all(
            select(HostedCopyOwner)
            .where(HostedCopyOwner.next_refresh_due < (now or utcnow()))
            .order_by(HostedCopyOwner.next_refresh_due)
        )


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


class HarvestRunRepository(Repository[HarvestRun]):
    model = HarvestRun

    def start(self, source_id: str, adapter: str, *, limit: int | None = None) -> HarvestRun:
        return self.add(
            HarvestRun(source_id=source_id, adapter=adapter, state="running", limit_applied=limit)
        )

    def finish(
        self,
        run: HarvestRun,
        *,
        state: str = "succeeded",
        checkpoint: dict[str, Any] | None = None,
        errors: Iterable[str] = (),
    ) -> HarvestRun:
        detail = list(errors)
        run.finished_at = utcnow()
        run.state = state
        run.checkpoint = checkpoint
        run.error_detail = detail
        run.errors = len(detail)
        self.session.flush()
        return run

    def last_successful(self, source_id: str) -> HarvestRun | None:
        return self.session.execute(
            select(HarvestRun)
            .where(HarvestRun.source_id == source_id, HarvestRun.state == "succeeded")
            .order_by(HarvestRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def resume_point(self, source_id: str) -> dict[str, Any] | None:
        """Where to pick up. A failed run's checkpoint is as good as a
        successful one's — better, in fact, since a failed run is exactly the
        case resumption exists for."""
        run = self.session.execute(
            select(HarvestRun)
            .where(HarvestRun.source_id == source_id, HarvestRun.checkpoint.is_not(None))
            .order_by(HarvestRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return run.checkpoint if run else None

    def recent(self, *, limit: int = 20, source_id: str | None = None) -> Sequence[HarvestRun]:
        stmt = select(HarvestRun).order_by(HarvestRun.started_at.desc()).limit(limit)
        if source_id:
            stmt = stmt.where(HarvestRun.source_id == source_id)
        return self._all(stmt)

    def stale_running(self, *, older_than: timedelta = timedelta(hours=6)) -> Sequence[HarvestRun]:
        """Runs still marked running long after they should have finished.

        A process killed mid-harvest leaves its row at ``running`` for ever,
        and a scheduler that skips sources with a run in flight would then
        never harvest that source again.
        """
        cutoff = utcnow() - older_than
        return self._all(
            select(HarvestRun).where(HarvestRun.state == "running", HarvestRun.started_at < cutoff)
        )


@dataclass(slots=True)
class RawRecordUpsert:
    row: RawRecord
    created: bool
    changed: bool

    @property
    def needs_processing(self) -> bool:
        """An unchanged payload short-circuits normalise, enrich and validate.

        This is most of what makes a daily re-harvest of 2,100 records cheap.
        """
        return self.created or self.changed


class RawRecordRepository(Repository[RawRecord]):
    model = RawRecord

    def by_source_record(self, source_id: str, source_record_id: str) -> RawRecord | None:
        return self.session.execute(
            select(RawRecord).where(
                RawRecord.source_id == source_id,
                RawRecord.source_record_id == source_record_id,
            )
        ).scalar_one_or_none()

    def upsert(
        self,
        *,
        source_id: str,
        source_record_id: str,
        payload: dict[str, Any],
        payload_hash: str,
        run_id: str | None = None,
        source_url: str | None = None,
        fetched_at: datetime | None = None,
    ) -> RawRecordUpsert:
        """Store a harvested payload, matching on ``(source, source id)``.

        The match key is the adapter's stable id and nothing derived from the
        content (PRD §7.6). Matching on a title or a URL would create a
        duplicate record the first time a source corrected a typo.
        """
        existing = self.by_source_record(source_id, source_record_id)
        if existing is None:
            row = RawRecord(
                source_id=source_id,
                source_record_id=source_record_id,
                payload=payload,
                payload_hash=payload_hash,
                run_id=run_id,
                source_url=source_url,
                fetched_at=fetched_at or utcnow(),
            )
            self.session.add(row)
            self.session.flush()
            return RawRecordUpsert(row, created=True, changed=True)

        changed = existing.payload_hash != payload_hash
        # fetched_at moves even when nothing changed: "we looked and it was the
        # same" is a different fact from "we have not looked since March", and
        # staleness reporting needs to tell them apart.
        existing.fetched_at = fetched_at or utcnow()
        existing.run_id = run_id or existing.run_id
        if changed:
            existing.payload = payload
            existing.payload_hash = payload_hash
            existing.source_url = source_url
        self.session.flush()
        return RawRecordUpsert(existing, created=False, changed=changed)

    def for_source(self, source_id: str, *, limit: int | None = None) -> Sequence[RawRecord]:
        stmt = select(RawRecord).where(RawRecord.source_id == source_id).order_by(RawRecord.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return self._all(stmt)


class RelevanceRepository(Repository[RelevanceDecision]):
    """PRD §7.2: *log every rejection with its reason so recall can be
    audited.* Without the rejections the filter is unfalsifiable."""

    model = RelevanceDecision

    def record(
        self,
        *,
        raw_record_id: str,
        source_id: str,
        accepted: bool,
        stage: str,
        reason: str,
        score: float | None = None,
        matched_terms: Sequence[str] = (),
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> RelevanceDecision:
        return self.add(
            RelevanceDecision(
                raw_record_id=raw_record_id,
                source_id=source_id,
                accepted=accepted,
                stage=stage,
                reason=reason,
                score=score,
                matched_terms=list(matched_terms),
                model=model,
                prompt_version=prompt_version,
            )
        )

    def rejections(
        self, *, source_id: str | None = None, stage: str | None = None, limit: int = 100
    ) -> Sequence[RelevanceDecision]:
        """The audit surface. A wrongly excluded dataset is invisible unless
        somebody can read the rejections."""
        stmt = (
            select(RelevanceDecision)
            .where(RelevanceDecision.accepted.is_(False))
            .order_by(RelevanceDecision.decided_at.desc())
            .limit(limit)
        )
        if source_id:
            stmt = stmt.where(RelevanceDecision.source_id == source_id)
        if stage:
            stmt = stmt.where(RelevanceDecision.stage == stage)
        return self._all(stmt)

    def rates(self) -> dict[str, dict[str, int]]:
        """Accept and reject counts per stage, for the operations dashboard."""
        rows = self.session.execute(
            select(
                RelevanceDecision.stage,
                RelevanceDecision.accepted,
                func.count().label("n"),
            ).group_by(RelevanceDecision.stage, RelevanceDecision.accepted)
        ).all()
        out: dict[str, dict[str, int]] = {}
        for stage, accepted, n in rows:
            bucket = out.setdefault(stage, {"accepted": 0, "rejected": 0})
            bucket["accepted" if accepted else "rejected"] = int(n)
        return out


class ReviewQueueRepository(Repository[ReviewQueueItem]):
    """The steward's work list (PRD §7.6)."""

    model = ReviewQueueItem

    def by_dataset(self, dataset_id: str) -> ReviewQueueItem | None:
        return self.session.execute(
            select(ReviewQueueItem).where(ReviewQueueItem.dataset_id == dataset_id)
        ).scalar_one_or_none()

    def enqueue(
        self,
        dataset_id: str,
        *,
        source_id: str | None = None,
        raw_record_id: str | None = None,
        data_domain: str | None = None,
        completeness_level: int = 1,
        validation_conforms: bool = False,
        violations: Sequence[Any] = (),
    ) -> ReviewQueueItem:
        """Add or refresh a queue entry, leaving steward state alone.

        A re-harvest must not reset ``state``, ``confirmed_fields`` or the
        steward's notes. Those are the reviewer's work, and losing them is how a
        queue teaches stewards not to trust it.
        """
        item = self.by_dataset(dataset_id)
        if item is None:
            item = ReviewQueueItem(dataset_id=dataset_id)
            self.session.add(item)
        item.source_id = source_id or item.source_id
        item.raw_record_id = raw_record_id or item.raw_record_id
        item.data_domain = data_domain or item.data_domain
        item.completeness_level = completeness_level
        item.validation_conforms = validation_conforms
        item.violations = list(violations)
        self.session.flush()
        return item

    def next_batch(
        self,
        *,
        limit: int = 25,
        state: str = "draft",
        data_domain: str | None = None,
    ) -> Sequence[ReviewQueueItem]:
        """Highest-leverage records first: most inbound links, then most
        complete. A record twelve others cite is worth reviewing before one
        nothing points at."""
        stmt = (
            select(ReviewQueueItem)
            .where(ReviewQueueItem.state == state)
            .order_by(
                ReviewQueueItem.inbound_link_count.desc(),
                ReviewQueueItem.completeness_level.desc(),
                ReviewQueueItem.created_at,
            )
            .limit(limit)
        )
        if data_domain:
            stmt = stmt.where(ReviewQueueItem.data_domain == data_domain)
        return self._all(stmt)

    def confirm(
        self,
        dataset_id: str,
        *,
        reviewed_by: str,
        confirmed_fields: Sequence[str] = (),
        notes: str | None = None,
    ) -> ReviewQueueItem | None:
        item = self.by_dataset(dataset_id)
        if item is None:
            return None
        item.state = "confirmed"
        item.reviewed_by = reviewed_by
        item.reviewed_at = utcnow()
        item.steward_notes = notes or item.steward_notes
        # Union, not replacement: a second review confirming three more fields
        # must not un-confirm the first review's work.
        item.confirmed_fields = sorted({*item.confirmed_fields, *confirmed_fields})
        item.conflict_detail = []
        self.session.flush()
        return item

    def record_conflict(
        self, dataset_id: str, conflicts: Sequence[dict[str, Any]]
    ) -> ReviewQueueItem | None:
        """A re-harvest changed a value a steward had confirmed.

        PRD §7.6 is explicit that this flags for re-review rather than
        overwriting. The steward's confirmation is a human judgement about a
        source that has since changed its mind; silently taking the new value
        would discard the judgement and never say so.
        """
        item = self.by_dataset(dataset_id)
        if item is None or not conflicts:
            return item
        item.state = "flagged"
        item.conflict_detail = [*item.conflict_detail, *conflicts]
        self.session.flush()
        log.warning(
            "confirmed field changed at source",
            dataset=dataset_id,
            fields=[c.get("field") for c in conflicts],
        )
        return item

    def set_inbound_links(self, counts: dict[str, int]) -> int:
        """Refresh the priority signal after a link recompute."""
        updated = 0
        for dataset_id, count in counts.items():
            updated += affected(
                self.session.execute(
                    update(ReviewQueueItem)
                    .where(ReviewQueueItem.dataset_id == dataset_id)
                    .values(inbound_link_count=count)
                )
            )
        return updated

    def counts_by_state(self) -> dict[str, int]:
        rows = self.session.execute(
            select(ReviewQueueItem.state, func.count()).group_by(ReviewQueueItem.state)
        ).all()
        return {state: int(n) for state, n in rows}


# ---------------------------------------------------------------------------
# Custodial history and link health
# ---------------------------------------------------------------------------


class RevisionRepository(Repository[DistributionRevision]):
    """PRD §F1.11. **Provenance is never silently rewritten** — a stable 3xx
    updates the stored URL and leaves the old value readable here."""

    model = DistributionRevision

    def record(
        self,
        *,
        distribution_id: str,
        dataset_id: str,
        field: str,
        old_value: str | None,
        new_value: str | None,
        source: str,
        actor: str | None = None,
        automated: bool = True,
        detail: str | None = None,
    ) -> DistributionRevision | None:
        """Write a revision, unless nothing actually changed.

        A no-op revision is worse than none: it pads the history a steward
        reads when deciding whether a distribution has been stable.
        """
        if old_value == new_value:
            return None
        return self.add(
            DistributionRevision(
                distribution_id=distribution_id,
                dataset_id=dataset_id,
                field=field,
                old_value=old_value,
                new_value=new_value,
                source=source,
                actor=actor,
                automated=automated,
                detail=detail,
            )
        )

    def history(self, distribution_id: str, *, limit: int = 50) -> Sequence[DistributionRevision]:
        return self._all(
            select(DistributionRevision)
            .where(DistributionRevision.distribution_id == distribution_id)
            .order_by(DistributionRevision.changed_at.desc())
            .limit(limit)
        )

    def for_dataset(self, dataset_id: str, *, limit: int = 50) -> Sequence[DistributionRevision]:
        return self._all(
            select(DistributionRevision)
            .where(DistributionRevision.dataset_id == dataset_id)
            .order_by(DistributionRevision.changed_at.desc())
            .limit(limit)
        )


class ProbeRepository(Repository[ProbeResult]):
    model = ProbeResult

    def record(self, result: ProbeResult) -> ProbeResult:
        return self.add(result)

    def history(self, distribution_id: str, *, limit: int = 20) -> Sequence[ProbeResult]:
        return self._all(
            select(ProbeResult)
            .where(ProbeResult.distribution_id == distribution_id)
            .order_by(ProbeResult.probed_at.desc())
            .limit(limit)
        )

    def prune(self, *, keep_days: int = 90) -> int:
        """Probe history is append-only and high-volume; without pruning it is
        the largest table in the system within a year and answers no question
        that the rollup does not."""
        cutoff = utcnow() - timedelta(days=keep_days)
        return affected(
            self.session.execute(delete(ProbeResult).where(ProbeResult.probed_at < cutoff))
        )


class HealthRepository(Repository[DistributionHealth]):
    """Current link health, kept alongside the history so neither the prober
    nor the API has to aggregate on every call."""

    model = DistributionHealth

    #: PRD §F1: APIs daily, bulk files weekly, tier 3 monthly.
    CADENCE_S: dict[str, int] = {
        "api": 86_400,
        "bulk": 604_800,
        "reference": 2_592_000,
    }

    def for_distribution(self, distribution_id: str) -> DistributionHealth | None:
        return self.session.get(DistributionHealth, distribution_id)

    def apply(self, probe: ProbeResult) -> DistributionHealth:
        """Fold one probe into the current health.

        ``consecutive_failures`` is the field the escalation policy reads, so it
        resets on any success and increments on anything else. A ``redirected``
        result counts as a success — the resource is there, at a new address,
        and treating a working redirect as a failure would eventually exclude a
        live dataset from access plans.
        """
        row = self.for_distribution(probe.distribution_id)
        if row is None:
            row = DistributionHealth(
                distribution_id=probe.distribution_id, dataset_id=probe.dataset_id
            )
            self.session.add(row)
            # Flushed before its own columns are read: `default=` is applied at
            # INSERT, so on an unflushed row `consecutive_failures` is None and
            # the increment below raises rather than counting.
            self.session.flush()

        succeeded = probe.status in ("verified", "redirected")
        row.status = probe.status
        row.last_probed_at = probe.probed_at
        row.consecutive_failures = 0 if succeeded else row.consecutive_failures + 1
        if succeeded:
            row.last_success_at = probe.probed_at
        row.next_probe_due = probe.probed_at + timedelta(seconds=row.probe_cadence_s)
        self.session.flush()
        return row

    def due(self, *, now: datetime | None = None, limit: int = 500) -> Sequence[DistributionHealth]:
        moment = now or utcnow()
        return self._all(
            select(DistributionHealth)
            .where(
                or_(
                    DistributionHealth.next_probe_due.is_(None),
                    DistributionHealth.next_probe_due <= moment,
                )
            )
            .order_by(DistributionHealth.next_probe_due)
            .limit(limit)
        )

    def unhealthy(self, *, min_failures: int = 3) -> Sequence[DistributionHealth]:
        return self._all(
            select(DistributionHealth)
            .where(DistributionHealth.consecutive_failures >= min_failures)
            .order_by(DistributionHealth.consecutive_failures.desc())
        )

    def set_cadence(self, distribution_id: str, kind: str) -> None:
        row = self.for_distribution(distribution_id)
        if row is None:
            return
        row.probe_cadence_s = self.CADENCE_S.get(kind, self.CADENCE_S["bulk"])
        self.session.flush()

    def exclude_from_plans(self, distribution_id: str, *, excluded: bool = True) -> None:
        row = self.for_distribution(distribution_id)
        if row is None:
            return
        row.excluded_from_plans = excluded
        self.session.flush()


# ---------------------------------------------------------------------------
# Inbound from users
# ---------------------------------------------------------------------------


class SubmissionRepository(Repository[Submission]):
    """PRD §F3. Fire-and-forget: receipt is confirmed, no status is tracked
    back to the submitter."""

    model = Submission

    def pending(self, *, limit: int = 50) -> Sequence[Submission]:
        return self._all(
            select(Submission)
            .where(Submission.state == "received")
            .order_by(Submission.created_at)
            .limit(limit)
        )

    def triage(self, submission_id: str, *, state: str, actor: str) -> Submission | None:
        row = self.get(submission_id)
        if row is None:
            return None
        row.state = state
        row.triaged_by = actor
        row.triaged_at = utcnow()
        self.session.flush()
        return row


class IssueReportRepository(Repository[IssueReport]):
    model = IssueReport

    def open_for(self, dataset_id: str) -> Sequence[IssueReport]:
        return self._all(
            select(IssueReport)
            .where(IssueReport.dataset_id == dataset_id, IssueReport.state == "open")
            .order_by(IssueReport.created_at.desc())
        )

    def counts_by_target(self, dataset_id: str) -> dict[str | None, int]:
        """Reports are grouped rather than deduped, so a target flagged eleven
        times reads as eleven (PRD §12.11 carries the choice forward)."""
        rows = self.session.execute(
            select(IssueReport.target_id, func.count())
            .where(IssueReport.dataset_id == dataset_id, IssueReport.state == "open")
            .group_by(IssueReport.target_id)
        ).all()
        return {target: int(n) for target, n in rows}

    def resolve(
        self, report_id: str, *, resolution: str, actor: str, state: str = "resolved"
    ) -> IssueReport | None:
        row = self.get(report_id)
        if row is None:
            return None
        row.state = state
        row.resolution = resolution
        row.resolved_by = actor
        row.resolved_at = utcnow()
        self.session.flush()
        return row


# ---------------------------------------------------------------------------
# Audit and operations
# ---------------------------------------------------------------------------


class AuditRepository(Repository[AuthorizationEvent]):
    """Every grant and refusal (PRD §F9, §F10).

    Also the only place that distinguishes a 404 meaning "absent" from a 404
    meaning "you may not know this exists". The caller cannot tell them apart —
    that is the point — so the audit log must.
    """

    model = AuthorizationEvent

    def record(
        self,
        *,
        action: str,
        outcome: str,
        resource_kind: str,
        resource_id: str | None = None,
        principal_id: str | None = None,
        principal_kind: str = "user",
        reason: str | None = None,
        masked_as: str | None = None,
        client: str | None = None,
        tool_name: str | None = None,
    ) -> AuthorizationEvent:
        return self.add(
            AuthorizationEvent(
                action=action,
                outcome=outcome,
                resource_kind=resource_kind,
                resource_id=resource_id,
                principal_id=principal_id,
                principal_kind=principal_kind,
                reason=reason,
                masked_as=masked_as,
                client=client,
                tool_name=tool_name,
            )
        )

    def refusals(self, *, limit: int = 100) -> Sequence[AuthorizationEvent]:
        return self._all(
            select(AuthorizationEvent)
            .where(AuthorizationEvent.outcome == "refused")
            .order_by(AuthorizationEvent.occurred_at.desc())
            .limit(limit)
        )

    def for_principal(self, principal_id: str, *, limit: int = 100) -> Sequence[AuthorizationEvent]:
        return self._all(
            select(AuthorizationEvent)
            .where(AuthorizationEvent.principal_id == principal_id)
            .order_by(AuthorizationEvent.occurred_at.desc())
            .limit(limit)
        )


class AccessPlanRepository(Repository[AccessPlanIssue]):
    model = AccessPlanIssue

    def issue(
        self,
        *,
        dataset_id: str,
        distribution_id: str,
        mode: str,
        ttl: timedelta,
        principal_id: str | None = None,
        client: str | None = None,
        slice_spec: dict[str, Any] | None = None,
    ) -> AccessPlanIssue:
        now = utcnow()
        return self.add(
            AccessPlanIssue(
                dataset_id=dataset_id,
                distribution_id=distribution_id,
                mode=mode,
                issued_at=now,
                expires_at=now + ttl,
                principal_id=principal_id,
                client=client,
                slice_spec=slice_spec,
            )
        )

    def active_for(self, principal_id: str) -> Sequence[AccessPlanIssue]:
        now = utcnow()
        return self._all(
            select(AccessPlanIssue).where(
                AccessPlanIssue.principal_id == principal_id,
                AccessPlanIssue.revoked_at.is_(None),
                AccessPlanIssue.expires_at > now,
            )
        )

    def revoke_for(self, dataset_id: str, principal_id: str) -> int:
        """Revoke a principal's outstanding plans for one dataset.

        PRD §12.9 leaves open whether removal from an allow-list revokes plans
        already issued or lets them expire. The default is expiry; this method
        exists so the other choice is a call site rather than a migration.
        """
        return affected(
            self.session.execute(
                update(AccessPlanIssue)
                .where(
                    AccessPlanIssue.dataset_id == dataset_id,
                    AccessPlanIssue.principal_id == principal_id,
                    AccessPlanIssue.revoked_at.is_(None),
                    AccessPlanIssue.expires_at > utcnow(),
                )
                .values(revoked_at=utcnow())
            )
        )


class RateLimitRepository(Repository[RateLimitBucket]):
    """Fixed-window counters. Redis in production; this is the durable fallback
    so a single-process deployment still enforces limits."""

    model = RateLimitBucket

    def hit(self, key: str, *, window_s: int, limit: int) -> tuple[bool, int]:
        """Count one request. Returns ``(allowed, count_after)``.

        Fixed windows rather than a sliding log: a sliding window needs a row
        per request, and the fallback limiter must not cost more than the thing
        it is protecting.
        """
        now = utcnow()
        start = datetime.fromtimestamp((int(now.timestamp()) // window_s) * window_s, tz=UTC)
        bucket = self.session.get(RateLimitBucket, (key, start))
        if bucket is None:
            bucket = RateLimitBucket(key=key, window_start=start, count=0)
            self.session.add(bucket)
        bucket.count += 1
        self.session.flush()
        return bucket.count <= limit, bucket.count

    def prune(self, *, older_than: timedelta = timedelta(days=1)) -> int:
        cutoff = utcnow() - older_than
        return affected(
            self.session.execute(
                delete(RateLimitBucket).where(RateLimitBucket.window_start < cutoff)
            )
        )


class ProjectorStateRepository(Repository[ProjectorState]):
    """One row, so projector lag is a queryable fact rather than a guess."""

    model = ProjectorState

    def current(self) -> ProjectorState:
        row = self.session.get(ProjectorState, 1)
        if row is None:
            row = ProjectorState(id=1)
            self.session.add(row)
            self.session.flush()
        return row

    def mark_indexed(
        self, *, pending: int = 0, error: str | None = None, full_reindex: bool = False
    ) -> ProjectorState:
        row = self.current()
        row.last_indexed_at = utcnow()
        row.pending_count = pending
        row.last_error = error
        if full_reindex:
            # Recorded on the same call rather than a separate one, so a
            # rebuild cannot update the index and forget to say it rebuilt —
            # which would leave `last_full_reindex_at` reading as "never" on a
            # system that reindexes nightly.
            row.last_full_reindex_at = row.last_indexed_at
        self.session.flush()
        return row

    def mark_commit(self) -> ProjectorState:
        row = self.current()
        row.last_commit_at = utcnow()
        row.pending_count += 1
        self.session.flush()
        return row

    def lag_seconds(self) -> float | None:
        """How far behind the index is. ``None`` means nothing has been
        committed yet, which is not the same as being up to date."""
        row = self.current()
        if row.last_commit_at is None:
            return None
        if row.last_indexed_at is None:
            return (utcnow() - row.last_commit_at).total_seconds()
        return max(0.0, (row.last_commit_at - row.last_indexed_at).total_seconds())


@dataclass(slots=True)
class Repositories:
    """Every repository over one session.

    A convenience for call sites that need several — a harvest run touches four
    — and the place that makes the transaction boundary obvious: they all share
    one session, so they all commit together.
    """

    session: Session

    @property
    def users(self) -> UserRepository:
        return UserRepository(self.session)

    @property
    def tokens(self) -> ApiTokenRepository:
        return ApiTokenRepository(self.session)

    @property
    def allowlist(self) -> AllowlistRepository:
        return AllowlistRepository(self.session)

    @property
    def custodians(self) -> CustodianshipRepository:
        return CustodianshipRepository(self.session)

    @property
    def hosted(self) -> HostedCopyRepository:
        return HostedCopyRepository(self.session)

    @property
    def runs(self) -> HarvestRunRepository:
        return HarvestRunRepository(self.session)

    @property
    def raw(self) -> RawRecordRepository:
        return RawRecordRepository(self.session)

    @property
    def relevance(self) -> RelevanceRepository:
        return RelevanceRepository(self.session)

    @property
    def review(self) -> ReviewQueueRepository:
        return ReviewQueueRepository(self.session)

    @property
    def revisions(self) -> RevisionRepository:
        return RevisionRepository(self.session)

    @property
    def probes(self) -> ProbeRepository:
        return ProbeRepository(self.session)

    @property
    def health(self) -> HealthRepository:
        return HealthRepository(self.session)

    @property
    def submissions(self) -> SubmissionRepository:
        return SubmissionRepository(self.session)

    @property
    def reports(self) -> IssueReportRepository:
        return IssueReportRepository(self.session)

    @property
    def audit(self) -> AuditRepository:
        return AuditRepository(self.session)

    @property
    def plans(self) -> AccessPlanRepository:
        return AccessPlanRepository(self.session)

    @property
    def limits(self) -> RateLimitRepository:
        return RateLimitRepository(self.session)

    @property
    def projector(self) -> ProjectorStateRepository:
        return ProjectorStateRepository(self.session)


__all__ = [
    "AccessPlanRepository",
    "AllowlistRepository",
    "ApiTokenRepository",
    "AuditRepository",
    "CustodianshipRepository",
    "HarvestRunRepository",
    "HealthRepository",
    "HostedCopyRepository",
    "IssueReportRepository",
    "ProbeRepository",
    "ProjectorStateRepository",
    "RateLimitRepository",
    "RawRecordRepository",
    "RawRecordUpsert",
    "RelevanceRepository",
    "Repositories",
    "Repository",
    "ReviewQueueRepository",
    "RevisionRepository",
    "SubmissionRepository",
    "UserRepository",
    "affected",
]
