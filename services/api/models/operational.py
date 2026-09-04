"""Operational tables. Nothing here is a catalog record.

Table groups, and why each is here rather than in the graph:

* **Identity** — users, identities, sessions, API tokens. Meaningless as RDF.
* **Entitlement** — allow-lists and custodianship. Enforced per request; the
  *fact* that a dataset is allow-listed is in the graph, the membership is not.
* **Acquisition** — harvest runs, raw records, rejections, review queue. Process
  state, not published metadata. The rejection log is what makes recall
  auditable (PRD §7.2).
* **Custodial** — distribution revision history and probe history. High-churn
  append-only history about how a record changed.
* **Inbound** — submissions and issue reports from users.
* **Audit** — authorisation grants and refusals, access plans issued.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from datahub.api.models.base import Base, IdMixin, TimestampMixin, utcnow
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    #: `user`, `steward`, `admin`. A steward may read drafts and confirm records;
    #: only an admin may change roles.
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: True for principals representing an automated client rather than a person.
    #: Agent traffic is several times chattier, so it is rate-limited separately.
    is_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column()

    identities: Mapped[list[Identity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tokens: Mapped[list[ApiToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("role in ('user','steward','admin')", name="role_known"),)


class Identity(Base, IdMixin, TimestampMixin):
    """A federated login. One user may have several (PRD §F10)."""

    __tablename__ = "identities"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)  # github|google|microsoft|local
    subject: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    #: Only set for provider='local'; federated identities never store a secret.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    user: Mapped[User] = relationship(back_populates="identities")

    __table_args__ = (UniqueConstraint("provider", "subject", name="provider_subject"),)


class Session(Base, IdMixin, TimestampMixin):
    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    revoked_at: Mapped[datetime | None] = mapped_column()
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    """Hashed, not stored raw: the audit requirement is to distinguish clients,
    not to retain addresses."""


class ApiToken(Base, IdMixin, TimestampMixin):
    """A bearer token for SDK and MCP callers. Only the hash is stored."""

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    """First characters of the token, shown in the UI so a user can identify
    which token to revoke without the token itself being recoverable."""
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    last_used_at: Mapped[datetime | None] = mapped_column()

    user: Mapped[User] = relationship(back_populates="tokens")


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------


class AllowlistEntry(Base, IdMixin, TimestampMixin):
    """One principal permitted on one restricted dataset.

    **The dataset creator manages this list. OpenGrid stores and enforces it and
    never arbitrates its contents** (PRD §F8). The custodian column records who
    may edit; the row itself is never an OpenGrid decision.
    """

    __tablename__ = "allowlist_entries"

    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    #: Either a user id, or an email for a principal who has not yet signed in.
    principal_id: Mapped[str | None] = mapped_column(String(64), index=True)
    principal_email: Mapped[str | None] = mapped_column(String(320), index=True)
    granted_by: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint("dataset_id", "principal_id", name="dataset_principal"),
        Index("ix_allowlist_active", "dataset_id", "revoked_at"),
        CheckConstraint(
            "principal_id is not null or principal_email is not null",
            name="principal_identified",
        ),
    )


class Custodianship(Base, IdMixin, TimestampMixin):
    """Who owns a dataset's allow-list, and who to notify when a link dies."""

    __tablename__ = "custodianships"

    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    organisation: Mapped[str | None] = mapped_column(String(200))
    #: Whether this custodian may delegate management to another principal.
    #: PRD §12.10 carries the delegation-and-audit question forward; until it is
    #: decided this defaults false and no code path sets it true.
    may_delegate: Mapped[bool] = mapped_column(Boolean, default=False)
    delegated_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("dataset_id", "user_id", name="dataset_custodian"),)


