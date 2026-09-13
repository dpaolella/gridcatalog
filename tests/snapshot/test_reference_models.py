"""Registry records must survive the same read path as ordinary datasets."""

import json
from pathlib import Path

import pytest
from datahub.api import deps
from datahub.graph.records import dataset_node
from datahub.projector import reindex
from datahub.snapshot import export


@pytest.mark.parametrize(
    ("fixture", "slug", "fields", "distributions"),
    [
        ("cascade-reference-model", "cascade-interconnect-reference", 0, 1),
        ("gb-osm-reference-model", "gb-osm-reference", 6, 2),
    ],
)
def test_reference_model_survives_graph_api_and_snapshot(
    client, loaded, tmp_path, fixture, slug, fields, distributions
):
    path = Path(__file__).resolve().parents[1] / "fixtures" / "registry" / f"{fixture}.jsonld"
    source = json.loads(path.read_text())
    # Both legal orders, including an ordinary Dataset with an additional type.
    source["@graph"][0]["type"] = ["ReferenceModel", "Dataset"]
    loaded.put(source)
    before = loaded.get_graph(slug)
    record = loaded.get(slug)
    root = dataset_node(record)
    assert set(root["type"]) == {"Dataset", "ReferenceModel"}
    assert record["@graph"][0] is root
    assert len(root["distribution"]) == distributions
    assert all(isinstance(value, dict) for value in root["distribution"])
    loaded.put(record)
    assert before.isomorphic(loaded.get_graph(slug))
    reindex(loaded, deps.search_backend())

    schema = client.get(f"/v1/datasets/{slug}/schema")
    paths = client.get(f"/v1/datasets/{slug}/distributions")
    assert schema.status_code == paths.status_code == 200
    assert len(schema.json()["fields"]) == fields
    assert len(paths.json()) == distributions
    assert all(path["download_url"] for path in paths.json())

    directory = tmp_path / "snapshot"
    export(directory)
    part = directory / "datasets" / slug
    assert json.loads((part / "schema.json").read_text()) == schema.json()
    assert json.loads((part / "distributions.json").read_text()) == paths.json()


@pytest.mark.parametrize(
    "types", ["Dataset", ["Dataset"], ["Dataset", "ReferenceModel"], ["ReferenceModel", "Dataset"]]
)
def test_dataset_membership_accepts_scalar_and_multiple_types(types):
    node = {"id": "https://example.org/model", "type": types}
    assert dataset_node(node) is node
    assert dataset_node({"@graph": [node]}) is node
