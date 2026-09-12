"""The four registry types, and the constraints that stop them lying.

A dataset record describes something somebody else holds, so the worst it can
do is describe it badly. These four *are* the thing: the Hub holds their bytes
and issues a citation that has to keep resolving. Every constraint asserted
below is one where a permissive shape would let the registry make a promise it
cannot keep, and each is tested in both directions — the fixture proves the
rule is satisfiable, the negative case proves it is enforced.

The negative half is the half that matters. A shape nobody has watched reject
anything is a shape that might be targeting a class no record carries, and it
would pass this file's positive assertions unchanged while enforcing nothing.
"""

from __future__ import annotations

import json

import pytest
from datahub.validate import ValidationRunner
from rdflib import Graph

from tests.fixtures.loader import context, load_graph, registry_names

CONTEXT = "https://schema.opengrid.org/context/opengrid-datahub.jsonld"


@pytest.fixture(scope="module")
def runner() -> ValidationRunner:
    return ValidationRunner()


def graph_of(*nodes: dict) -> Graph:
    document = {"@context": context()["@context"], "@graph": list(nodes)}
    graph = Graph()
    graph.parse(data=json.dumps(document), format="json-ld")
    return graph


def messages(runner: ValidationRunner, graph: Graph) -> str:
    report = runner.validate(graph, target_level=1)
    return "\n".join(v.message for v in report.violations)


# ---- the fixtures conform ------------------------------------------------


@pytest.mark.parametrize("name", registry_names())
def test_every_registry_fixture_conforms(runner: ValidationRunner, name: str) -> None:
    report = runner.validate(load_graph(name), target_level=1)
    assert report.conforms, "\n".join(v.message for v in report.violations)


def test_all_four_types_are_covered_by_a_fixture() -> None:
    """Guards the parametrised test above from quietly covering three types.

    Deleting a fixture would shrink `registry_names()` and every remaining test
    would still pass, so the type list is asserted rather than inferred.
    """
    seen = set()
    for name in registry_names():
        document = json.loads(
            (__import__("pathlib").Path("tests/fixtures/registry") / f"{name}.jsonld").read_text()
        )
        for node in document["@graph"]:
            declared = node["type"]
            seen.update([declared] if isinstance(declared, str) else declared)
    assert {"Study", "AssumptionSet", "ReferenceModel", "RunRecord"} <= seen


# ---- a promise the registry cannot keep ----------------------------------


STUDY = {
    "id": "https://catalog.opengrid.org/study/t",
    "type": "Study",
    "title": "T",
    "studyKind": "filing",
    "publisher": "https://catalog.opengrid.org/agent/a",
    "analysisType": ["https://schema.opengrid.org/concept/analysis-type/capacityExpansion"],
    "hasAssumptionSet": ["https://catalog.opengrid.org/assumptions/t"],
}


def test_a_frozen_study_without_a_citation_is_refused(runner: ValidationRunner) -> None:
    """og:frozenAt promises the version will not change. Without og:citationId
    there is no handle to cite, so a reader ends up citing a moving target —
    which is the single failure a registry exists to prevent."""
    frozen = STUDY | {"frozenAt": "2026-04-02T00:00:00Z"}
    assert "citation" in messages(runner, graph_of(frozen)).lower()

    citable = frozen | {"citationId": "opengrid:study/t@1.0"}
    assert runner.validate(graph_of(citable), target_level=1).conforms


def test_an_intervention_names_what_it_contests(runner: ValidationRunner) -> None:
    """An intervention with no parent is an assertion floating free of the
    filing it argues with, and the regulator story is exactly putting the two
    side by side."""
    orphan = STUDY | {"studyKind": "intervention"}
    assert "parentStudy" in messages(runner, graph_of(orphan))

    attached = orphan | {"parentStudy": "https://catalog.opengrid.org/study/parent"}
    assert runner.validate(graph_of(attached), target_level=1).conforms


def test_a_study_cannot_be_its_own_parent(runner: ValidationRunner) -> None:
    loop = STUDY | {"studyKind": "intervention", "parentStudy": STUDY["id"]}
    assert "own parent" in messages(runner, graph_of(loop))


def test_a_study_needs_an_assumption_set(runner: ValidationRunner) -> None:
    """Without one the study is a PDF, and nothing an intervenor can fork."""
    bare = {k: v for k, v in STUDY.items() if k != "hasAssumptionSet"}
    assert "assumption set" in messages(runner, graph_of(bare))


# ---- the pin, and the receipt --------------------------------------------


