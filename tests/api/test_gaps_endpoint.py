"""`/v1/gaps`, and the boundary it must not cross (#56).

A gap is not a dataset. Separate route, separate response model, separate
count — because one appearing in `/v1/datasets` would be the catalog asserting
a record for something that does not exist, which is the opposite of why the
register exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.gaps import register


def test_the_endpoint_answers_and_counts_separately(client) -> None:
    response = client.get("/v1/gaps?q=nodal+demand")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["gaps"][0]["id"] == "nodal-demand"
    assert "datasets" not in body, "a gap count must not be reported as a dataset count"


def test_the_response_carries_the_attribution(client) -> None:
    gap = client.get("/v1/gaps?q=nodal+demand").json()["gaps"][0]
    assert gap["observed_by"]
    assert gap["observed"]
    assert "stale" in gap


def test_gaps_never_appear_in_a_dataset_search(client) -> None:
    """The boundary. A gap in `/v1/datasets` would be the catalog asserting a
    record for something that does not exist, which is the opposite of why the
    register exists."""
    ids = {gap.id for gap in register()}
    results = client.get("/v1/datasets?limit=200").json()
    assert not (ids & {row["id"] for row in results["results"]})


def test_the_snapshot_exports_the_whole_register() -> None:
    """The static site matches client-side, so a page of the register is not
    enough — and the static site is what most readers see."""
    from datahub import snapshot

    source = Path(snapshot.__file__).read_text()
    assert '"gaps.json"' in source, "the exporter no longer writes the register"
    assert "/v1/gaps?limit=100" in source, (
        "the exporter is writing a default page of the register rather than all of it"
    )
