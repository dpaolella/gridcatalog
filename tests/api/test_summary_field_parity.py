"""The page cannot ask a list row for a field the API never sends.

The fifth instance of one mistake: a name that has to be kept identical across
layers by hand. The licence filter was broken in both directions at once; the
MCP server sent `concept=` for two milestones and had it dropped; fourteen
facets were advertised and filtered nothing; `share_alike` reached the search
document and stopped at the API row. Each was fixed by writing the name in one
more place, and each time the *next* layer went unchecked.

This one is the layer below the facets: `web/src/lib/api.ts` declares a
`DatasetSummary` interface, the API returns a `DatasetSummary` model, and
TypeScript checks the interface against itself rather than against the server.
A field the page declares and the server never sends is `undefined` at runtime,
with no error anywhere — and on the static site, where `StaticSearch` filters
the rows in the browser, `undefined` is a filter that silently matches nothing.

That is not hypothetical. `format` and `has_usage_evidence` are both in
`services/snapshot.py`'s `FACETS`, so the published site rendered them as
clickable filters with real bucket counts — counts aggregated from the search
index, where the fields exist — and clicking one returned zero results, because
the rows being filtered never carried them (#87).

Only one direction is asserted. A field the API sends and the page ignores is
waste; a field the page reads and the API never sends is a bug.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.schemas import DatasetSummary

WEB_CLIENT = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "api.ts"

#: Fields the page declares that the live API is not expected to send.
#:
#: One entry, and it is documented on both sides rather than merely tolerated:
#: `datahub snapshot export` computes `search_text` and adds it to each row,
#: and `web/src/lib/api.ts` says in the field's own comment that "the live API
#: never sends it". The static site has only list rows, so without it a record
#: with no summary is unfindable by any word describing it.
#:
#: Deliberately not a general allow-list. An exemption for a field the API does
#: not send is the same silence moved somewhere harder to notice, and a list of
#: things a test agrees to ignore only ever grows.
SNAPSHOT_ONLY = {"search_text"}


def web_summary_fields() -> set[str]:
    """Top-level property names of the `DatasetSummary` interface.

    Parsed rather than generated, for the reason `tests/snapshot/test_facet_parity.py`
    parses TSX: the alternative is a build step that emits TypeScript from the
    Pydantic models, and until that exists the names are kept identical by hand
    — which is exactly the thing that keeps failing.
    """
    source = WEB_CLIENT.read_text()
    start = source.index("export interface DatasetSummary {")
    body = source[start : source.index("\n}\n", start)]
    # Strip comments first: a doc comment mentioning `record_type:` would
    # otherwise be read as a declaration, and a parser that invents fields
    # reports failures that are not there.
    body = re.sub(r"/\*\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return set(re.findall(r"^\s*(\w+)\??:", body, flags=re.M))


def test_every_field_the_page_reads_is_one_the_api_sends() -> None:
    declared = web_summary_fields()
    assert declared, "parsed no fields out of the web DatasetSummary — the parser is broken"

    missing = declared - set(DatasetSummary.model_fields) - SNAPSHOT_ONLY
    assert missing == set(), (
        f"web/src/lib/api.ts declares {sorted(missing)} on DatasetSummary and the "
        f"API never sends them on a list row. They are `undefined` in the browser "
        f"with no error, and any filter or search over them silently matches "
        f"nothing. Add them to services/api/schemas.py's DatasetSummary and to "
        f"from_document."
    )


def test_the_snapshot_exception_is_still_an_exception() -> None:
    """If the API starts sending `search_text`, this allow-list is a lie.

    The point of naming one exception rather than keeping a general allow-list
    is that the exception has to stay true. A stale entry is how an allow-list
    starts growing.
    """
    overlap = SNAPSHOT_ONLY & set(DatasetSummary.model_fields)
    assert overlap == set(), (
        f"{sorted(overlap)} is listed as snapshot-only but the API model now "
        f"declares it. Remove it from SNAPSHOT_ONLY."
    )
