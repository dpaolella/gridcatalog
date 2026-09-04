"""The operational store's query objects (WP-2.3).

Three properties carry the weight, and each is a rule the PRD states rather
than a convention:

* an allow-list entry counts only while it is unrevoked and unexpired;
* re-harvest matches on ``(source_id, source_record_id)`` and nothing derived;
* a steward-confirmed field is never silently overwritten.

The rest of the tests are here because a query object with no test is a place
for a wrong ``WHERE`` clause to live indefinitely.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.models.base import utcnow
from datahub.api.models.operational import ApiToken, ProbeResult, ReviewQueueItem, User
from sqlalchemy.exc import IntegrityError

# ---- identity ------------------------------------------------------------


def test_a_federated_login_is_matched_on_subject_not_email(repos) -> None:
    """An email is reassignable and a subject is not. Matching on email would
    let a reused address inherit the previous holder's allow-list grants."""
    first = repos.users.upsert_federated("github", "1234", email="a@example.org")
    repos.session.commit()

    # The same person signs in again, having changed their address upstream.
    again = repos.users.upsert_federated("github", "1234", email="new@example.org")
    # A different person is later assigned the address the first one gave up.
    other = repos.users.upsert_federated("github", "9999", email="a@example.org")

    assert again.id == first.id
    assert other.id != first.id
    # The colliding address stays on the identity; only the primary is left
    # unset, because two users cannot both hold it and neither claim is ours
    # to arbitrate.
    assert other.email is None
    assert other.identities[0].email == "a@example.org"


def test_signing_in_again_records_the_visit(repos) -> None:
    user = repos.users.upsert_federated("github", "1234")
    assert user.last_seen_at is None
    repos.session.commit()

    assert repos.users.upsert_federated("github", "1234").last_seen_at is not None


def test_a_revoked_token_does_not_authenticate(repos) -> None:
    """Revocation is a condition of the lookup, not a check afterwards: a call
    site that forgot it would be an authentication bypass."""
    user = repos.users.upsert_federated("github", "1")
    token = repos.tokens.add(
        ApiToken(user_id=user.id, name="sdk", token_hash="deadbeef", prefix="og_dead")
    )
    repos.session.commit()

    assert repos.tokens.by_hash("deadbeef") is not None
    assert repos.tokens.revoke(token.id)
    assert repos.tokens.by_hash("deadbeef") is None


def test_an_expired_token_does_not_authenticate(repos) -> None:
    user = repos.users.upsert_federated("github", "1")
    repos.tokens.add(
        ApiToken(
            user_id=user.id,
            name="stale",
            token_hash="expired",
            prefix="og_exp",
            expires_at=utcnow() - timedelta(seconds=1),
        )
    )
    repos.session.commit()

    assert repos.tokens.by_hash("expired") is None


def test_role_is_constrained_at_the_database(repos) -> None:
    """A typo'd role must not become a new privilege level by accident."""
    repos.session.add(User(email="x@example.org", role="superuser"))
    with pytest.raises(IntegrityError):
        repos.session.commit()


# ---- entitlement ---------------------------------------------------------


def test_an_expired_grant_does_not_entitle(repos) -> None:
    """The failure this exists to prevent: a lapsed grant that keeps working
    because the expiry clause was dropped from one query."""
    repos.allowlist.grant(
        "ds/secret",
        granted_by="custodian",
        principal_id="u1",
        expires_at=utcnow() - timedelta(minutes=1),
    )
    repos.allowlist.grant("ds/secret", granted_by="custodian", principal_id="u2")
    repos.session.commit()

    assert not repos.allowlist.is_allowed("ds/secret", "u1", None)
    assert repos.allowlist.is_allowed("ds/secret", "u2", None)
    assert repos.allowlist.datasets_for("u1", None) == []
    assert repos.allowlist.datasets_for("u2", None) == ["ds/secret"]


def test_a_revoked_grant_does_not_entitle(repos) -> None:
    repos.allowlist.grant("ds/secret", granted_by="custodian", principal_id="u1")
    repos.session.commit()
    assert repos.allowlist.is_allowed("ds/secret", "u1", None)

    assert repos.allowlist.revoke("ds/secret", "u1")
    repos.session.commit()

    assert not repos.allowlist.is_allowed("ds/secret", "u1", None)


