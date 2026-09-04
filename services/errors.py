"""Error taxonomy shared by every service.

Errors carry the fact that failed, not a rendered message, so the API layer and
the CLI can present the same failure differently without either re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DataHubError(Exception):
    """Base class. Every deliberate failure in the system derives from this."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_payload(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.context}


class NotFound(DataHubError):
    status_code = 404
    code = "not_found"


class ValidationFailed(DataHubError):
    """A record failed SHACL validation.

    Carries the violations so the message can point at the failing triple
    rather than saying "invalid" (PRD §7.5, M1 done-criterion).
    """

    status_code = 422
    code = "validation_failed"

    def __init__(self, message: str, violations: list[Violation] | None = None, **ctx: Any):
        super().__init__(message, **ctx)
        self.violations = violations or []

    def to_payload(self) -> dict[str, Any]:
        payload = super().to_payload()
        payload["violations"] = [v.to_dict() for v in self.violations]
        return payload


@dataclass(slots=True)
class Violation:
    """One SHACL constraint violation, resolved to the triple that failed."""

    focus_node: str
    path: str | None
    message: str
    severity: str = "Violation"
    value: str | None = None
    source_shape: str | None = None
    constraint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "focusNode": self.focus_node,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
            "value": self.value,
            "sourceShape": self.source_shape,
            "constraint": self.constraint,
        }

    def __str__(self) -> str:
        triple = f"<{self.focus_node}> {self.path or '?'} {self.value or ''}".strip()
        return f"{self.severity}: {self.message} — at {triple}"


class NotEntitled(DataHubError):
    """The caller is authenticated but not permitted.

    Never raised for an ``allowlisted-existence`` record: those return
    :class:`NotFound`, because a 403 is itself a disclosure (ADR-0006).
    """

    status_code = 403
    code = "forbidden"


class NotAuthenticated(DataHubError):
    status_code = 401
    code = "unauthenticated"


class RateLimited(DataHubError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after_s: int = 60, **ctx: Any) -> None:
        super().__init__(message, **ctx)
        self.retry_after_s = retry_after_s


class HarvestError(DataHubError):
    code = "harvest_failed"


class SourceUnavailable(HarvestError):
    """A third-party source failed. Never a reason to fail a whole run."""

    status_code = 502
    code = "source_unavailable"


class EnrichmentRefused(DataHubError):
    """The enricher was asked for a field outside its allow-list (ADR-0005)."""

    code = "enrichment_refused"


class NoUsableDistribution(DataHubError):
    """Every distribution is unreachable or excluded, so no plan can be issued."""

    status_code = 409
    code = "no_usable_distribution"

    def __init__(self, message: str, dataset_id: str, **ctx: Any) -> None:
        super().__init__(message, dataset_id=dataset_id, **ctx)


@dataclass(slots=True)
class Problem:
    """RFC 9457 problem detail, the API's error wire format."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        body = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail:
            body["detail"] = self.detail
        if self.instance:
            body["instance"] = self.instance
        body.update(self.extensions)
        return body
