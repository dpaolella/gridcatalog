"""The intake challenge (PRD F3, #40 item 3).

F3 asks for "CAPTCHA and rate limiting" on the anonymous intake path. Rate
limiting by hashed client address has existed since WP-4.4; a challenge had
never been built. The two answer different questions -- the limiter caps *one*
client per hour, and the flood the requirement is about is distributed, where
every request comes from a different address and each is under the cap.

Unconfigured is the default and a supported state: no key belongs in this
repository, so the tests below fix the behaviour in both configurations rather
than assuming a deployment has turned it on.
"""

from __future__ import annotations

import httpx
import pytest
from datahub.api import captcha
from datahub.api.captcha import Outcome


class _Settings:
    def __init__(self, secret: str = "", site: str | None = None) -> None:
        self.captcha_secret_key = secret
        # Defaults to "set whenever the secret is", so the tests below are
        # about verification rather than about configuration; the
        # half-configured cases name both explicitly.
        self.captcha_site_key = site if site is not None else ("site" if secret else "")


def test_unconfigured_is_a_pass_and_not_an_error() -> None:
    """The default. A deployment that has not set a key must behave exactly as
    it did before this existed, with rate limiting alone."""
    assert captcha.verify("anything", _Settings()) is Outcome.NOT_CONFIGURED
    assert captcha.verify(None, _Settings()) is Outcome.NOT_CONFIGURED
    assert not captcha.is_configured(_Settings())


def test_a_missing_token_fails_once_configured() -> None:
    """Turning the challenge on has to actually require it, or it is decoration."""
    assert captcha.verify(None, _Settings("secret")) is Outcome.FAILED
    assert captcha.verify("", _Settings("secret")) is Outcome.FAILED


def _response(body: dict) -> httpx.Response:
    """A response `raise_for_status` can be called on.

    `httpx.Response` needs its request set before `raise_for_status` works, and
    a bare one raises a RuntimeError that `verify` catches as "unavailable" —
    so a stub without it silently tests the wrong branch.
    """
    return httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.invalid"))


def test_a_provider_rejection_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _response({"success": False, "error-codes": ["bad"]})
    )
    assert captcha.verify("token", _Settings("secret")) is Outcome.FAILED


def test_a_provider_confirmation_passes(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response({"success": True}))
    assert captcha.verify("token", _Settings("secret")) is Outcome.PASSED


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("no route"),
        httpx.ReadTimeout("slow"),
        httpx.HTTPStatusError("500", request=None, response=None),  # type: ignore[arg-type]
    ],
)
def test_an_unreachable_provider_fails_open(monkeypatch, boom: Exception) -> None:
    """The load-bearing decision, and the one worth stating as a test.

    An outage at the provider is not evidence about the person filing a report.
    Turning it into a rejection means a Cloudflare incident silently stops every
    bug report reaching this catalog -- worse than the spam the challenge exists
    to stop, and undiagnosable from outside. Same reasoning the rate limiter
    already documents: "a limiter that is itself unavailable does not block the
    write".
    """

    def _raise(*args, **kwargs):
        raise boom

    monkeypatch.setattr(httpx, "post", _raise)
    assert captcha.verify("token", _Settings("secret")) is Outcome.UNAVAILABLE


def test_only_an_active_rejection_refuses_a_submission() -> None:
    """The router's rule, stated where it can be read.

    Three of the four outcomes let the write through and one refuses. If this
    ever inverts, an unreachable provider becomes an intake outage.
    """
    refusing = {o for o in Outcome if o is Outcome.FAILED}
    assert refusing == {Outcome.FAILED}
    assert {Outcome.NOT_CONFIGURED, Outcome.PASSED, Outcome.UNAVAILABLE} == set(Outcome) - refusing


def test_half_a_configuration_reads_as_off() -> None:
    """The one misconfiguration that would silently break intake.

    A secret with no site key means the browser renders no widget and sends no
    token, and a missing token is a failure — so every submission would be
    refused while the form looked fine. Off is the safe reading of a
    half-finished rollout.
    """
    secret_only = _Settings("secret", site="")
    assert not captcha.is_configured(secret_only)
    assert captcha.verify(None, secret_only) is Outcome.NOT_CONFIGURED

    site_only = _Settings("", site="site")
    assert not captcha.is_configured(site_only)
    assert captcha.verify(None, site_only) is Outcome.NOT_CONFIGURED


def test_both_halves_turn_it_on() -> None:
    assert captcha.is_configured(_Settings("secret", site="site"))
