"""The harvest pipeline (WP-3.1, WP-3.7).

    harvest ──► filter ──► normalize ──► enrich ──► validate ──► review queue

One class runs that for one source. Everything it needs already exists —
adapters, the relevance filter, the normaliser, the enricher, the validator, the
repositories — so what is left is the sequencing, and the sequencing is where
the rules live:

* **Idempotent re-harvest, keyed on ``sourceId``** (PRD §7.6). An unchanged
  payload short-circuits the whole pipeline: no re-normalise, no re-enrich, no
  re-validate, no model call. On a daily crawl of 2,100 records this is most of
  the cost.
* **A steward-confirmed field is never silently overwritten.** A changed source
  value under a confirmed field flags the record for re-review instead.
* **Failures go to ``flagged``, never to the review queue** (WP-3.7). The queue
  is a work list of records that could be published; a record that does not
  validate cannot be, and mixing the two makes the queue useless.
* **Every relevance decision is written down**, accepts as well as rejects, so
  recall can be audited (PRD §7.2).
* **A partial run is kept.** A source that dies at record 800 of 2,100 leaves
  800 records and a checkpoint, not an exception.

The graph and the operational store are written together per record rather than
per run: a run that dies half way should leave half the records published, not
a rolled-back nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml
from datahub.api.models.base import session_scope
from datahub.api.models.repositories import Repositories
from datahub.config import Settings, get_settings
from datahub.errors import ValidationFailed
from datahub.graph.graphs import NamedGraph
from datahub.graph.records import RecordStore
from datahub.harvest.adapters import Adapter, HarvestedRecord, build
from datahub.harvest.enrich import Enricher
from datahub.harvest.filters.relevance import RelevanceFilter, text_of
from datahub.harvest.normalizers.engine import Normalizer
from datahub.harvest.validate import ValidationRunner
from datahub.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class SourceResult:
    """What one source's run did. Every count is a decision someone can audit."""

    source_id: str
    adapter: str
    seen: int = 0
    #: Passed the relevance filter.
    accepted: int = 0
    rejected: int = 0
    #: Payload unchanged since the last run, so the pipeline was skipped.
    unchanged: int = 0
    created: int = 0
    updated: int = 0
    #: Validated and queued for review.
    queued: int = 0
    #: Did not validate. In the graph's draft space, out of the review queue.
    flagged: int = 0
    #: Re-harvest found a source change under a steward-confirmed field.
    conflicted: int = 0
    enriched: int = 0
    errors: list[str] = field(default_factory=list)
    checkpoint: dict[str, Any] | None = None
    run_id: str | None = None
    duration_s: float = 0.0

    @property
    def summary(self) -> str:
        parts = [
            f"{self.source_id}: {self.seen} seen",
            f"{self.accepted} relevant",
            f"{self.queued} queued",
        ]
        if self.unchanged:
            parts.append(f"{self.unchanged} unchanged")
        if self.flagged:
            parts.append(f"{self.flagged} flagged")
        if self.conflicted:
            parts.append(f"{self.conflicted} conflicts")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts) + f" in {self.duration_s:.1f}s"