ASSUMPTION = {
    "id": "https://catalog.opengrid.org/assumption/t",
    "type": "Assumption",
    "assumptionPath": "Investments/Financials/TechnologyFinancialData#return_on_equity",
    "assumptionValue": "0.098",
    "valueBasis": "estimated",
    "fieldSource": ["https://catalog.opengrid.org/ds/s"],
}
ASSUMPTION_SET = {
    "id": "https://catalog.opengrid.org/assumptions/t",
    "type": "AssumptionSet",
    "title": "T",
    "schemaPin": "3325d27c8c626a375c7492f1341af139d5686e53",
    "hasAssumption": [ASSUMPTION["id"]],
}


def test_the_schema_pin_must_be_a_commit_sha(runner: ValidationRunner) -> None:
    """A version string identifies nothing here: SiennaSchemas publishes no
    tags at all and promises no compatibility between pre-1.0 releases, so
    "validated against sienna-schemas v0.9" is worth nothing six months on."""
    for bad in ("v0.9.0", "latest", "main", "3325d27"):
        graph = graph_of(ASSUMPTION_SET | {"schemaPin": bad}, ASSUMPTION)
        assert "commit SHA" in messages(runner, graph), f"{bad!r} was accepted as a pin"

    assert runner.validate(graph_of(ASSUMPTION_SET, ASSUMPTION), target_level=1).conforms


def test_a_registered_set_validated_clean(runner: ValidationRunner) -> None:
    """A non-zero error count means the Hub published a document its own
    validator rejected, which is the one thing an intake receipt is for."""
    dirty = ASSUMPTION_SET | {"receiptErrors": 2}
    assert "validator rejected" in messages(runner, graph_of(dirty, ASSUMPTION))

    clean = ASSUMPTION_SET | {"receiptErrors": 0, "receiptWarnings": 3}
    assert runner.validate(graph_of(clean, ASSUMPTION), target_level=1).conforms


def test_an_assumption_cites_its_source_and_states_its_basis(runner: ValidationRunner) -> None:
    """ "Trace every data source behind a financing proposal" is a query over
    og:fieldSource. One assumption without it breaks the chain silently."""
    sourceless = {k: v for k, v in ASSUMPTION.items() if k != "fieldSource"}
    assert "cites where its value came from" in messages(
        runner, graph_of(ASSUMPTION_SET, sourceless)
    )

    basisless = {k: v for k, v in ASSUMPTION.items() if k != "valueBasis"}
    assert "measured, estimated, modeled or synthetic" in messages(
        runner, graph_of(ASSUMPTION_SET, basisless)
    )


# ---- fidelity, declared rather than asserted -----------------------------


REFERENCE_MODEL = {
    "id": "https://catalog.opengrid.org/ds/t-net",
    "type": ["Dataset", "ReferenceModel"],
    "title": "T",
    "description": "D",
    "fidelityClass": "screening",
    "dataDomain": ["https://schema.opengrid.org/concept/data-domain/DD1"],
    "provenanceClass": "https://schema.opengrid.org/concept/provenance-class/synthetic",
    "license": "https://spdx.org/licenses/CC-BY-4.0",
    "accessRestriction": "https://schema.opengrid.org/concept/access-restriction/none",
    "anonymousAccess": True,
    "documentationStatus": "partial",
    "completenessLevel": 1,
    "reviewState": "confirmed",
    "harvestSource": "curated",
    "distribution": ["https://catalog.opengrid.org/dist/t-net--d"],
}
DISTRIBUTION = {
    "id": "https://catalog.opengrid.org/dist/t-net--d",
    "type": "Distribution",
    "accessURL": "https://example.org/t-net.json",
}


def test_fidelity_is_a_declared_field_and_a_closed_one(runner: ValidationRunner) -> None:
    """The Hub vision asks whether fidelity is "a declared, machine-readable
    field on each reference model, or prose in a README", and notes that
    without a declared value the escalation path has no defined step. The
    shape answers it: required, and one of three."""
    undeclared = {k: v for k, v in REFERENCE_MODEL.items() if k != "fidelityClass"}
    assert "fidelityClass" in messages(runner, graph_of(undeclared, DISTRIBUTION))

    invented = REFERENCE_MODEL | {"fidelityClass": "high"}
    assert "indicative, screening or authoritative" in messages(
        runner, graph_of(invented, DISTRIBUTION)
    )


def test_authority_must_name_what_it_rests_on(runner: ValidationRunner) -> None:
    """Authority asserted without evidence is the claim a commission discounts,
    and it takes the rest of the record down with it."""
    bare = REFERENCE_MODEL | {"fidelityClass": "authoritative"}
    assert "authority rests on" in messages(runner, graph_of(bare, DISTRIBUTION))

    evidenced = bare | {"questionClassPartition": ["https://catalog.opengrid.org/question-class/q"]}
    partition = {
        "id": "https://catalog.opengrid.org/question-class/q",
        "type": "QuestionClass",
        "questionClass": "Relative system cost between two portfolios",
        "robustness": "robust",
        "robustnessBasis": "Agreement study, 41 of 44 paired cases.",
    }
    assert runner.validate(graph_of(evidenced, DISTRIBUTION, partition), target_level=1).conforms