def test_a_grant_may_name_someone_who_has_not_signed_in(repos) -> None:
    """A custodian grants access to a colleague by address, before that
    colleague has an account. Requiring a user id first would make the
    allow-list unusable for the case it exists for."""
    repos.allowlist.grant("ds/secret", granted_by="custodian", principal_email="Later@Example.org")
    repos.session.commit()

    assert repos.allowlist.is_allowed("ds/secret", None, "later@example.org")
    assert not repos.allowlist.is_allowed("ds/secret", None, "someone@example.org")


def test_an_anonymous_caller_is_entitled_to_nothing(repos) -> None:
    repos.allowlist.grant("ds/secret", granted_by="c", principal_id="u1")
    repos.session.commit()

    assert not repos.allowlist.is_allowed("ds/secret", None, None)
    assert repos.allowlist.datasets_for(None, None) == []


def test_entitlement_is_returned_as_a_list_to_compile_into_the_query(repos) -> None:
    """ADR-0006: entitlement is compiled into the SPARQL query, never applied
    to its results. Post-filtering leaks existence through result counts."""
    for dataset in ("ds/b", "ds/a", "ds/c"):
        repos.allowlist.grant(dataset, granted_by="c", principal_id="u1")
    repos.session.commit()

    assert repos.allowlist.datasets_for("u1", None) == ["ds/a", "ds/b", "ds/c"]


def test_a_grant_names_a_principal_or_it_is_not_a_grant(repos) -> None:
    from datahub.api.models.operational import AllowlistEntry

    repos.session.add(AllowlistEntry(dataset_id="ds/x", granted_by="c"))
    with pytest.raises(IntegrityError):
        repos.session.commit()


def test_a_hosted_copy_without_an_owner_is_visible_as_such(repos) -> None:
    """PRD §F2: a named refresh owner *before* launch, not after. The check has
    to be answerable, which means the absence has to be queryable."""
    assert not repos.hosted.has_owner("dist/hosted--parquet")


# ---- harvest -------------------------------------------------------------


def test_an_unchanged_payload_short_circuits_the_pipeline(repos) -> None:
    """Most of what makes a daily re-harvest of 2,100 records cheap."""
    first = repos.raw.upsert(
        source_id="oedi", source_record_id="abc", payload={"a": 1}, payload_hash="h1"
    )
    same = repos.raw.upsert(
        source_id="oedi", source_record_id="abc", payload={"a": 1}, payload_hash="h1"
    )
    changed = repos.raw.upsert(
        source_id="oedi", source_record_id="abc", payload={"a": 2}, payload_hash="h2"
    )

    assert first.created and first.needs_processing
    assert not same.created and not same.changed and not same.needs_processing
    assert changed.changed and changed.needs_processing
    assert changed.row.id == first.row.id, "re-harvest updated rather than duplicated"
    assert changed.row.payload == {"a": 2}


