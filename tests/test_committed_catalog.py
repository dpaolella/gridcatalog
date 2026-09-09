"""A committed record must not shadow one the build regenerates (#18 regression).

`pages.yml` loads the catalog in three passes, and the last one wins:

    datahub seed load                      # 127 records, from data/seed-sources.yaml
    datahub record load var/site/curated   # 17 golden-set fixtures
    for source in data/catalog/*/          # whatever a harvest run committed

`data/catalog/curated/` held 91 records, **every one of them reproducible** by
the first two passes and every one a snapshot taken before #18. So the third
pass overwrote 23 corrected records with their pre-fix versions, and
`https://opengrid.org/catalog/no-known-access-path` — a URL invented to satisfy
a shape, which #18 removed — was live again on the published site with a
working "Open at source" button on it.

Nothing failed. Both halves were internally consistent; the bug lived in the
order they were loaded.

`data/catalog/yaml_repo/` stays, and the distinction is the point:
a harvested record cannot be regenerated without re-running a harvest against a
third party, so git is the only place it can live. A record the build derives
from a file already in git is a cache, and a cache of the wrong vintage is
worse than no cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "data" / "catalog"
FIXTURES = ROOT / "tests" / "fixtures" / "records"

#: The URL #18 removed. Named so this test is about that specific fabrication
#: and not about any URL that happens to look odd.
SENTINEL = "https://opengrid.org/catalog/no-known-access-path"


def committed_slugs() -> dict[str, Path]:
    return {p.stem: p for p in COMMITTED.glob("*/*.jsonld")}


@pytest.fixture(scope="module")
def generated() -> set[str]:
    """Every slug the build produces from a file already in git."""
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from datahub.harvest.seed import SeedLoader

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    seeded = {
        dataset_id.rsplit("/", 1)[-1]
        for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for dataset_id in records.list_ids(graph=graph)
    }
    return seeded | {p.stem for p in FIXTURES.glob("*.jsonld")}


#: Empty, and it has to stay that way now (#68).
#:
#: This used to waive `esa-worldcover` and `nasa-merra-2` — one dataset arriving
#: twice, once because a human listed it and once because a harvester found it —
#: on the grounds that nothing was lost while both copies carried the same 24
#: fields. `harvest/auto` showed what that waiver was actually protecting: two
#: more arrived, `ecmwf-era5` and `global-wind-atlas`, and the harvested ERA5
#: had **0** fields and a `LicenseRef-Unreviewed-generated-…` licence against
#: the golden record's 4 hand-authored plus 273 persisted. Loaded last, it wins.
#:
#: `record export` now refuses any slug `data/seed-sources.yaml` regenerates, so
#: a collision cannot reach git at all and the two waived files were deleted.
#: Identity resolution (#65 WP-2) is still the real fix — this stops the damage,
#: it does not merge the two sources into one record.
KNOWN_COLLISIONS: set[str] = set()


def test_no_committed_record_shadows_a_generated_one(generated) -> None:
    """The bug, stated as the rule that would have caught it.

    A committed record whose slug the build also derives is loaded last and
    wins, so it silently pins that record to whenever the file was written. It
    cannot be spotted by reading either half.
    """
    shadowing = sorted((set(committed_slugs()) & generated) - KNOWN_COLLISIONS)
    assert not shadowing, (
        "these committed records duplicate ones the build generates from "
        "data/seed-sources.yaml or the golden set, and are loaded after them, so "
        f"the committed vintage wins: {shadowing}"
    )


def test_the_known_collisions_have_not_multiplied(generated) -> None:
    """One dataset, two records, and the loader picks by file order.

    This is the identity-resolution gap the self-population work has to close.
    Asserting equality rather than a ceiling so that *fixing* one shows up here
    too, and the list shrinks deliberately rather than drifting.
    """
    assert set(committed_slugs()) & generated == KNOWN_COLLISIONS


def test_no_committed_record_carries_the_fabricated_access_url() -> None:
    """Belt and braces, and cheap. The one above is the general rule; this names
    the specific value so a failure says immediately what went wrong."""
    carrying = sorted(
        slug for slug, path in committed_slugs().items() if SENTINEL in path.read_text()
    )
    assert not carrying, f"#18's fabricated access URL is back in committed data: {carrying}"


def test_the_harvested_records_are_still_committed() -> None:
    """The other half of the rule, so the fix is not "delete everything".

    A harvested record cannot be regenerated without re-running a harvest
    against a third party. Git is the only place it can live, and a change that
    removed it would cost the catalog its entire harvested corpus.
    """
    harvested = list((COMMITTED / "yaml_repo").glob("*.jsonld"))
    assert len(harvested) > 100, (
        f"only {len(harvested)} harvested records are committed; these cannot be "
        "rebuilt from anything in this repository"
    )