def test_robust_needs_a_basis_and_unknown_does_not(runner: ValidationRunner) -> None:
    """Admitting a limit costs a reader nothing; claiming one does. That
    asymmetry is why "unknown" is free and "robust" is not."""
    claimed = {
        "id": "https://catalog.opengrid.org/question-class/q",
        "type": "QuestionClass",
        "questionClass": "Local congestion outcomes",
        "robustness": "robust",
    }
    assert "names what established it" in messages(runner, graph_of(claimed))

    admitted = claimed | {"robustness": "unknown"}
    assert runner.validate(graph_of(admitted), target_level=1).conforms


def test_hosting_a_registered_object_is_not_an_exception(runner: ValidationRunner) -> None:
    """The custody inversion reaching the shapes.

    "Hosting is by exception" was written when nothing the Hub held was its
    own. Four of the five reasons still say "this lives somewhere else and we
    took a copy anyway, here is why"; the fifth says "this lives here, and that
    is the promise". A reader who cannot tell those apart does not know whether
    the bytes will be there next year, which is why it is a fifth value rather
    than a relaxation of the rule.

    The refresh-owner half is untouched by any of it — the reason a copy is
    held changed, the way a hub accumulates stale mirrors did not.
    """
    hosted = {
        "id": "https://catalog.opengrid.org/dist/t-net--d",
        "type": "Distribution",
        "accessURL": "https://hub.opengrid.org/reference/t/v1/system.json",
        "hostedByOpenGrid": True,
        "refreshOwner": "https://catalog.opengrid.org/agent/opengrid-reference-models",
    }
    assert runner.validate(
        graph_of(hosted | {"hostingReason": "registered"}), target_level=1
    ).conforms

    invented = hosted | {"hostingReason": "because we felt like it"}
    assert "State why OpenGrid holds these bytes" in messages(runner, graph_of(invented))

    unowned = {k: v for k, v in hosted.items() if k != "refreshOwner"}
    assert "refresh owner" in messages(runner, graph_of(unowned | {"hostingReason": "registered"}))


# ---- the Hub registers runs; it does not execute them --------------------


RUN = {
    "id": "https://catalog.opengrid.org/run/t",
    "type": "RunRecord",
    "ranAssumptionSet": "https://catalog.opengrid.org/assumptions/t",
    "onReferenceModel": "https://catalog.opengrid.org/ds/t-net",
    "executedBy": "Somebody, and not the Hub",
    "caseHash": "9f2c4a17b8e35d6042cf91ae7b3d8c5019e6f4a2b7c8d093e15f2a6b4c7d8e90",
    "tool": "PyPSA",
    "toolVersion": "0.31.2",
    "executedAt": "2026-03-28T11:42:06Z",
}


def test_a_run_names_who_executed_it(runner: ValidationRunner) -> None:
    """Solving is out of scope and two of the vision's open questions ask
    whether the Hub ever hosts compute. An unattributed result answers both in
    the affirmative by implication."""
    anonymous = {k: v for k, v in RUN.items() if k != "executedBy"}
    assert "who executed this run" in messages(runner, graph_of(anonymous))


def test_a_run_identifies_the_case_it_solved(runner: ValidationRunner) -> None:
    """Two runs of "the same" study on an edited case are different runs.
    Without the hash a re-run that quietly changed an input reads as a
    reproduction."""
    hashless = {k: v for k, v in RUN.items() if k != "caseHash"}
    assert "caseHash" in messages(runner, graph_of(hashless))

    truncated = RUN | {"caseHash": "9f2c4a17"}
    assert "SHA-256" in messages(runner, graph_of(truncated))


def test_a_tool_without_a_version_reproduces_nothing(runner: ValidationRunner) -> None:
    unversioned = {k: v for k, v in RUN.items() if k != "toolVersion"}
    assert "reproduces nothing" in messages(runner, graph_of(unversioned))


def test_an_objective_value_carries_its_unit(runner: ValidationRunner) -> None:
    """A bare number compared against another study's bare number is how two
    incompatible results get called a disagreement."""
    unitless = RUN | {"objectiveValue": 18420000000.0}
    assert "carries its unit" in messages(runner, graph_of(unitless))

    united = unitless | {"objectiveUnit": "http://qudt.org/vocab/unit/USD"}
    assert runner.validate(graph_of(united), target_level=1).conforms
