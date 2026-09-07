"""Usage evidence: who has actually used a dataset.

The value of this field is that it answers "has anyone built anything with
this", which is the question a modeller asks after coverage. Its risk is that a
fabricated citation is worse than no citation — a reader carries it into their
own bibliography and it acquires a second life there.

So most of what is defended here is the second thing. Nothing may be inferred,
an entry a reader cannot follow is dropped rather than shown, and every entry
says who asserts it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import InMemorySearchBackend
from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import CONTAINMENT_PREDICATES, RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.adapters.yaml_repo import YamlRepoAdapter
from datahub.namespaces import OG
from datahub.projector import Projector
from fixtures.loader import load_record

# ---- pulling it out of the source ---------------------------------------


def _entry(**at_work) -> dict:
    return {"Name": "A dataset", "DataAtWork": at_work}


def test_each_section_keeps_the_kind_it_came_from() -> None:
    """A peer-reviewed study and a demo notebook both show the dataset was
    used, and they are not the same claim. Flattening the three sections into
    one list without the kind would make a record with six tutorials look like
    a record with six papers."""
    got = YamlRepoAdapter._usage_evidence(
        _entry(
            Publications=[{"Title": "A paper", "URL": "https://example.org/p"}],
            Tutorials=[{"Title": "A notebook", "URL": "https://example.org/t"}],
            **{"Tools & Applications": [{"Title": "A viewer", "URL": "https://example.org/v"}]},
        )
    )
    assert [e["_kind"] for e in got] == ["publication", "tutorial", "tool"]


def test_an_entry_with_no_url_is_dropped() -> None:
    """The rule the whole field turns on. A citation a reader cannot follow is
    an assertion, and this is where an unverifiable one does real damage."""
    got = YamlRepoAdapter._usage_evidence(
        _entry(Publications=[{"Title": "No link"}, {"Title": "Fine", "URL": "https://example.org"}])
    )
    assert [e["_title"] for e in got] == ["Fine"]


def test_an_entry_with_no_title_is_dropped() -> None:
    got = YamlRepoAdapter._usage_evidence(_entry(Publications=[{"URL": "https://example.org"}]))
    assert got == []


def test_a_record_with_no_data_at_work_yields_nothing() -> None:
    assert YamlRepoAdapter._usage_evidence({"Name": "A dataset"}) == []
    assert YamlRepoAdapter._usage_evidence({"Name": "x", "DataAtWork": "not a dict"}) == []


REGISTRY = Path("var/harvest/aws_open_data/datasets")


@pytest.mark.skipif(not REGISTRY.is_dir(), reason="no AWS registry clone in var/harvest")
def test_a_real_registry_entry_yields_real_citations() -> None:
    """Over the actual file, because what breaks a parser is real data: a
    section key with an ampersand and a space in it, entries with no author,
    and URLs carrying query strings."""
    entry = yaml.safe_load((REGISTRY / "nrel-pds-wtk.yaml").read_text())
    got = YamlRepoAdapter._usage_evidence(entry)

    assert len(got) >= 6
    titles = [e["_title"] for e in got]
    assert "The Wind Integration National Dataset (WIND) Toolkit" in titles
    assert all(e["_url"].startswith("http") for e in got)
    assert {"publication", "tool"} <= {e["_kind"] for e in got}


# ---- surviving the store -------------------------------------------------


def test_usage_evidence_is_a_containment_predicate() -> None:
    """Omitting it is the bug that silently dropped `og:provenanceGap` on 231
    records: the node writes fine, is not gathered as part of the record, and
    vanishes on the next read. Nothing else in the suite would catch it."""
    assert OG.usageEvidence in CONTAINMENT_PREDICATES


def test_it_survives_a_rewrite() -> None:
    """The containment rule, exercised rather than asserted."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)

    document = load_record("ecmwf-era5")
    dataset_node(document)["usageEvidence"] = [
        {
            "id": "https://catalog.opengrid.org/ds/ecmwf-era5#usage-0",
            "type": "UsageEvidence",
            "title": "A study that used ERA5",
            "accessURL": "https://example.org/study",
            "evidenceKind": "publication",
            "assertedBy": "curated",
        }
    ]
    records.put(document, graph=NamedGraph.DRAFT)

    read_back = dataset_node(records.get("ecmwf-era5")).get("usageEvidence") or []
    assert len(read_back) == 1
    assert read_back[0]["title"] == "A study that used ERA5"
    assert read_back[0]["assertedBy"] == "curated"

    records.put(records.get("ecmwf-era5"), graph=NamedGraph.DRAFT)
    assert len(dataset_node(records.get("ecmwf-era5")).get("usageEvidence") or []) == 1


# ---- the shape -----------------------------------------------------------


