"""The analysis-type scheme against the framework it models (#51).

The scheme had eight Tier 2 concepts; the OpenGrid data-landscape framework
names ten, and adds seven composite workflows that a utility or a regulator
actually runs. It also draws a distinction the scheme could not express:
whether a dataset is an **input to** an analysis or an **output of** one.

    Demand Forecasting's Output in DD4 (load forecasts) becomes Capacity
    Expansion's Input in DD4. Resource Adequacy's Output in DD2 (ELCC values)
    becomes Capacity Expansion's Input in DD2. These chains are where embedded
    assumptions propagate.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from datahub.namespaces import OG

VOCAB = Path(__file__).resolve().parents[1] / "vocab" / "og-analysis-type.ttl"
AT = "https://schema.opengrid.org/concept/analysis-type/"

#: The framework's Tier 2 classes. Two were missing before this.
TIER_2 = {
    "capacityExpansion",
    "productionCost",
    "resourceAdequacy",
    "transmissionPlanning",
    "distributionPlanning",
    "demandForecasting",
}
#: Its Tier 3 composite workflows, all seven.
TIER_3 = {
    "interconnectionStudy",
    "generationRetirementStudy",
    "costBenefitAnalysis",
    "integratedResourcePlan",
    "longTermTransmissionPlan",
    "tyndp",
    "tpl001",
}


@pytest.fixture(scope="module")
def vocab() -> Graph:
    graph = Graph()
    graph.parse(VOCAB, format="turtle")
    return graph


def notations(graph: Graph) -> set[str]:
    return {str(s).removeprefix(AT) for s in graph.subjects(SKOS.inScheme, None)}


def test_every_tier_2_class_the_framework_names_is_in_the_scheme(vocab) -> None:
    missing = TIER_2 - notations(vocab)
    assert not missing, f"the framework names these and the scheme does not: {sorted(missing)}"


def test_every_composite_workflow_is_in_the_scheme(vocab) -> None:
    missing = TIER_3 - notations(vocab)
    assert not missing, sorted(missing)


def test_a_composite_names_what_it_is_made_of(vocab) -> None:
    """`og:composedOf`, so a dataset fit for every constituent is derivably fit
    for the workflow rather than being told twice."""
    for workflow in TIER_3:
        parts = list(vocab.objects(URIRef(f"{AT}{workflow}"), OG.composedOf))
        assert parts, f"{workflow} is a composite and names no constituents"
        for part in parts:
            assert (part, SKOS.inScheme, None) in vocab, (workflow, str(part))


def test_a_composite_is_not_broader_than_its_parts(vocab) -> None:
    """The direction that would be wrong.

    Making IRP `skos:broader` capacity expansion lets a broader-transitive
    query conclude that an IRP dataset *is* a capacity expansion dataset, and
    the entailment pass materialises exactly that.
    """
    for workflow in TIER_3:
        assert not list(vocab.objects(URIRef(f"{AT}{workflow}"), SKOS.broader)), workflow


def test_every_concept_states_its_input_domains(vocab) -> None:
    """`og:typicalInputDomain` is what the link service derives shared workflow
    tags from, so a concept without it is invisible to PRD §F6.6."""
    for notation in TIER_2 | TIER_3:
        iri = URIRef(f"{AT}{notation}")
        assert list(vocab.objects(iri, OG.typicalInputDomain)), notation


def test_the_two_new_tier_2_classes_say_why_they_are_not_covered_already(vocab) -> None:
    """A concept added without a scope note is one nobody can apply correctly."""
    for notation in ("distributionPlanning", "demandForecasting"):
        note = vocab.value(URIRef(f"{AT}{notation}"), SKOS.scopeNote)
        assert note is not None and len(str(note)) > 80, notation
