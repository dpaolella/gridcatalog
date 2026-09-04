"""Crosswalk honesty rules X1-X4 (PRD §4.4), asserted over the whole corpus.

`tests/vocab/test_crosswalks.py` checks these inside the crosswalk files. These
check them as properties of the *records*, which is where they can be violated
by a harvester rather than by an editor.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph
from rdflib.namespace import SKOS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.validate import ValidationRunner
from datahub.namespaces import OG
from fixtures.loader import corpus_graph, load_graph, record_names

GC = "https://schema.opengrid.org/concept/grid-concept/"


@pytest.fixture(scope="module")
def corpus() -> Graph:
    return corpus_graph()


@pytest.fixture(scope="module")
def runner() -> ValidationRunner:
    return ValidationRunner()


# ---- X1 ------------------------------------------------------------------


def test_x1_no_record_carries_its_own_external_mapping(corpus: Graph) -> None:
    """X1: 'Concept-to-external-scheme mappings are authored once as shared
    versioned SKOS schemes, never per dataset.'

    A record that mapped its own column to a CIM attribute would be asserting
    privately what the crosswalk asserts publicly, and the two would diverge.
    """
    for predicate in (SKOS.exactMatch, SKOS.closeMatch, SKOS.broadMatch, SKOS.relatedMatch):
        offenders = list(corpus.subject_objects(predicate))
        assert not offenders, (
            f"a record asserts {predicate.split('#')[-1]} directly: {offenders[:3]}. "
            "External mappings belong in vocab/crosswalks/."
        )


# ---- X3 ------------------------------------------------------------------


def test_x3_every_inferred_assignment_states_a_basis(corpus: Graph) -> None:
    """X3: 'An inferred concept assignment must be flagged as inferred with a
    stated basis.'"""
    for field in corpus.subjects(OG.inferredAssignment, None):
        basis = corpus.value(field, OG.inferenceBasis)
        assert basis is not None, f"{field} is flagged inferred with no stated basis"
        assert len(str(basis)) > 40, (
            f"{field}'s inference basis is too short to be a basis: {basis!r}. "
            "It has to say what the inference was made from, not that one was made."
        )


def test_x3_inferred_assignments_exist_in_the_corpus(corpus: Graph) -> None:
    """A corpus with no inferred assignment cannot test the rule that governs
    them, and enrichment will produce them constantly."""
    inferred = list(corpus.subjects(OG.inferredAssignment, None))
    assert inferred, "no fixture exercises an inferred concept assignment"


def test_x3_an_unflagged_inference_is_rejected(runner: ValidationRunner) -> None:
    assert not runner.validate(load_graph("inferred-concept-without-basis"), 3).conforms


# ---- X4 ------------------------------------------------------------------


def test_x4_gap_markers_state_a_reason(corpus: Graph) -> None:
    """X4: 'A field with no confident mapping carries an explicit gap marker. It
    is never silently omitted.'"""
    gaps = list(corpus.objects(None, OG.conceptGap))
    assert gaps, "no fixture exercises a concept gap"
    for gap in gaps:
        reason = corpus.value(gap, OG.gapReason)
        assert reason is not None, f"{gap} is a bare gap marker"
        assert len(str(reason)) > 40, f"{gap}'s reason is too short to be one: {reason!r}"


def test_x4_level_three_fields_all_resolve_or_declare_a_gap(corpus: Graph) -> None:
    """The corpus-wide form of the rule: across every level-3 record, no field
    is silently unmapped."""
    from fixtures.loader import declared_level

    unmapped: list[str] = []
    for name in record_names():
        if declared_level(name) < 3:
            continue
        graph = load_graph(name)
        for dataset in graph.subjects(OG.hasField, None):
            for field in graph.objects(dataset, OG.hasField):
                has_concept = graph.value(field, OG.concept) is not None
                has_gap = graph.value(field, OG.conceptGap) is not None
                if not (has_concept or has_gap):
                    unmapped.append(f"{name}:{field}")
    assert not unmapped, f"level 3 fields with neither concept nor gap: {unmapped}"


def test_x4_every_concept_reference_resolves(corpus: Graph) -> None:
    """A dangling concept IRI is worse than a gap: it looks like coverage and
    silently drops the field from every concept query."""
    scheme = Graph()
    scheme.parse(
        (Path(__file__).resolve().parents[2] / "vocab" / "og-grid-concept.ttl").as_posix(),
        format="turtle",
    )
    known = {str(c) for c in scheme.subjects(None, SKOS.Concept)}
    for _, concept in corpus.subject_objects(OG.concept):
        assert str(concept) in known, f"record references unknown concept {concept}"


# ---- principle 2: absent means not captured ------------------------------


def test_no_record_asserts_an_empty_upstream_list(corpus: Graph) -> None:
    """PRD principle 2: a missing field means 'not captured', never 'does not
    exist'. An explicitly empty list would be the second, dressed as the first.

    Where a record genuinely has no upstream — a primary observation — it says
    so with og:upstreamSourceUncaptured false rather than with an empty list.
    """
    from rdflib.namespace import RDF

    for subject, obj in corpus.subject_objects(OG.upstreamSource):
        assert obj != RDF.nil, f"{subject} asserts an empty upstream list"


# ---- principle 4: grounded or absent -------------------------------------


def test_no_fixture_invents_a_licence(corpus: Graph) -> None:
    """Every licence in the corpus is either an SPDX identifier or carries an
    og:licenseNote explaining why it is not."""
    from rdflib.namespace import DCTERMS

    for dataset, licence in corpus.subject_objects(DCTERMS.license):
        text = str(licence)
        if text.startswith("https://spdx.org/licenses/LicenseRef-"):
            note = corpus.value(dataset, OG.licenseNote)
            assert note is not None, (
                f"{dataset} uses a LicenseRef with no og:licenseNote explaining the terms. "
                "A LicenseRef identifier means 'not in the SPDX list', which is only "
                "informative if the record says what the terms actually are."
            )
        else:
            assert text.startswith("https://spdx.org/licenses/"), (
                f"{dataset} carries a licence IRI that is neither SPDX nor a LicenseRef: {text}"
            )