class HostedCopyOwner(Base, IdMixin, TimestampMixin):
    """The named refresh owner for a distribution OpenGrid hosts.

    PRD §F2: *every hosted copy has a named refresh owner assigned before
    launch, not after* — the documented failure mode in every comparable
    project. A hosted distribution with no row here cannot be published, which
    is why this is a table and not a note in a runbook.
    """

    __tablename__ = "hosted_copy_owners"

    distribution_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    owner_contact: Mapped[str] = mapped_column(String(320))
    hosting_reason: Mapped[str] = mapped_column(String(32))
    """Which of the four exceptions in PRD §F2 justifies hosting:
    ``format`` (a), ``fragile-source`` (b), ``charges-user`` (c), ``etl-output`` (d)."""
    refresh_cadence: Mapped[str | None] = mapped_column(String(64))
    last_refreshed_at: Mapped[datetime | None] = mapped_column()
    next_refresh_due: Mapped[datetime | None] = mapped_column(index=True)

    __table_args__ = (
        CheckConstraint(
            "hosting_reason in ('format','fragile-source','charges-user','etl-output')",
            name="hosting_reason_known",
        ),
    )


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------


class HarvestRun(Base, IdMixin):
    """One execution of one adapter. Counts, errors and duration (PRD §7.1)."""

    __tablename__ = "harvest_runs"

    source_id: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column()
    state: Mapped[str] = mapped_column(String(16), default="running", index=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    records_flagged: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[list[Any]] = mapped_column(JSON, default=list)
    #: Opaque per-adapter resume token, so a long harvest survives a restart.
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    limit_applied: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "state in ('running','succeeded','failed','cancelled')", name="run_state_known"
        ),
        Index("ix_harvest_runs_source_started", "source_id", "started_at"),
    )


class RawRecord(Base, IdMixin, TimestampMixin):
    """Source-native metadata as harvested, before normalisation.

    Kept so a normaliser bug can be fixed and replayed without re-crawling a
    third party, and so a steward can see what the source actually said.
    """

    __tablename__ = "raw_records"

    source_id: Mapped[str] = mapped_column(String(64), index=True)
    source_record_id: Mapped[str] = mapped_column(String(500), index=True)
    """The adapter's stable id. Re-harvest matches on it, which is what makes
    re-harvest idempotent (PRD §7.6)."""
    run_id: Mapped[str | None] = mapped_column(ForeignKey("harvest_runs.id", ondelete="SET NULL"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    """Content hash. An unchanged hash short-circuits the whole pipeline."""
    source_url: Mapped[str | None] = mapped_column(String(2000))
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (UniqueConstraint("source_id", "source_record_id", name="source_record"),)


class RelevanceDecision(Base, IdMixin):
    """Every filter decision, accept and reject alike.

    PRD §7.2: *log every rejection with its reason so recall can be audited.*
    Without this table the filter is unfalsifiable — a wrongly excluded dataset
    is invisible by construction.
    """

    __tablename__ = "relevance_decisions"

    raw_record_id: Mapped[str] = mapped_column(
        ForeignKey("raw_records.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    decided_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    accepted: Mapped[bool] = mapped_column(Boolean, index=True)
    stage: Mapped[str] = mapped_column(String(16))  # keyword | vocabulary | llm
    reason: Mapped[str] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    matched_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        CheckConstraint("stage in ('keyword','vocabulary','llm')", name="stage_known"),
    )


class ReviewQueueItem(Base, IdMixin, TimestampMixin):
    """A record awaiting steward confirmation (PRD §7.6).

    Sorted by domain and by inbound link count, so high-leverage records get
    reviewed first.
    """

    __tablename__ = "review_queue"

    dataset_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    raw_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("raw_records.id", ondelete="SET NULL")
    )
    source_id: Mapped[str | None] = mapped_column(String(64), index=True)
    data_domain: Mapped[str | None] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    completeness_level: Mapped[int] = mapped_column(Integer, default=1)
    inbound_link_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    validation_conforms: Mapped[bool] = mapped_column(Boolean, default=False)
    violations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column()
    steward_notes: Mapped[str | None] = mapped_column(Text)
    #: Field paths a steward has confirmed. Re-harvest must not overwrite these
    #: silently; a changed source value flags the record for re-review instead
    #: (PRD §7.6).
    confirmed_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Set when re-harvest found a source change under a confirmed field.
    conflict_detail: Mapped[list[Any]] = mapped_column(JSON, default=list)

    __table_args__ = (
        CheckConstraint(
            "state in ('draft','in-review','confirmed','flagged')", name="review_state_known"
        ),
        CheckConstraint("completeness_level between 1 and 3", name="level_in_range"),
        Index("ix_review_priority", "state", "data_domain", "inbound_link_count"),
    )


# ---------------------------------------------------------------------------
# Custodial history
# ---------------------------------------------------------------------------


class DistributionRevision(Base, IdMixin):
    """Revision history per distribution URL (PRD §F1.11).

    A stable 3xx auto-updates the stored URL and writes a row here.
    **Provenance is never silently rewritten** — the old value stays readable.
    """

    __tablename__ = "distribution_revisions"

    distribution_id: Mapped[str] = mapped_column(String(255), index=True)
    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32))  # probe | harvest | steward | report
    automated: Mapped[bool] = mapped_column(Boolean, default=True)
    actor: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "source in ('probe','harvest','steward','report','migration')",
            name="revision_source_known",
        ),
        Index("ix_revision_dist_time", "distribution_id", "changed_at"),
    )