def test_looking_and_finding_nothing_new_is_still_looking(repos) -> None:
    """ "We checked and it was the same" is a different fact from "we have not
    checked since March", and staleness reporting needs both."""
    first = repos.raw.upsert(
        source_id="oedi",
        source_record_id="abc",
        payload={"a": 1},
        payload_hash="h1",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    repos.session.commit()

    again = repos.raw.upsert(
        source_id="oedi",
        source_record_id="abc",
        payload={"a": 1},
        payload_hash="h1",
        fetched_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    assert not again.changed
    assert again.row.fetched_at == datetime(2026, 6, 1, tzinfo=UTC)
    assert first.row.id == again.row.id


def test_the_same_record_id_under_two_sources_is_two_records(repos) -> None:
    a = repos.raw.upsert(source_id="oedi", source_record_id="1", payload={}, payload_hash="h")
    b = repos.raw.upsert(source_id="zenodo", source_record_id="1", payload={}, payload_hash="h")
    assert a.row.id != b.row.id


def test_a_harvest_resumes_from_a_failed_run(repos) -> None:
    """The case resumption exists for. A source with 2,100 records at one
    request a second is a thirty-five minute run."""
    run = repos.runs.start("oedi", "ckan")
    repos.runs.finish(run, state="failed", checkpoint={"after": "record-800"}, errors=["503"])
    repos.session.commit()

    assert repos.runs.resume_point("oedi") == {"after": "record-800"}
    assert repos.runs.last_successful("oedi") is None
    assert run.errors == 1


def test_a_run_killed_mid_harvest_is_findable(repos) -> None:
    """A row left at `running` for ever would make a scheduler that skips
    in-flight sources never harvest that source again."""
    run = repos.runs.start("oedi", "ckan")
    run.started_at = utcnow() - timedelta(hours=9)
    repos.session.commit()

    assert [r.id for r in repos.runs.stale_running()] == [run.id]
    assert repos.runs.stale_running(older_than=timedelta(days=2)) == []


def test_run_state_is_constrained(repos) -> None:
    run = repos.runs.start("oedi", "ckan")
    run.state = "sort-of-finished"
    with pytest.raises(IntegrityError):
        repos.session.commit()


# ---- relevance -----------------------------------------------------------


def test_rejections_are_auditable(repos) -> None:
    """PRD §7.2. Without the rejections the filter is unfalsifiable: a wrongly
    excluded dataset is invisible by construction."""
    raw = repos.raw.upsert(
        source_id="zenodo", source_record_id="1", payload={}, payload_hash="h"
    ).row
    repos.relevance.record(
        raw_record_id=raw.id,
        source_id="zenodo",
        accepted=False,
        stage="keyword",
        reason="no grid vocabulary term matched title or description",
    )
    repos.relevance.record(
        raw_record_id=raw.id,
        source_id="zenodo",
        accepted=True,
        stage="llm",
        reason="describes transmission line ratings",
        score=0.88,
        model="test-model",
        prompt_version="v1",
    )
    repos.session.commit()

    rejected = repos.relevance.rejections()
    assert len(rejected) == 1
    assert "no grid vocabulary term" in rejected[0].reason
    assert repos.relevance.rates() == {
        "keyword": {"accepted": 0, "rejected": 1},
        "llm": {"accepted": 1, "rejected": 0},
    }


def test_a_decision_names_the_stage_that_made_it(repos) -> None:
    raw = repos.raw.upsert(
        source_id="zenodo", source_record_id="1", payload={}, payload_hash="h"
    ).row
    with pytest.raises(IntegrityError):
        repos.relevance.record(
            raw_record_id=raw.id,
            source_id="zenodo",
            accepted=False,
            stage="vibes",
            reason="",
        )


# ---- review queue --------------------------------------------------------


def test_a_confirmed_field_is_flagged_not_overwritten(repos) -> None:
    """PRD §7.6. A steward's confirmation is a human judgement about a source
    that has since changed its mind; taking the new value silently would
    discard the judgement and never say so."""
    repos.review.enqueue("ds/x", source_id="oedi", data_domain="DD1")
    repos.review.confirm("ds/x", reviewed_by="steward@example.org", confirmed_fields=["license"])
    repos.session.commit()

    item = repos.review.record_conflict(
        "ds/x", [{"field": "license", "old": "CC-BY-4.0", "new": "CC-BY-NC-4.0"}]
    )

    assert item.state == "flagged"
    assert item.conflict_detail[0]["new"] == "CC-BY-NC-4.0"
    assert item.confirmed_fields == ["license"], "the confirmation itself survives the conflict"


def test_a_reharvest_does_not_reset_a_stewards_work(repos) -> None:
    repos.review.enqueue("ds/x", source_id="oedi", data_domain="DD1")
    repos.review.confirm(
        "ds/x",
        reviewed_by="steward@example.org",
        confirmed_fields=["license"],
        notes="checked with the custodian",
    )
    repos.session.commit()

    item = repos.review.enqueue("ds/x", source_id="oedi", completeness_level=2)

    assert item.state == "confirmed"
    assert item.confirmed_fields == ["license"]
    assert item.steward_notes == "checked with the custodian"
    assert item.completeness_level == 2, "source-derived facts still update"


def test_a_second_review_adds_to_the_first(repos) -> None:
    repos.review.enqueue("ds/x")
    repos.review.confirm("ds/x", reviewed_by="a", confirmed_fields=["license"])
    item = repos.review.confirm("ds/x", reviewed_by="b", confirmed_fields=["tier"])

    assert item.confirmed_fields == ["license", "tier"]


def test_the_queue_puts_high_leverage_records_first(repos) -> None:
    """A record twelve others cite is worth reviewing before one nothing points
    at."""
    for dataset_id, links, level in (
        ("ds/ignored", 0, 1),
        ("ds/cited", 12, 1),
        ("ds/complete", 0, 3),
    ):
        repos.review.enqueue(dataset_id, completeness_level=level)
        repos.review.set_inbound_links({dataset_id: links})
    repos.session.commit()

    assert [i.dataset_id for i in repos.review.next_batch()] == [
        "ds/cited",
        "ds/complete",
        "ds/ignored",
    ]


def test_the_queue_can_be_filtered_by_domain(repos) -> None:
    repos.review.enqueue("ds/a", data_domain="DD1")
    repos.review.enqueue("ds/b", data_domain="DD5")
    repos.session.commit()

    assert [i.dataset_id for i in repos.review.next_batch(data_domain="DD5")] == ["ds/b"]


def test_review_state_is_constrained(repos) -> None:
    repos.session.add(ReviewQueueItem(dataset_id="ds/x", state="probably-fine"))
    with pytest.raises(IntegrityError):
        repos.session.commit()


def test_completeness_level_is_constrained(repos) -> None:
    repos.session.add(ReviewQueueItem(dataset_id="ds/x", completeness_level=4))
    with pytest.raises(IntegrityError):
        repos.session.commit()


# ---- link health ---------------------------------------------------------


def _probe(status: str, *, at: datetime | None = None) -> ProbeResult:
    return ProbeResult(
        distribution_id="dist/1",
        dataset_id="ds/1",
        status=status,
        probed_at=at or utcnow(),
        http_status=200 if status == "verified" else 503,
    )


def test_consecutive_failures_reset_on_a_success(repos) -> None:
    for _ in range(3):
        repos.health.apply(_probe("unreachable"))
    assert repos.health.for_distribution("dist/1").consecutive_failures == 3

    row = repos.health.apply(_probe("verified"))

    assert row.consecutive_failures == 0
    assert row.last_success_at is not None


def test_a_working_redirect_counts_as_reachable(repos) -> None:
    """The resource is there, at a new address. Treating a live 301 as a
    failure would eventually exclude a working dataset from access plans."""
    repos.health.apply(_probe("unreachable"))
    row = repos.health.apply(_probe("redirected"))

    assert row.consecutive_failures == 0
    assert row.status == "redirected"


def test_health_schedules_the_next_probe(repos) -> None:
    at = datetime(2026, 3, 1, tzinfo=UTC)
    row = repos.health.apply(_probe("verified", at=at))

    assert row.next_probe_due == at + timedelta(seconds=row.probe_cadence_s)
    assert repos.health.due(now=at) == []
    assert [r.distribution_id for r in repos.health.due(now=at + timedelta(days=8))] == ["dist/1"]


def test_cadence_follows_the_kind_of_distribution(repos) -> None:
    """PRD §F1: APIs daily, bulk files weekly, tier 3 monthly."""
    repos.health.apply(_probe("verified"))
    repos.health.set_cadence("dist/1", "api")

    assert repos.health.for_distribution("dist/1").probe_cadence_s == 86_400


def test_persistently_failing_distributions_are_findable(repos) -> None:
    for _ in range(3):
        repos.health.apply(_probe("unreachable"))
    repos.session.commit()

    assert [r.distribution_id for r in repos.health.unhealthy()] == ["dist/1"]
    assert repos.health.unhealthy(min_failures=4) == []


def test_probe_history_can_be_pruned_without_losing_current_state(repos) -> None:
    old = _probe("verified", at=utcnow() - timedelta(days=200))
    repos.probes.record(old)
    repos.health.apply(old)
    repos.probes.record(_probe("verified"))
    repos.session.commit()

    assert repos.probes.prune(keep_days=90) == 1
    assert repos.health.for_distribution("dist/1") is not None
    assert len(repos.probes.history("dist/1")) == 1


# ---- revisions -----------------------------------------------------------


def test_a_url_change_leaves_the_old_value_readable(repos) -> None:
    """PRD §F1.11: provenance is never silently rewritten."""
    repos.revisions.record(
        distribution_id="dist/1",
        dataset_id="ds/1",
        field="accessURL",
        old_value="http://old.example.org/f.nc",
        new_value="https://new.example.org/f.nc",
        source="probe",
        detail="301 observed on three consecutive probes",
    )
    repos.session.commit()

    history = repos.revisions.history("dist/1")
    assert len(history) == 1
    assert history[0].old_value == "http://old.example.org/f.nc"
    assert history[0].automated is True


def test_a_revision_that_changes_nothing_is_not_written(repos) -> None:
    """A no-op revision pads the history a steward reads when deciding whether
    a distribution has been stable."""
    assert (
        repos.revisions.record(
            distribution_id="dist/1",
            dataset_id="ds/1",
            field="accessURL",
            old_value="https://same",
            new_value="https://same",
            source="probe",
        )
        is None
    )
    assert repos.revisions.history("dist/1") == []


def test_revision_source_is_constrained(repos) -> None:
    with pytest.raises(IntegrityError):
        repos.revisions.record(
            distribution_id="dist/1",
            dataset_id="ds/1",
            field="accessURL",
            old_value="a",
            new_value="b",
            source="a-hunch",
        )


# ---- audit ---------------------------------------------------------------


def test_a_masked_refusal_is_recorded_as_a_refusal(repos) -> None:
    """The caller sees a 404 and cannot tell it from an absent record — that is
    the point (ADR-0006). The audit log is the only place that can."""
    repos.audit.record(
        action="dataset.read",
        outcome="refused",
        resource_kind="dataset",
        resource_id="ds/secret",
        principal_id="u1",
        reason="not on the allow-list",
        masked_as="404",
        client="mcp",
        tool_name="get_dataset",
    )
    repos.session.commit()

    event = repos.audit.refusals()[0]
    assert event.outcome == "refused"
    assert event.masked_as == "404"
    assert repos.audit.for_principal("u1")[0].id == event.id


def test_outcome_is_constrained(repos) -> None:
    with pytest.raises(IntegrityError):
        repos.audit.record(action="x", outcome="maybe", resource_kind="dataset")


# ---- access plans --------------------------------------------------------


def test_an_expired_plan_is_not_active(repos) -> None:
    repos.plans.issue(
        dataset_id="ds/1",
        distribution_id="dist/1",
        mode="redirect",
        ttl=timedelta(seconds=-1),
        principal_id="u1",
    )
    repos.plans.issue(
        dataset_id="ds/1",
        distribution_id="dist/1",
        mode="redirect",
        ttl=timedelta(minutes=15),
        principal_id="u1",
    )
    repos.session.commit()

    assert len(repos.plans.active_for("u1")) == 1


def test_plans_can_be_revoked_when_a_grant_is(repos) -> None:
    """PRD §12.9 leaves the choice open. The default is expiry; this makes the
    other choice a call site rather than a migration."""
    repos.plans.issue(
        dataset_id="ds/1",
        distribution_id="dist/1",
        mode="redirect",
        ttl=timedelta(minutes=15),
        principal_id="u1",
    )
    repos.session.commit()

    assert repos.plans.revoke_for("ds/1", "u1") == 1
    assert repos.plans.active_for("u1") == []


# ---- rate limits and projector state -------------------------------------


def test_the_fallback_limiter_counts_within_a_window(repos) -> None:
    results = [repos.limits.hit("ip:1.2.3.4", window_s=60, limit=3) for _ in range(4)]
    assert [allowed for allowed, _ in results] == [True, True, True, False]
    assert [count for _, count in results] == [1, 2, 3, 4]


def test_limiter_keys_do_not_interfere(repos) -> None:
    repos.limits.hit("ip:a", window_s=60, limit=1)
    allowed, count = repos.limits.hit("ip:b", window_s=60, limit=1)
    assert allowed and count == 1


def test_projector_lag_is_a_queryable_fact(repos) -> None:
    assert repos.projector.lag_seconds() is None, "nothing committed is not the same as caught up"

    repos.projector.mark_commit()
    assert repos.projector.lag_seconds() is not None
    assert repos.projector.current().pending_count == 1

    repos.projector.mark_indexed()
    assert repos.projector.lag_seconds() == 0.0
    assert repos.projector.current().pending_count == 0


def test_a_full_reindex_clears_the_backlog_and_the_error(repos) -> None:
    repos.projector.mark_commit()
    repos.projector.mark_indexed(pending=4, error="opensearch refused the bulk write")

    state = repos.projector.mark_indexed(full_reindex=True)

    assert state.pending_count == 0
    assert state.last_error is None
    assert state.last_full_reindex_at is not None


def test_an_incremental_index_does_not_claim_a_full_rebuild(repos) -> None:
    """`last_full_reindex_at` is what says whether the derived state has been
    rebuilt from scratch recently. A per-record projection setting it would make
    a system that never rebuilds look like one that rebuilds constantly."""
    repos.projector.mark_indexed()
    assert repos.projector.current().last_full_reindex_at is None


def test_projector_state_is_a_singleton(repos) -> None:
    from datahub.api.models.operational import ProjectorState

    repos.projector.current()
    repos.session.add(ProjectorState(id=2))
    with pytest.raises(IntegrityError):
        repos.session.commit()