@pytest.mark.parametrize(
    ("drop", "expected"),
    [("accessURL", "resolvable URL"), ("title", "title"), ("assertedBy", "who asserts")],
)
def test_the_shape_refuses_an_entry_missing_what_makes_it_checkable(
    drop: str, expected: str
) -> None:
    """Each of these turns evidence back into an assertion, which is the thing
    this field must not become."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)

    node = {
        "id": "https://catalog.opengrid.org/ds/ecmwf-era5#usage-0",
        "type": "UsageEvidence",
        "title": "A study",
        "accessURL": "https://example.org/study",
        "evidenceKind": "publication",
        "assertedBy": "curated",
    }
    del node[drop]
    document = load_record("ecmwf-era5")
    dataset_node(document)["usageEvidence"] = [node]

    report = records.runner.validate_jsonld(document, 1)
    messages = " ".join(str(getattr(v, "message", v)) for v in report.violations)
    assert not report.conforms
    assert expected in messages


def test_the_shape_refuses_an_invented_evidence_kind() -> None:
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    document = load_record("ecmwf-era5")
    dataset_node(document)["usageEvidence"] = [
        {
            "id": "https://catalog.opengrid.org/ds/ecmwf-era5#usage-0",
            "type": "UsageEvidence",
            "title": "A study",
            "accessURL": "https://example.org/study",
            "evidenceKind": "blog-post",
            "assertedBy": "curated",
        }
    ]
    assert not records.runner.validate_jsonld(document, 1).conforms


def test_a_record_with_no_usage_evidence_still_validates() -> None:
    """Optional at every level, and deliberately. Coverage is a property of the
    source — most catalogues have no field that could carry this — so requiring
    it would fail datasets for their catalogue's schema (PRD §14.2)."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    assert records.runner.validate_jsonld(load_record("ecmwf-era5"), 1).conforms


# ---- reaching the reader -------------------------------------------------


def test_it_reaches_the_search_document_and_the_facet() -> None:
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)

    document = load_record("ecmwf-era5")
    dataset_node(document)["usageEvidence"] = [
        {
            "id": "https://catalog.opengrid.org/ds/ecmwf-era5#usage-1",
            "type": "UsageEvidence",
            "title": "A tutorial",
            "accessURL": "https://example.org/t",
            "evidenceKind": "tutorial",
            "assertedBy": "aws_open_data",
        },
        {
            "id": "https://catalog.opengrid.org/ds/ecmwf-era5#usage-0",
            "type": "UsageEvidence",
            "title": "A study",
            "accessURL": "https://example.org/s",
            "evidenceKind": "publication",
            "assertedBy": "aws_open_data",
        },
    ]
    records.put(document)

    doc = Projector(records, InMemorySearchBackend()).document_for("ecmwf-era5")
    assert doc is not None
    assert doc.has_usage_evidence is True
    assert doc.usage_evidence_count == 2
    # Publications first: a list that opens with demo notebooks answers a
    # different question from the one the reader asked.
    assert [e.kind for e in doc.usage_evidence] == ["publication", "tutorial"]
    assert doc.usage_evidence[0].asserted_by == "aws_open_data"


def test_a_record_without_it_is_false_rather_than_null() -> None:
    """The facet has two buckets and a reader can click either. A null would
    make "nothing recorded" unfilterable, which is the half of the catalog this
    field is most likely to describe."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    records.put(load_record("ecmwf-era5"))

    doc = Projector(records, InMemorySearchBackend()).document_for("ecmwf-era5")
    assert doc is not None
    assert doc.has_usage_evidence is False
    assert doc.usage_evidence_count == 0
    assert doc.usage_evidence == []


# ---- what is committed ---------------------------------------------------


CURATED = Path(__file__).resolve().parents[2] / "data" / "catalog" / "curated"


@pytest.mark.skipif(not CURATED.is_dir(), reason="no exported catalog in this checkout")
def test_every_committed_entry_names_a_source_and_a_url() -> None:
    """Over the real catalog files, which is where a fabricated citation would
    actually live. An entry that fails this is one somebody would cite."""
    import json

    checked = 0
    for path in sorted(CURATED.glob("*.jsonld")):
        node = next(
            (n for n in json.loads(path.read_text())["@graph"] if n.get("type") == "Dataset"),
            None,
        )
        for entry in (node or {}).get("usageEvidence") or []:
            checked += 1
            assert entry.get("title"), path.name
            assert str(entry.get("accessURL", "")).startswith("http"), path.name
            assert entry.get("assertedBy"), f"{path.name}: an anonymous citation cannot be weighed"
            assert entry.get("evidenceKind") in {"publication", "tutorial", "tool"}, path.name
    assert checked, "no curated usage evidence is committed, so this asserts nothing"
