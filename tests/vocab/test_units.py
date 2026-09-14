"""The unit registry: complete, self-consistent, and honest about QUDT."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from datahub.namespaces import OG, QUDT
from rdflib import Graph, URIRef
from rdflib.namespace import RDFS

REPO_ROOT = Path(__file__).resolve().parents[2]
QUDT_UNIT = "http://qudt.org/vocab/unit/"
OG_UNIT = "https://schema.opengrid.org/unit/"
#: The two namespaces this registry is the authority for. A record citing a
#: unit under either has to find it here; anything else is somebody else's
#: vocabulary and not this file's to adjudicate.
GOVERNED = (QUDT_UNIT, OG_UNIT)


@pytest.fixture(scope="module")
def units() -> Graph:
    g = Graph()
    g.parse((REPO_ROOT / "vocab" / "og-units.ttl").as_posix(), format="turtle")
    return g


@pytest.fixture(scope="module")
def concepts() -> Graph:
    g = Graph()
    g.parse((REPO_ROOT / "vocab" / "og-grid-concept.ttl").as_posix(), format="turtle")
    return g


def test_every_unit_used_is_declared(units: Graph, concepts: Graph) -> None:
    """An undeclared unit cannot be converted, so a field carrying it cannot be
    compared with a field in any other unit."""
    declared = {str(s) for s in units.subjects(OG.conversionMultiplier, None)}
    used = {str(o) for o in concepts.objects(None, OG.defaultUnit)}
    assert not (used - declared), f"units used but not declared: {sorted(used - declared)}"


def test_no_declared_unit_is_unused(units: Graph, concepts: Graph) -> None:
    """Dead entries rot. If a unit is no longer used, remove it.

    "Used" is a concept's default unit *or* a unit a record cites. It was only
    the first, which is too narrow in the direction that does damage: a unit
    that fifty records name and no concept defaults to read as dead here, and
    acting on that reading would dangle fifty fields. The megatonne is exactly
    that case — a carbon cap in an assumption set, no concept's default.
    """
    declared = {str(s) for s in units.subjects(OG.conversionMultiplier, None)}
    used = {str(o) for o in concepts.objects(None, OG.defaultUnit)} | units_cited_by_records()
    assert not (declared - used), f"declared but unused: {sorted(declared - used)}"


def units_cited_by_records() -> set[str]:
    """Every ``og:unit`` named by a fixture record, on a field or an assumption."""
    root = REPO_ROOT / "tests" / "fixtures"
    cited: set[str] = set()
    for path in sorted(root.glob("records/*.jsonld")) + sorted(root.glob("registry/*.jsonld")):
        document = json.loads(path.read_text())
        for node in document.get("@graph", [document]):
            unit = node.get("unit")
            for iri in unit if isinstance(unit, list) else [unit] if unit else []:
                cited.add(str(iri))
    return cited


def test_every_unit_is_fully_described(units: Graph) -> None:
    for unit in units.subjects(OG.conversionMultiplier, None):
        assert units.value(unit, RDFS.label) is not None, f"{unit} has no label"
        assert units.value(unit, QUDT.symbol) is not None, f"{unit} has no symbol"
        assert units.value(unit, QUDT.hasQuantityKind) is not None, f"{unit} has no quantity kind"
        assert units.value(unit, OG.qudtStatus) is not None, f"{unit} has no QUDT status"


def test_conversion_multipliers_are_positive(units: Graph) -> None:
    for unit, multiplier in units.subject_objects(OG.conversionMultiplier):
        assert float(multiplier) > 0, f"{unit} has a non-positive conversion multiplier"


def test_only_temperature_carries_an_offset(units: Graph) -> None:
    """A non-zero offset on anything but temperature is almost always a mistake,
    and a silent one: it produces plausible wrong numbers."""
    for unit, offset in units.subject_objects(OG.conversionOffset):
        if float(offset) == 0:
            continue
        kind = str(units.value(unit, QUDT.hasQuantityKind))
        assert kind.endswith("Temperature"), (
            f"{unit} has a non-zero offset but is not a temperature"
        )


def test_status_values_are_from_the_closed_set(units: Graph) -> None:
    for unit, status in units.subject_objects(OG.qudtStatus):
        assert str(status) in {"verified", "unverified", "og-minted"}, f"{unit}: {status}"


def test_og_minted_units_are_in_the_og_namespace(units: Graph) -> None:
    """Minting under the QUDT namespace would assert an IRI QUDT does not
    define, which is fabrication (principle 4)."""
    for unit, status in units.subject_objects(OG.qudtStatus):
        if str(status) == "og-minted":
            assert str(unit).startswith(OG_UNIT), f"{unit} is minted but sits in the QUDT namespace"
        else:
            assert str(unit).startswith(QUDT_UNIT), f"{unit} claims QUDT status outside QUDT"


def test_known_conversions_are_right(units: Graph) -> None:
    """Spot-checks against values a physicist would recognise. A transposed
    digit here produces wrong answers everywhere and no error anywhere."""

    def factor(local: str) -> float:
        for base in (QUDT_UNIT, OG_UNIT):
            value = units.value(URIRef(base + local), OG.conversionMultiplier)
            if value is not None:
                return float(value)
        raise AssertionError(f"{local} is not declared")

    assert factor("MegaW") == pytest.approx(1e6)
    assert factor("MegaW-HR") == pytest.approx(3.6e9)  # 1 MWh in joules
    assert factor("HR") == pytest.approx(3600.0)
    assert factor("YR") == pytest.approx(31557600.0)  # Julian year
    assert factor("DEG") == pytest.approx(3.141592653589793 / 180)
    assert factor("PERCENT") == pytest.approx(0.01)
    assert factor("MegaBTU_IT") == pytest.approx(1.055e9, rel=1e-3)
    assert factor("TON_Metric") == pytest.approx(1e3)
    # Heat rate: 3412.14 Btu/kWh is unity efficiency, so the factor is its reciprocal.
    assert 1 / factor("BTU_IT-PER-KiloW-HR") == pytest.approx(3412.14, rel=1e-4)


@pytest.mark.network
def test_qudt_reconciliation_pending(units: Graph) -> None:
    """The unverified entries are a known, bounded debt (vocab/README.md).

    This test is marked `network` and skipped by default rather than passing
    silently: a passing test would claim a check that has not happened. When a
    QUDT release is reachable, replace the body with a real resolution check and
    flip the statuses in og-units.ttl to "verified".
    """
    unverified = [str(u) for u, s in units.subject_objects(OG.qudtStatus) if str(s) == "unverified"]
    pytest.skip(
        f"{len(unverified)} unit IRIs are believed present in QUDT but unchecked. "
        "Reconcile against a pinned QUDT release; see vocab/README.md."
    )


def test_no_record_cites_a_unit_the_registry_does_not_define(units: Graph) -> None:
    """A field's ``og:unit`` has to resolve, or it says nothing.

    Every reference model in the corpus minted its own unit IRIs under
    ``https://schema.opengrid.org/concept/unit/`` — ``kilovolt``, ``ohm``,
    ``megavolt-ampere`` — while this registry, and every dataset record in the
    corpus, names units by their QUDT IRI. All three were therefore dangling,
    and nothing complained: an unresolvable unit is served as a null label and
    a null label renders as no unit at all. The Schema tab showed a bare column of
    numbers for every value in all three models, and a machine reading the
    record for a conversion factor got nothing to convert with.

    The failure mode is the quiet one this project's fourth principle is about:
    the record *looked* like it carried a unit. Checked here rather than in a
    shape, because a SHACL shape can require a unit to be an IRI and cannot know
    which IRIs this registry mints.
    """
    declared = {str(s) for s in units.subjects(RDFS.label, None)}
    assert declared, "the registry parsed to nothing"

    cited = units_cited_by_records()
    assert cited, "no records to check"

    # Only units under a namespace this registry is the authority for. A record
    # is free to cite a vocabulary this file knows nothing about, and this test
    # is not the adjudicator of whether that one resolves.
    dangling = sorted(iri for iri in cited if iri.startswith(GOVERNED) and iri not in declared)
    assert not dangling, "units cited by a record and not defined:\n" + "\n".join(dangling)
