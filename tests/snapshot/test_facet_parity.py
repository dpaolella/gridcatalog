"""The two filter panels must offer the same filters.

There are two builds of the same page. The server-rendered one asks the API
for facets on every request; the static one renders from `facets.json`, written
once at export time. Each reads its list from a different file, in a different
language:

    web/src/app/datasets/page.tsx    const FACETS = [...]
    services/snapshot.py            FACETS = (...)

`snapshot.py`'s comment said "the same list the server-rendered search asks
for". It was not. It carried `has_usage_evidence`, which the page never
requested, and it lacked `field_count_bucket` when that was added -- so the
field-count filter shipped, rendered on a local run, and was **absent from the
published site**, which is where it was found.

Silent in both directions: a facet the response omits simply does not render,
and a facet requested by only one build makes the same URL behave differently
depending on which one you opened.

This is the cross-language twin of `tests/api/test_filter_facet_parity.py`,
which exists because the licence filter was broken in both directions at once
for exactly this reason -- a name kept identical by hand in two places.
"""

from __future__ import annotations

import re
from pathlib import Path

from datahub.api.search.document import FACET_FIELDS
from datahub.snapshot import FACETS as SNAPSHOT_FACETS

ROOT = Path(__file__).resolve().parents[2]

#: The catalog page, which is `/datasets` since the nav became four peers and
#: was `/` before that. Hardcoded rather than globbed for a file containing a
#: `FACETS` array: a glob would silently find the wrong page the day a second
#: one grows a facet panel, and the whole point of this module is that a list
#: kept in step by hand is a list that drifts. Moving the page fails here with
#: the path in the message, which is the failure you want.
PAGE = ROOT / "web" / "src" / "app" / "datasets" / "page.tsx"


def page_facets() -> list[str]:
    """The `FACETS` array from the search page, read from the source.

    Parsed rather than duplicated here, because a third copy of the list would
    be a third thing to keep in step and this file exists to stop that.

    Line comments are stripped before the quoted strings are read. The array
    carries a comment per facet explaining why that filter exists, and any
    quoted word inside one — a facet value, an example query — would otherwise
    be read as a facet name and reported as a disagreement between two files
    that agree. That happened the first time a comment quoted a search term.
    """
    source = PAGE.read_text()
    match = re.search(r"const FACETS = \[(.*?)\];", source, re.S)
    assert match, f"no `const FACETS = [...]` in {PAGE.relative_to(ROOT)}"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return re.findall(r'"([a-z_]+)"', body)


def test_both_builds_request_the_same_facets() -> None:
    page = set(page_facets())
    snapshot = set(SNAPSHOT_FACETS)
    assert page == snapshot, (
        "the server-rendered and static filter panels disagree, so the same page "
        "offers different filters depending on which build you open. "
        f"only in page.tsx: {sorted(page - snapshot)}; "
        f"only in snapshot.py: {sorted(snapshot - page)}"
    )


def test_every_requested_facet_is_one_the_backend_can_compute() -> None:
    """A name neither build can resolve returns no bucket and renders nothing.

    Cheap to assert here and it closes the loop: the two lists agreeing is only
    useful if what they agree on is real.
    """
    unknown = sorted(set(page_facets()) - set(FACET_FIELDS))
    assert not unknown, (
        f"these facets are requested by the UI and are not keys of FACET_FIELDS, "
        f"so they silently return nothing: {unknown}"
    )


def test_the_field_count_facet_reaches_the_static_site() -> None:
    """The specific regression, named so a failure says what broke.

    #46's whole point is that a reader can find the ~34 records with a real
    schema. On the static site -- the published one -- they could not.
    """
    assert "field_count_bucket" in SNAPSHOT_FACETS
    assert "field_count_bucket" in page_facets()