class ProbeResult(Base, IdMixin):
    """One link-health probe. HEAD or a single-byte range, never a download."""

    __tablename__ = "probe_results"

    distribution_id: Mapped[str] = mapped_column(String(255), index=True)
    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    probed_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(8), default="HEAD")
    redirect_target: Mapped[str | None] = mapped_column(String(2000))
    redirect_permanent: Mapped[bool] = mapped_column(Boolean, default=False)
    content_length: Mapped[int | None] = mapped_column(Integer)
    supports_range: Mapped[bool | None] = mapped_column(Boolean)
    cors_enabled: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status in ('verified','degraded','unreachable','redirected')",
            name="probe_status_known",
        ),
        Index("ix_probe_dist_time", "distribution_id", "probed_at"),
    )


class DistributionHealth(Base, TimestampMixin):
    """Current health, kept alongside the history so the prober does not have to
    aggregate on every run and the API does not have to on every read."""

    __tablename__ = "distribution_health"

    distribution_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), default="verified", index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_probed_at: Mapped[datetime | None] = mapped_column(index=True)
    last_success_at: Mapped[datetime | None] = mapped_column()
    next_probe_due: Mapped[datetime | None] = mapped_column(index=True)
    probe_cadence_s: Mapped[int] = mapped_column(Integer, default=604800)
    """Defaults per PRD §F1: APIs daily, bulk files weekly, Tier 3 monthly."""
    excluded_from_plans: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    custodian_notified_at: Mapped[datetime | None] = mapped_column()


# ---------------------------------------------------------------------------
# Inbound from users
# ---------------------------------------------------------------------------


class Submission(Base, IdMixin, TimestampMixin):
    """Data intake form (PRD §F3).

    Fire-and-forget by design: receipt is confirmed, no status is tracked back
    to the submitter.
    """

    __tablename__ = "submissions"

    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text)
    originator: Mapped[str | None] = mapped_column(String(300))
    data_domain: Mapped[str | None] = mapped_column(String(16), index=True)
    license_text: Mapped[str] = mapped_column(String(300))
    submitter_contact: Mapped[str | None] = mapped_column(String(320))
    access_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    format_hint: Mapped[str | None] = mapped_column(String(120))
    approximate_size: Mapped[str | None] = mapped_column(String(64))
    update_cadence: Mapped[str | None] = mapped_column(String(64))
    documentation_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(16), default="received", index=True)
    triaged_by: Mapped[str | None] = mapped_column(String(64))
    triaged_at: Mapped[datetime | None] = mapped_column()
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        CheckConstraint(
            "state in ('received','triaged','accepted','declined','duplicate')",
            name="submission_state_known",
        ),
    )


