"""An allow-list grant must actually grant.

Two ways it silently did not. Both left the operational row intact, so the
custodian's own view of the list showed the person they had added — while
entitlement, which is evaluated against the *index*, did not know about them.
A grant that looks applied and is not is worse than one that fails loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HIDDEN = "utility-load-shapes-allowlisted"
IRI = f"https://catalog.opengrid.org/ds/{HIDDEN}"


@pytest.fixture
def granted_by_email(client, loaded):
    """A grant made by address, for a user who already has an account."""
    from datahub.api import deps
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories
    from datahub.projector import reindex

    address = "By.Address@example.org"
    with session_scope() as session:
        repos = Repositories(session)
        user = repos.users.upsert_federated(
            "local", "by-address", email=address, display_name="by address"
        )
        session.flush()
        token = tokens.mint(repos, user, name="t", scopes=("catalog:read",)).token
        repos.allowlist.grant(IRI, granted_by="custodian", principal_email=address)

    reindex(loaded, deps.search_backend(), session_factory=session_scope)
    return token


def test_a_grant_by_email_reaches_the_index(granted_by_email):
    """`entitled_principals` projects addresses alongside ids, deliberately.

    Its docstring says why: someone granted access by address before they had an
    account must still match once they sign in. The projection did that from the
    start; the *matching* side only ever compared `principal_id`, so every email
    grant was inert.
    """
    from datahub.api import deps
    from datahub.api.search.document import FACET_FIELDS  # noqa: F401  (import check)

    doc = next(d for d in deps.search_backend().all_documents() if d.id == HIDDEN)
    assert any("@" in value for value in doc.entitled_principals)


def test_a_grant_by_email_lets_its_subject_see_the_record(client, granted_by_email):
    response = client.get(
        f"/v1/datasets/{HIDDEN}", headers={"Authorization": f"Bearer {granted_by_email}"}
    )
    assert response.status_code == 200, (
        "a grant the custodian made, and can see in their own list, did not let "
        "its subject read the record"
    )


def test_email_matching_ignores_case(client, granted_by_email):
    """The grant was written `By.Address@example.org`; the account holds it as
    the provider returned it. A custodian typing different casing must not
    silently grant nothing."""
    from datahub.api.search.backend import Entitlement
    from datahub.api.search.document import SearchDocument

    doc = SearchDocument(
        id="x",
        iri="https://example.org/x",
        title="x",
        visibility="allowlisted-existence",
        entitled_principals=["By.Address@example.org"],
    )
    assert Entitlement(principal_id="u", email="by.address@EXAMPLE.org").can_see_existence(doc)


def test_the_search_backend_is_flushed_on_shutdown(api_env):
    """A grant re-projected in-process must survive a restart.

    `_reproject` writes the changed document straight into the backend so a
    custodian's grant takes effect immediately. With the file-backed backend
    that write lived only in memory: `deps.reset()` flushed the graph store and
    dropped the search backend without flushing it, so the next process read the
    file as the last full reindex left it.
    """
    from datahub.api import deps
    from datahub.api.search.document import SearchDocument

    backend = deps.search_backend()
    backend.index([SearchDocument(id="survivor", iri="https://example.org/s", title="Survivor")])
    deps.reset()

    reloaded = deps.search_backend()
    assert any(d.id == "survivor" for d in reloaded.all_documents()), (
        "the document was dropped on shutdown, so any allow-list grant made "
        "since the last full reindex is gone"
    )
