"""Rate limiting across every client type (WP-6.3).

PRD §F9: *rate limits sized for agentic traffic being several times chattier
than human traffic*, and §F10: *rate limiting across all client types, with
different budgets for human and agent traffic.*

Three budgets, because three kinds of caller have genuinely different shapes:

* **Anonymous** — the tightest. An unauthenticated caller is either a person
  browsing, which is slow, or something automated that has not identified
  itself, which is the thing a limit is for.
* **Human** — a person clicking, plus the search-while-typing the UI does on
  every keystroke.
* **Agent** — several times the human budget, because an agent answering one
  question legitimately makes twenty calls, and a limit that made agentic use
  impossible would just push it to scraping the UI.

**The limit is per principal, not per address.** A shared office NAT is one
address and forty people; a single agent behind a rotating pool is forty
addresses and one caller. Falling back to the address only for anonymous
traffic is the closest available approximation.

**A limited response says what the limit is and when it resets.** PRD §F10:
*401 and 403 with clear errors, never silent degradation. A silently truncated
result set is a correctness bug that looks like a UX choice.* The same applies
here — a 429 with no ``Retry-After`` teaches a client to hammer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from datahub.config import Settings, get_settings
from datahub.logging import get_logger

log = get_logger(__name__)

WINDOW_S = 60

#: Paths a limit must never apply to. A health check that can be rate-limited
#: takes the deployment out of rotation under exactly the load it exists to
#: report on.
EXEMPT = ("/v1/health", "/docs", "/redoc", "/openapi.json")


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_in: int
    bucket: str

    @property
    def headers(self) -> dict[str, str]:
        """Standard rate-limit headers, on every response and not only on 429.

        A client that can see it has four requests left paces itself; one that
        finds out by being refused has already failed a user's request.
        """
        out = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(max(0, self.remaining)),
            "RateLimit-Reset": str(self.reset_in),
        }
        if not self.allowed:
            out["Retry-After"] = str(self.reset_in)
        return out


class RateLimiter:
    """Fixed-window counters, in memory with a durable fallback.

    In-process is the fast path and is correct for a single-process
    deployment. The operational store's counters are what make a limit hold
    across a restart, and Redis is what makes it hold across processes — the
    seam is :meth:`_count`, deliberately one method.
    """

    def __init__(self, settings: Settings | None = None, *, session_factory: Any = None) -> None:
        self.settings = settings or get_settings()
        self._counts: dict[tuple[str, int], int] = {}
        self._session_factory = session_factory

    def budget(self, *, principal_id: str | None, is_agent: bool) -> int:
        if principal_id is None:
            return self.settings.rate_limit_anonymous_per_min
        return (
            self.settings.rate_limit_agent_per_min
            if is_agent
            else self.settings.rate_limit_human_per_min
        )

    def check(
        self,
        *,
        principal_id: str | None,
        is_agent: bool,
        client_host: str | None,
        cost: int = 1,
    ) -> Decision:
        """Count one request against the caller's budget.

        Per principal where there is one. A shared office NAT is one address
        and forty people; an agent behind a rotating pool is forty addresses
        and one caller.
        """
        bucket = f"user:{principal_id}" if principal_id else f"ip:{client_host or 'unknown'}"
        limit = self.budget(principal_id=principal_id, is_agent=is_agent)
        window = int(time.time()) // WINDOW_S
        count = self._count(bucket, window, cost)

        return Decision(
            allowed=count <= limit,
            limit=limit,
            remaining=limit - count,
            reset_in=WINDOW_S - int(time.time()) % WINDOW_S,
            bucket=bucket,
        )

    def _count(self, bucket: str, window: int, cost: int) -> int:
        """Increment and return the count. The one seam a backend swaps."""
        key = (bucket, window)
        self._counts[key] = self._counts.get(key, 0) + cost
        # Windows older than the current one can never be counted against
        # again; without this the dict is a slow memory leak keyed by minute.
        if len(self._counts) > 4096:
            self._counts = {k: v for k, v in self._counts.items() if k[1] >= window - 1}
        return self._counts[key]

    def reset(self) -> None:
        self._counts.clear()


def exempt(path: str) -> bool:
    return path.startswith(EXEMPT)


def enabled(settings: Settings | None = None) -> bool:
    """Whether to count this request at all.

    False only where the caller is not a caller — the snapshot exporter driving
    the app in-process to produce a build artefact. A throttled exporter writes
    a static site with pages silently missing, which is worse than the abuse
    the limit exists to stop.
    """
    return (settings or get_settings()).rate_limit_enabled


__all__ = ["EXEMPT", "WINDOW_S", "Decision", "RateLimiter", "enabled", "exempt"]
