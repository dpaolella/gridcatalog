"""`docs/api.md` is generated, and stays so (WP-10.3).

A hand-maintained API reference is correct on the day it is written and wrong
by the end of the month, invisibly. This test is what makes "generated" a fact
rather than a claim: add a route without regenerating the page and it fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DOC = REPO_ROOT / "docs" / "api.md"


def _generated() -> str:
    from datahub.api.app import create_app
    from datahub.api.docgen import to_markdown

    return to_markdown(create_app().openapi()).rstrip() + "\n"


def test_the_api_reference_is_current() -> None:
    assert API_DOC.read_text() == _generated(), (
        "docs/api.md is stale. Regenerate it: datahub openapi --markdown docs/api.md"
    )


def test_every_endpoint_appears_in_the_reference(client) -> None:
    """A route the document does not mention is one no client author finds."""
    document = client.get("/openapi.json").json()
    page = API_DOC.read_text()

    for path in document["paths"]:
        assert f"`{path}`" in page, f"{path} is missing from docs/api.md"


def test_the_reference_states_the_two_rules_a_client_author_needs() -> None:
    """The control-plane rule and the indistinguishable 404. Everything else in
    the page is a table; these two are the things that will otherwise be
    discovered the hard way."""
    page = API_DOC.read_text()

    assert "never returns data" in page
    assert "existence oracle" in page