class HarvestRunner:
    """Runs one source end to end."""

    def __init__(
        self,
        source: dict[str, Any],
        records: RecordStore,
        settings: Settings | None = None,
        *,
        adapter: Adapter | None = None,
        relevance: RelevanceFilter | None = None,
        enricher: Enricher | None = None,
        validator: ValidationRunner | None = None,
        session_factory: Any = None,
    ) -> None:
        self.source = source
        self.source_id = str(source.get("id") or source.get("adapter"))
        self.records = records
        self.settings = settings or get_settings()
        self.adapter = adapter or build(source, self.settings)
        self.relevance = relevance or RelevanceFilter(self.settings)
        self.enricher = enricher or Enricher(self.settings)
        self.validator = validator or records.runner
        self.normalizer = Normalizer(
            self.adapter.name, self.settings, source_domains=source.get("domains")
        )
        self._session_factory = session_factory or session_scope

    # ---- the run ---------------------------------------------------------

    def run(self, *, limit: int | None = None, resume: bool = True) -> SourceResult:
        import time

        started = time.perf_counter()
        result = SourceResult(source_id=self.source_id, adapter=self.adapter.name)

        checkpoint = self._resume_point() if resume else None
        with self._session_factory() as session:
            run_row = Repositories(session).runs.start(
                self.source_id, self.adapter.name, limit=limit
            )
            result.run_id = run_row.id

        harvested, summary = self.adapter.harvest(limit=limit, checkpoint=checkpoint)
        result.seen = summary.seen
        result.errors.extend(summary.errors)

        for record in harvested:
            try:
                self._one(record, result)
            except Exception as exc:
                result.errors.append(f"{record.source_id}: {type(exc).__name__}: {exc}")
                log.warning(
                    "record failed", source=self.source_id, record=record.source_id, error=str(exc)
                )
            result.checkpoint = self.checkpoint_after(record)

        result.duration_s = time.perf_counter() - started
        self._finish(run_row.id, result)
        log.info("harvest complete", **{"summary": result.summary})
        return result

    def _one(self, record: HarvestedRecord, result: SourceResult) -> None:
        """One record, all the way through.

        The order matters and is not arbitrary: the cheapest gate first, then
        the idempotency check, then the expensive work. Relevance is decided
        before the payload is stored so a rejected record still leaves an
        audit row but costs nothing else; the content-hash check comes next so
        an unchanged record never reaches the normaliser, let alone a model.
        """
        with self._session_factory() as session:
            repos = Repositories(session)

            stored = repos.raw.upsert(
                source_id=self.source_id,
                source_record_id=record.source_id,
                payload=record.payload,
                payload_hash=record.content_hash,
                run_id=result.run_id,
                source_url=record.source_url,
                fetched_at=record.fetched_at,
            )

            decision = self.relevance.decide(text_of(record.payload), title=self._title_of(record))
            repos.relevance.record(
                raw_record_id=stored.row.id,
                source_id=self.source_id,
                **decision.as_row(),
            )
            if not decision.accepted:
                result.rejected += 1
                return
            result.accepted += 1

            if not stored.needs_processing:
                # The whole reason a daily re-harvest is affordable.
                result.unchanged += 1
                return

        normalized = self.normalizer.normalize(record)
        if not normalized.document:
            result.errors.append(f"{record.source_id}: {'; '.join(normalized.warnings)}")
            return

        document = normalized.document
        enrichment = self.enricher.enrich(document)
        if enrichment.enriched:
            document = self.enricher.apply(document, enrichment)
            document["completenessLevel"] = self.normalizer.level(document)
            result.enriched += 1

        self._publish(document, normalized.warnings, result)

    def _publish(
        self,
        document: dict[str, Any],
        warnings: Sequence[str],
        result: SourceResult,
    ) -> None:
        """Write the record, then decide where it belongs.

        Validation decides between the review queue and ``flagged``, and the
        record is written either way: a record that does not validate is still
        the best information anyone has about that dataset, and deleting it
        would mean re-crawling to see it again. It goes to the draft graph,
        which nothing publishes from.
        """
        level = int(document.get("completenessLevel", 1))
        report = self.validator.validate_jsonld(document, level)
        dataset_id = str(document["id"])

        conflicts = self._conflicts(dataset_id, document)

        try:
            put = self.records.put(document, graph=NamedGraph.DRAFT, validate=False)
        except ValidationFailed as exc:  # pragma: no cover - validate=False
            result.errors.append(f"{dataset_id}: {exc.message}")
            return
        if put.created:
            result.created += 1
        elif put.changed:
            result.updated += 1

        with self._session_factory() as session:
            repos = Repositories(session)
            if report.conforms:
                repos.review.enqueue(
                    dataset_id,
                    source_id=self.source_id,
                    data_domain=self._domain_of(document),
                    completeness_level=level,
                    validation_conforms=True,
                )
                result.queued += 1
            else:
                # WP-3.7: failures go to `flagged`, never to the review queue.
                # The queue is a work list of records that could be published;
                # mixing in ones that cannot makes it useless as a work list.
                item = repos.review.enqueue(
                    dataset_id,
                    source_id=self.source_id,
                    data_domain=self._domain_of(document),
                    completeness_level=level,
                    validation_conforms=False,
                    violations=[v.to_dict() for v in report.violations],
                )
                item.state = "flagged"
                result.flagged += 1
                log.info(
                    "record flagged",
                    dataset=dataset_id,
                    violations=len(report.violations),
                    warnings=list(warnings)[:3],
                )

            if conflicts:
                repos.review.record_conflict(dataset_id, conflicts)
                result.conflicted += 1

    # ---- steward-confirmed fields ---------------------------------------

    def _conflicts(self, dataset_id: str, document: dict[str, Any]) -> list[dict[str, Any]]:
        """Where a re-harvest disagrees with something a steward confirmed.

        PRD §7.6 is explicit that this flags rather than overwrites. The
        steward's confirmation is a human judgement about a source that has
        since changed its mind; taking the new value silently would discard the
        judgement and never say so.
        """
        with self._session_factory() as session:
            item = Repositories(session).review.by_dataset(dataset_id)
            confirmed = list(item.confirmed_fields) if item else []
        if not confirmed:
            return []

        try:
            existing = self.records.get(dataset_id)
        except Exception:
            return []
        from datahub.graph.records import dataset_node

        try:
            current = dataset_node(existing)
        except Exception:
            return []

        conflicts = []
        for term in confirmed:
            was, now = current.get(term), document.get(term)
            if now is not None and was is not None and was != now:
                conflicts.append({"field": term, "old": _short(was), "new": _short(now)})
        return conflicts

    # ---- plumbing --------------------------------------------------------

    def checkpoint_after(self, record: HarvestedRecord) -> dict[str, Any]:
        """Where to resume if the run dies here.

        Adapter-shaped: the key each adapter's ``iter_records`` looks for. A
        checkpoint the adapter cannot read is worse than none, because it looks
        like resumption is working.
        """
        payload = record.payload
        if self.adapter.name == "ckan":
            return {"modified_after": payload.get("metadata_modified")}
        if self.adapter.name in ("stac", "yaml_repo", "oep_api", "cds_catalogue"):
            return {"after": record.source_id.rsplit(":", 1)[-1]}
        return {}

    def _resume_point(self) -> dict[str, Any] | None:
        with self._session_factory() as session:
            return Repositories(session).runs.resume_point(self.source_id)

    def _finish(self, run_id: str, result: SourceResult) -> None:
        with self._session_factory() as session:
            repos = Repositories(session)
            run = repos.runs.get(run_id)
            if run is None:  # pragma: no cover - the row was just written
                return
            run.records_seen = result.seen
            run.records_accepted = result.accepted
            run.records_rejected = result.rejected
            run.records_updated = result.updated
            run.records_unchanged = result.unchanged
            run.records_flagged = result.flagged
            repos.runs.finish(
                run,
                state="failed" if result.errors and not result.accepted else "succeeded",
                checkpoint=result.checkpoint,
                errors=result.errors[:50],
            )

    def _title_of(self, record: HarvestedRecord) -> str | None:
        from datahub.harvest.normalizers.engine import resolve

        path = self.normalizer.mapping.identity.get("title", "title")
        value = resolve(record.payload, path)
        return str(value) if value else None

    @staticmethod
    def _domain_of(document: dict[str, Any]) -> str | None:
        domains = document.get("dataDomain") or []
        if isinstance(domains, str):
            domains = [domains]
        return str(domains[0]).rsplit("/", 1)[-1] if domains else None


