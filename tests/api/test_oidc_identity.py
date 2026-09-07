"""What an identity provider tells us, and what we are entitled to conclude.

An address read out of a provider's user payload is not a display detail. The
allow-list grants by email, `entitled_principals` projects addresses into the
index, and `Entitlement._entitled` matches on them — so `read_user`'s middle
return value is an **authorization identity**, and a caller who can make a
provider emit an address they do not control inherits every grant made to it.

`Entitlement.email` was documented as "the caller's verified address" and
nothing verified it. These tests pin the three providers to what each one
actually asserts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.entitlement.oidc import PROVIDERS


def read(provider: str, payload: dict) -> tuple[str, str | None, str | None]:
    return PROVIDERS[provider].read_user(payload)


def test_google_email_is_taken_only_when_google_says_it_verified_it() -> None:
    """`email_verified` is in the userinfo response and was ignored.

    False is not hypothetical: a Workspace account federated from an external
    IdP can carry an address Google has not confirmed, and that address was
    being treated as proof of who the caller is.
    """
    subject, email, _ = read("google", {"sub": "1", "email": "a@x.org", "email_verified": True})
    assert (subject, email) == ("1", "a@x.org")

    _, unverified, _ = read("google", {"sub": "1", "email": "a@x.org", "email_verified": False})
    assert unverified is None, "an address Google flags unverified was matched against grants"


def test_a_google_payload_with_no_verification_claim_is_not_trusted() -> None:
    """Absent is unverified. A userinfo response without the claim is not one
    from the endpoint this reads, and guessing in the permissive direction is
    the wrong way to be wrong about an authorization identity."""
    assert read("google", {"sub": "1", "email": "a@x.org"})[1] is None


def test_a_microsoft_login_name_is_not_read_as_an_address() -> None:
    """A UPN is shaped like an address and need not be one.

    `alice@contoso.onmicrosoft.com` is the common form. It was used as the
    fallback when Graph returned no `mail`, so a sign-in name was matched
    against allow-list grants.
    """
    _, mailbox, _ = read("microsoft", {"id": "9", "mail": "alice@contoso.com"})
    assert mailbox == "alice@contoso.com"

    _, upn_only, _ = read(
        "microsoft", {"id": "9", "userPrincipalName": "alice@contoso.onmicrosoft.com"}
    )
    assert upn_only is None, "a UPN was read as a verified mailbox"


def test_github_returns_the_public_profile_address_or_nothing() -> None:
    """GitHub only lets a user publish one of their verified addresses, so this
    one is asserted rather than typed. Null is the ordinary case — most users
    keep it private — and must not become a sign-in failure."""
    assert read("github", {"id": 7, "email": "dev@x.org", "login": "dev"})[1] == "dev@x.org"
    assert read("github", {"id": 7, "login": "dev"})[1] is None


def test_a_caller_with_no_usable_address_still_signs_in() -> None:
    """The safe answer is `None`, not a refusal. Identity is `(provider,
    subject)`; the address only ever adds allow-list reach, so dropping it costs
    the caller nothing they were entitled to."""
    for provider, payload in (
        ("google", {"sub": "1", "email": "a@x.org", "email_verified": False}),
        ("microsoft", {"id": "9", "userPrincipalName": "a@x.onmicrosoft.com"}),
        ("github", {"id": 7, "login": "dev"}),
    ):
        subject, email, _ = read(provider, payload)
        assert subject and email is None, provider
