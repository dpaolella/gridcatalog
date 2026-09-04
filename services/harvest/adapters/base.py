"""The harvest adapter contract.

Eight adapters implement this (PRD §7.1). Everything they share lives here so
that eight modules cannot each invent their own rate limiter, their own retry
policy and their own idea of what a harvested record is.

The rules the PRD sets for every adapter:

* **A stable ``source_id``**, so re-harvest is idempotent. Matching on it
  updates source-derived fields and leaves steward-confirmed ones alone; a
  changed source value under a confirmed field flags the record for re-review
  rather than overwriting it silently (PRD §7.6).
* **A run record** with counts, errors and duration.
* **Politeness.** "Never look like abusive traffic to a source you do not
  control." The default is one request per second with exponential backoff, and
  an adapter that wants faster has to say so and say why.
* **Independently runnable**: ``python -m datahub.harvest --source oedi --limit 100``.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from datahub.config import Settings, get_settings
from datahub.errors import SourceUnavailable
from datahub.logging import get_logger

log = get_logger(__name__)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class HarvestedRecord:
    """One record as the source published it, before any normalisation.

    Kept verbatim in ``raw_records`` so a normaliser bug can be fixed and
    replayed without re-crawling a third party, and so a steward can see what
    the source actually said rather than what we made of it.
    """

    source_id: str
    """Stable within the source. The idempotency key for re-harvest."""

    source: str
    """Which adapter produced this — ``oedi``, ``zenodo``, ``curated``."""

    payload: dict[str, Any]
    source_url: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def content_hash(self) -> str:
        """Hash of the payload. An unchanged hash short-circuits the pipeline.

        Sorted keys, because a source that reorders its JSON has not changed
        its data and should not cost a re-normalise, a re-enrich and a
        re-validate.
        """
        canonical = json.dumps(self.payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def qualified_id(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass(slots=True)
class HarvestRunSummary:
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    seen: int = 0
    emitted: int = 0
    errors: list[str] = field(default_factory=list)
    checkpoint: dict[str, Any] | None = None
    limit_applied: int | None = None

    @property
    def duration_s(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()

    @property
    def summary(self) -> str:
        line = f"{self.source}: {self.emitted} of {self.seen} records in {self.duration_s:.1f}s"
        if self.errors:
            line += f", {len(self.errors)} errors"
        if self.limit_applied:
            line += f" (limited to {self.limit_applied})"
        return line


class RateLimiter:
    """A token-bucket-free minimum-interval limiter.

    Deliberately the simplest thing that is actually polite: a floor on the gap
    between requests. A token bucket would allow a burst, and a burst against a
    volunteer-run CKAN instance is exactly what gets a harvester blocked.
    """

    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
                now = time.monotonic()
            self._next_allowed = now + self.interval


class Adapter(ABC):
    """Base class for every harvest adapter.

    Subclasses implement :meth:`iter_records`. Everything else — the HTTP
    client, the rate limit, the retry policy, the run summary — is provided.
    """

    #: Adapter id, matching the ``adapter`` field in ``data/seed-sources.yaml``.
    name: str = "abstract"

    def __init__(
        self,
        source_id: str,
        settings: Settings | None = None,
        *,
        endpoint: str | None = None,
        rate_per_second: float | None = None,
        client: httpx.Client | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.source_id = source_id
        self.settings = settings or get_settings()
        self.endpoint = endpoint
        self.config = config or {}
        self.limiter = RateLimiter(
            rate_per_second
            if rate_per_second is not None
            else self.settings.harvest_default_rate_per_s
        )
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.settings.harvest_timeout_s,
                follow_redirects=True,
                headers={"User-Agent": self.settings.harvest_user_agent},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> Adapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- the one method a subclass must write ----------------------------

    @abstractmethod
    def iter_records(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> Iterator[HarvestedRecord]:
        """Yield records, oldest-first where the source has an order.

        ``checkpoint`` is whatever this adapter last returned in
        :attr:`HarvestRunSummary.checkpoint`. A long harvest that dies partway
        must be resumable; a source with 2,100 datasets and a rate limit of one
        per second is a thirty-five minute run, and starting it again from zero
        is how a harvest never finishes.
        """

    # ---- provided --------------------------------------------------------

    def harvest(
        self, *, limit: int | None = None, checkpoint: dict[str, Any] | None = None
    ) -> tuple[list[HarvestedRecord], HarvestRunSummary]:
        """Run the adapter and collect its records.

        A source failing part-way is recorded, not raised: a run that got 800 of
        2,100 records is worth keeping, and the checkpoint says where to resume.
        """
        summary = HarvestRunSummary(
            source=self.source_id, started_at=datetime.now(UTC), limit_applied=limit
        )
        records: list[HarvestedRecord] = []
        try:
            for record in self.iter_records(limit=limit, checkpoint=checkpoint):
                summary.seen += 1
                records.append(record)
                summary.emitted += 1
                if limit is not None and summary.emitted >= limit:
                    break
        except SourceUnavailable as exc:
            summary.errors.append(str(exc))
            log.warning("source unavailable", source=self.source_id, error=str(exc))
        except Exception as exc:
            summary.errors.append(f"{type(exc).__name__}: {exc}")
            log.exception("harvest failed", source=self.source_id)
        summary.finished_at = datetime.now(UTC)
        log.info("harvest run complete", **{"summary": summary.summary})
        return records, summary

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """A rate-limited, retrying GET returning JSON.

        Retries on 429, on 5xx and on a transport error, with exponential
        backoff and jitter. Jitter matters: without it, two adapters that start
        together stay in lockstep and hammer a recovering source in unison.
        """
        last_error: Exception | None = None
        for attempt in range(self.settings.harvest_max_retries + 1):
            self.limiter.wait()
            try:
                response = self.client.get(url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = SourceUnavailable(
                        f"{url} returned {response.status_code}",
                        status=response.status_code,
                    )
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        time.sleep(min(int(retry_after), 60))
                        continue
                else:
                    # A 404 or a 403 will not improve on a retry, and retrying
                    # a 403 is what turns a misconfiguration into a ban.
                    raise SourceUnavailable(
                        f"{url} returned {response.status_code}",
                        status=response.status_code,
                    )
            if attempt < self.settings.harvest_max_retries:
                backoff = (2**attempt) + random.uniform(0, 0.5)
                time.sleep(backoff)
        raise SourceUnavailable(f"{url} failed after retries: {last_error}")


def slugify(text: str, *, max_length: int = 80) -> str:
    """A stable, URL-safe slug. The last segment of a minted IRI.

    Deterministic, because a slug is an identity: the same title must give the
    same slug on every run, or a re-harvest creates a duplicate record rather
    than updating one.
    """
    slug = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    if len(slug) <= max_length:
        return slug or "unnamed"
    # Truncating alone would collide two long titles sharing a prefix; the
    # digest keeps them distinct while staying deterministic.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{slug[: max_length - 9].rstrip('-')}-{digest}"
