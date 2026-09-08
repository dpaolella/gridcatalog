"""Human-verification for the anonymous intake forms (PRD §F3).

PRD §F3 asks for "CAPTCHA and rate limiting" on the intake path. Rate limiting
by hashed client address has existed since WP-4.4 and is real, but it caps
*one* client per hour — and the flood the requirement is about is distributed,
where every request comes from a different address and each one is comfortably
under the cap. The two controls answer different questions: the limiter asks
"is this client asking too often", a challenge asks "is there a person here".

**Unconfigured is a supported state, and it is the default.** No token is in
this repository and none should be: a site key and a secret belong to a
deployment, not to a codebase. So `verify` returns
:data:`Outcome.NOT_CONFIGURED` when no secret is set, the routers treat that as
a pass, and the deployment runs exactly as it does today with rate limiting
alone. What changes is that turning the challenge on becomes configuration
rather than code — which is the shape §F3 asks for everywhere else too.

`/v1/status` reports which state it is in, so "we thought CAPTCHA was on" is
answerable without reading the deployment's environment.

Cloudflare Turnstile is the implementation because its verification is one
form POST with no SDK, it has a free tier with no account minimum, and it does
not ship third-party analytics into a page that is otherwise dependency-free.
The provider is behind this module: swapping it means changing `_VERIFY_URL`
and the response key, not the routers.
"""

from __future__ import annotations

import enum
from typing import Any

import httpx
from datahub.logging import get_logger

log = get_logger(__name__)

#: Cloudflare's siteverify endpoint. One POST, form-encoded, JSON back.
_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

#: Short. A challenge that cannot be verified must not hold a person's report
#: open for ten seconds — see the fail-open reasoning on :func:`verify`.
_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


class Outcome(enum.StrEnum):
    """Why a submission was let through, or was not."""

    #: No secret configured. The deployment has not turned this on.
    NOT_CONFIGURED = "not-configured"
    #: The provider confirmed the token.
    PASSED = "passed"
    #: The provider rejected the token, or none was supplied.
    FAILED = "failed"
    #: The provider could not be reached. Treated as a pass; see below.
    UNAVAILABLE = "unavailable"


def is_configured(settings: Any) -> bool:
    """On only when **both** halves are set.

    Requiring both is what stops the one misconfiguration that silently breaks
    intake. With a secret and no site key the browser renders no widget and
    sends no token, the server counts a missing token as a failure, and every
    submission is refused — the form looks fine and nothing arrives. Half a
    configuration is much more likely to be a half-finished rollout than an
    intent to reject everybody, so it reads as off.

    The reverse (site key, no secret) is harmless on its own — the widget
    renders and the token is ignored — and is also treated as off, so the two
    keys have one meaning between them rather than two.
    """
    return bool(getattr(settings, "captcha_secret_key", "")) and bool(
        getattr(settings, "captcha_site_key", "")
    )


def verify(token: str | None, settings: Any, *, remote_ip: str | None = None) -> Outcome:
    """Check one challenge token.

    **Fails open when the provider is unreachable, and closed when it answers
    "no".** Those are different failures and they deserve different answers. A
    provider outage is not evidence about the person filing the report, and
    turning it into a rejection means an outage at Cloudflare silently stops
    every bug report reaching this catalog — a worse outcome than the spam the
    challenge exists to stop, and one nobody would diagnose from the outside.
    A token the provider actively rejects *is* evidence, and is refused.

    The same reasoning the rate limiter already documents: "a limiter that is
    itself unavailable does not block the write".
    """
    if not is_configured(settings):
        return Outcome.NOT_CONFIGURED
    if not token:
        return Outcome.FAILED

    payload = {"secret": settings.captcha_secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        response = httpx.post(_VERIFY_URL, data=payload, timeout=_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        # Deliberately not `raise`. See the docstring: an outage at the
        # provider must not become an outage of the intake path.
        log.warning("captcha provider unavailable", error=str(exc))
        return Outcome.UNAVAILABLE

    if bool(body.get("success")):
        return Outcome.PASSED
    log.info("captcha rejected", codes=body.get("error-codes"))
    return Outcome.FAILED
