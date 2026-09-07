"""The example searches the UI suggests must actually find something.

The placeholder in the search box and the help text on an empty result set both
name example queries. They are the first thing a visitor types, and two of them
returned nothing against the published catalog — `"nodal LMP"` and `"ssrd"` —
which reads as the search being broken rather than the example being
aspirational. Worse on the empty state, where the reader is *already* looking at
no results and the advice hands them another none.

So the copy is checked against the data, on the corpus a Pages build publishes.
Neither half can move without the other noticing: rewrite the placeholder and
this runs the new examples; drop a dataset and this says which suggestion went
stale.

`ssrd` is the interesting case and is deliberately **not** required to match. It
is a real column on ERA5 and it resolves to `globalHorizontalIrradiance`, but
free-text search indexes concept labels rather than field local names, so typing
the column finds nothing. The copy now names both halves and tells the reader
which one to search for; this asserts the half it tells them to type.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = ROOT / "web" / "src" / "messages" / "en.json"

#: Synthetic fixtures for the entitlement matrix, which `pages.yml` deletes
#: before loading. Kept in step with `tests/harvest/test_published_identity.py`.
NOT_PUBLISHED = {"caiso-nodal-lmp-restricted", "utility-load-shapes-allowlisted"}


@pytest.fixture(scope="module")
def catalog():
    """The published catalog, in a search index — seed inventory plus curated."""
    from datahub.api.search.backend import InMemorySearchBackend
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from datahub.harvest.seed import SeedLoader
    from datahub.projector.build import build_document
    from fixtures.loader import load_record, record_names

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    for name in record_names():
        if name not in NOT_PUBLISHED:
            records.put(load_record(name))

    backend = InMemorySearchBackend()
    backend.index(
        [
            build_document(records.get_graph(iri, graph=NamedGraph.CATALOG), iri)
            for iri in records.list_ids(graph=NamedGraph.CATALOG)
        ]
    )
    backend.refresh()
    return backend


def hits(backend, query: str) -> int:
    from datahub.api.search import Entitlement, SearchRequest

    return len(
        backend.search(SearchRequest(q=query, limit=20, entitlement=Entitlement.anonymous())).hits
    )


@pytest.fixture(scope="module")
def messages() -> dict:
    return json.loads(MESSAGES.read_text())


def test_every_example_in_the_search_placeholder_finds_something(catalog, messages) -> None:
    """The first thing a visitor types."""
    placeholder = messages["search"]["placeholder"]
    examples = re.findall(r'"([^"]+)"', placeholder)
    assert examples, f"no quoted examples in {placeholder!r} — has the copy changed shape?"

    empty = {example: hits(catalog, example) for example in examples}
    assert all(empty.values()), (
        "the search box suggests queries the published catalog answers with nothing, "
        f"which reads as the search being broken: {[q for q, n in empty.items() if not n]}"
    )


def test_the_advice_on_an_empty_result_set_is_not_itself_a_dead_end(catalog, messages) -> None:
    """Where the reader is already looking at nothing.

    The help text names a concept and tells the reader to search for it. If that
    search is also empty the advice is worse than silence.
    """
    from datahub.api.search import Entitlement, SearchRequest

    concept = "globalHorizontalIrradiance"
    assert "{concept}" in messages["empty"]["noResultsHelp"], (
        "the help text no longer names a concept; update this test with it"
    )
    found = catalog.search(
        SearchRequest(q=concept, limit=20, entitlement=Entitlement.anonymous())
    ).hits
    assert found, f"the empty state tells a reader to search for {concept!r}, which finds nothing"


def test_the_column_the_copy_names_is_a_real_column(catalog) -> None:
    """`ssrd` has to exist, or the sentence is a fiction about the data.

    It is not required to be *findable* — the copy exists precisely because
    searching a column name does not work — but it must be a column ERA5
    actually has, resolving to the concept the copy claims.
    """
    from fixtures.loader import load_record

    fields = {
        node.get("localName"): str(node.get("concept") or "").rsplit("/", 1)[-1]
        for node in load_record("ecmwf-era5")["@graph"]
        if node.get("type") == "Field"
    }
    assert fields.get("ssrd") == "globalHorizontalIrradiance", (
        f"the empty state says ERA5's `ssrd` resolves to globalHorizontalIrradiance; "
        f"the record says {fields.get('ssrd')!r}"
    )