class IssueReport(Base, IdMixin, TimestampMixin):
    """Report an issue, against a record, a field or a distribution (PRD §F3).

    Anonymous reports are allowed; ``reporter_user_id`` is null then.
    """

    __tablename__ = "issue_reports"

    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    #: The exact thing flagged, auto-captured: a field IRI, a distribution id,
    #: or null for the record as a whole.
    target_kind: Mapped[str] = mapped_column(String(16), default="dataset")
    target_id: Mapped[str | None] = mapped_column(String(255), index=True)
    issue_type: Mapped[str] = mapped_column(String(32), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    reporter_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reporter_contact: Mapped[str | None] = mapped_column(String(320))
    state: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column()
    #: Reports against the same target are grouped rather than deduped, so the
    #: count is visible. PRD §12.11 carries the surface-vs-dedupe choice forward.
    duplicate_of: Mapped[str | None] = mapped_column(
        ForeignKey("issue_reports.id", ondelete="SET NULL")
    )
    #: PRD §12.12: whether a report on an externally-custodian-owned record is
    #: forwarded. Recorded per report so the eventual policy is auditable.
    forwarded_to_custodian_at: Mapped[datetime | None] = mapped_column()
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        CheckConstraint(
            "issue_type in ('incorrect-metadata','broken-link','license-question',"
            "'wrong-classification','duplicate-record','other')",
            name="issue_type_known",
        ),
        CheckConstraint(
            "target_kind in ('dataset','field','distribution')", name="target_kind_known"
        ),
        CheckConstraint(
            "state in ('open','triaged','resolved','rejected')", name="report_state_known"
        ),
        Index("ix_reports_target", "dataset_id", "target_id", "state"),
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuthorizationEvent(Base, IdMixin):
    """Every grant and refusal (PRD §F10, §F9).

    Also the record that distinguishes a 404 returned because a record is
    absent from a 404 returned because the caller may not know it exists
    (ADR-0006). The caller cannot tell; the audit log can.
    """

    __tablename__ = "authorization_events"

    occurred_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    principal_id: Mapped[str | None] = mapped_column(String(64), index=True)
    principal_kind: Mapped[str] = mapped_column(String(16), default="user")
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(255), index=True)
    outcome: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str | None] = mapped_column(String(200))
    #: Set when the response shown to the caller differs from the true outcome,
    #: e.g. a 404 standing in for a refusal on an allow-listed-existence record.
    masked_as: Mapped[str | None] = mapped_column(String(16))
    client: Mapped[str | None] = mapped_column(String(32))  # ui | sdk | mcp | api
    tool_name: Mapped[str | None] = mapped_column(String(64), index=True)

    __table_args__ = (
        CheckConstraint("outcome in ('granted','refused')", name="outcome_known"),
        Index("ix_auth_principal_time", "principal_id", "occurred_at"),
    )


class AccessPlanIssue(Base, IdMixin):
    """Every access plan issued.

    PRD §12.9 asks whether a plan issued to a user later removed from an
    allow-list is revoked or left to expire. Default is expiry with a short TTL;
    this table is what makes the alternative implementable later without a
    migration, and what makes either choice auditable now.
    """

    __tablename__ = "access_plan_issues"

    dataset_id: Mapped[str] = mapped_column(String(255), index=True)
    distribution_id: Mapped[str] = mapped_column(String(255), index=True)
    principal_id: Mapped[str | None] = mapped_column(String(64), index=True)
    issued_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    revoked_at: Mapped[datetime | None] = mapped_column()
    mode: Mapped[str] = mapped_column(String(24))
    client: Mapped[str | None] = mapped_column(String(32))
    slice_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        CheckConstraint(
            "mode in ('redirect','partial-read','subsetting-protocol')", name="plan_mode_known"
        ),
    )


class RateLimitBucket(Base):
    """Fixed-window counters. Redis in production; this is the durable fallback
    so a single-process deployment still enforces limits."""

    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)


class ProjectorState(Base):
    """One row. Makes projector lag a queryable fact rather than a guess."""

    __tablename__ = "projector_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_commit_at: Mapped[datetime | None] = mapped_column()
    last_indexed_at: Mapped[datetime | None] = mapped_column()
    pending_count: Mapped[int] = mapped_column(Integer, default=0)
    last_full_reindex_at: Mapped[datetime | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)