# ---------------------------------------------------------------------------
# Running several sources
# ---------------------------------------------------------------------------


def harvest_sources(settings: Settings | None = None) -> list[dict[str, Any]]:
    """The source registry, from ``data/seed-sources.yaml``."""
    settings = settings or get_settings()
    document = yaml.safe_load(settings.seed_sources_path.read_text())
    return list(document.get("harvest_sources", []))


def run_sources(
    records: RecordStore,
    *,
    source_ids: Iterable[str] | None = None,
    limit: int | None = None,
    settings: Settings | None = None,
    max_priority: int | None = None,
    resume: bool = True,
    **kwargs: Any,
) -> list[SourceResult]:
    """Run several sources, in priority order.

    Priority order because a run that is cut short — by a schedule, a rate
    limit, an operator — should have spent its time on OEDI and Zenodo rather
    than on the twenty-record STAC catalog.

    One source failing does not stop the others: its errors land in its own
    result and the next source runs.
    """
    settings = settings or get_settings()
    wanted = set(source_ids) if source_ids else None
    sources = [
        source
        for source in harvest_sources(settings)
        if (wanted is None or source.get("id") in wanted)
        and (max_priority is None or int(source.get("priority", 9)) <= max_priority)
    ]
    if wanted and (missing := wanted - {s.get("id") for s in sources}):
        raise KeyError(f"unknown source(s): {', '.join(sorted(missing))}")

    results = []
    for source in sorted(sources, key=lambda s: (int(s.get("priority", 9)), str(s.get("id")))):
        runner = HarvestRunner(source, records, settings, **kwargs)
        try:
            results.append(runner.run(limit=limit, resume=resume))
        except Exception as exc:
            log.exception("source failed", source=source.get("id"))
            results.append(
                SourceResult(
                    source_id=str(source.get("id")),
                    adapter=str(source.get("adapter")),
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            )
        finally:
            runner.adapter.close()
    return results


def _short(value: Any, limit: int = 200) -> str:
    text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
    return text[:limit]


__all__ = ["HarvestRunner", "SourceResult", "harvest_sources", "run_sources"]
