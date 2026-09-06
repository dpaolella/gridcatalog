"""Auto-promotion gates (WP-11.8, ADR-0012).

The rule these tests exist to hold: **a record reaches the public catalog
without a person only when the pipeline can substantiate it**, and the two
things that would actually harm a user if got wrong — a licence nobody
established, a link that goes nowhere — are the two the gates are aimed at.

Everything here is a fact about the record. There is deliberately no test for a
per-source trust list, because there deliberately is none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.promote import AUTO_CONFIRMED, promote, verdict

SPDX = "http://spdx.org/licenses/"


def record(**overrides) -> dict:
    """A record that passes every gate, so a test can break exactly one."""
    base = {
        "id": "https://catalog.opengrid.org/ds/example",
        "type": "Dataset",
        "reviewState": "draft",
        "license": f"{SPDX}CC-BY-4.0",
        "distribution": [{"id": "https://catalog.opengrid.org/dist/example--csv"}],
        "_validation_conforms": True,
    }
    base.update(overrides)
    return base


def test_a_substantiated_record_promotes() -> None:
    result = promote(record())
    assert result.promoted
    assert result.refusals == []


def test_promotion_stamps_a_state_that_is_not_confirmed() -> None:
    """`confirmed` means a person checked it. Conflating the two would make the
    catalog unable to say which of its records anybody has looked at."""
    document = record()
    promote(document)
    assert document["reviewState"] == AUTO_CONFIRMED
    assert document["reviewState"] != "confirmed"


def test_a_record_a_person_confirmed_is_never_restamped() -> None:
    """A human judgement outranks this one, and overwriting it would erase the
    fact that somebody looked."""
    document = record(reviewState="confirmed")
    result = promote(document)
    assert not result.promoted
    assert document["reviewState"] == "confirmed"


# ---- the licence gate ----------------------------------------------------


@pytest.mark.parametrize(
    "licence",
    [
        f"{SPDX}LicenseRef-Unreviewed-creative-commons",
        f"{SPDX}LicenseRef-Unstated",
        "",
    ],
)
def test_an_unresolved_licence_refuses_promotion(licence: str) -> None:
    """The harm is specific: a reader sees an identifier, reuses the data on
    the strength of it, and nobody ever established the terms (PRD §7.4)."""
    result = promote(record(license=licence))
    assert not result.promoted
    assert any(d.gate == "licence" for d in result.refusals)


def test_a_resolved_licenseref_is_fine() -> None:
    """`LicenseRef-US-Gov-Public-Domain` is a reviewed identifier with real
    terms behind it. Only the `Unreviewed` and `Unstated` markers are refusals."""
    assert promote(record(license=f"{SPDX}LicenseRef-US-Gov-Public-Domain")).promoted


# ---- the link gate -------------------------------------------------------


def test_a_record_with_no_distribution_refuses() -> None:
    result = promote(record(distribution=[]))
    assert not result.promoted
    assert any(d.gate == "links" for d in result.refusals)


def test_a_link_never_probed_does_not_block() -> None:
    """The plan said "probed 200 within 30 days". Taken literally that blocks
    the first run, when nothing has been probed and nothing can be. Not-known-
    dead is the gate; it tightens on its own as probe history accumulates
    (ADR-0012)."""
    assert promote(record(), health={}).promoted


def test_every_distribution_unreachable_refuses() -> None:
    document = record()
    health = {"https://catalog.opengrid.org/dist/example--csv": "unreachable"}
    result = promote(document, health)
    assert not result.promoted
    assert any(d.gate == "links" for d in result.refusals)


def test_one_live_distribution_is_enough() -> None:
    """A dataset reachable by any of its paths is reachable. Refusing it
    because one mirror died would hide a working dataset."""
    document = record(
        distribution=[
            {"id": "https://catalog.opengrid.org/dist/example--csv"},
            {"id": "https://catalog.opengrid.org/dist/example--api"},
        ]
    )
    health = {"https://catalog.opengrid.org/dist/example--csv": "unreachable"}
    assert promote(document, health).promoted


# ---- the drafted-value gate ----------------------------------------------


def test_a_drafted_value_in_a_gating_field_refuses() -> None:
    result = promote(record(enrichedField=["summary", "license"]))
    assert not result.promoted
    refusal = next(d for d in result.refusals if d.gate == "drafted-values")
    assert "license" in refusal.reason


def test_a_drafted_value_in_a_describing_field_is_fine() -> None:
    """The line ADR-0005 already draws: a model may describe a dataset and may
    not state facts about the world only the source can state."""
    assert promote(record(enrichedField=["summary", "dataDomain", "keyword"])).promoted


# ---- reporting -----------------------------------------------------------


def test_every_gate_runs_so_one_pass_reports_every_problem() -> None:
    """A steward fixing one refusal at a time and re-running is the slowest
    possible way to learn what is wrong with a record."""
    result = verdict(record(license="", distribution=[], _validation_conforms=False))
    assert len(result.refusals) == 3
    assert {d.gate for d in result.refusals} == {"validates", "licence", "links"}


def test_a_refusal_says_which_gate_and_why() -> None:
    """The queue shows this instead of leaving a steward to guess."""
    why = promote(record(license=f"{SPDX}LicenseRef-Unstated")).why_not
    assert "licence" in why
    assert "unresolved" in why


# ---- regressions ---------------------------------------------------------


def test_the_gates_read_a_framed_document_not_only_a_bare_node() -> None:
    """A record read back from the store is `{"@context", "@graph": [...]}`.

    The gates first read `record["license"]` off the top level, so every field
    came back absent and **every record was refused for stating no licence** —
    0 of 524 promoted while the gates looked like they worked. The unit tests
    did not catch it because they all passed bare nodes, which is why this one
    passes a document.
    """
    document = {
        "@context": "https://schema.opengrid.org/context/opengrid-datahub.jsonld",
        "@graph": [record()],
    }
    result = promote(document)
    assert result.promoted
    assert document["@graph"][0]["reviewState"] == AUTO_CONFIRMED


def test_the_validation_gate_is_not_a_rubber_stamp() -> None:
    """It took the caller's word for it, on the reasoning that a draft record
    was validated when it was written. The harvest runner writes drafts with
    `validate=False` on purpose, so the gate passed everything and `--dry-run`
    reported 353 promotable records where 121 were."""
    assert not promote(record(_validation_conforms=False)).promoted
