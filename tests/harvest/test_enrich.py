"""LLM-assisted enrichment (WP-3.6).

ADR-0005: *no guardrail may depend on the agent's cooperation.* So the model in
these tests does not cooperate. It returns licences, access URLs, byte sizes and
identifiers, and the tests assert that none of them reaches the record.

That is the whole point of the file. A test suite that only exercised a
well-behaved model would prove the prompt is polite, not that the guardrail
holds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.enrich import (
    ENRICHABLE_FIELDS,
    Enricher,
    EnrichmentUnavailable,
    ScriptedClient,
)
from datahub.harvest.enrich.client import DisabledClient, make_client

RECORD = {
    "id": "https://catalog.opengrid.org/ds/nrel-wind-toolkit",
    "type": "Dataset",
    "title": "NREL Wind Integration National Dataset (WIND) Toolkit",
    "description": "Modeled wind speed and power output time series at 2km resolution.",
    "license": "http://spdx.org/licenses/CC-BY-4.0",
    "keyword": ["wind"],
    "reviewState": "draft",
    "completenessLevel": 1,
    "distribution": [
        {
            "id": "https://catalog.opengrid.org/dist/nrel-wind-toolkit--0",
            "accessURL": "https://oedi-data-lake.s3.amazonaws.com/wtk/v1/",
            "formatLabel": "HDF5",
        }
    ],
}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "true")
    from datahub.config import get_settings, reset_settings

    reset_settings()
    return get_settings()


def enricher(enabled, answers) -> tuple[Enricher, ScriptedClient]:
    client = ScriptedClient(answers)
    return Enricher(enabled, client=client), client


# ---- the allow-list ------------------------------------------------------


def test_a_fabricated_licence_never_reaches_the_record(enabled) -> None:
    """ "A fabricated license is worse than a missing one" (PRD §7.4). The model
    here returns one anyway; the filter is what stops it, not the prompt."""
    naked = {k: v for k, v in RECORD.items() if k != "license"}
    subject, _ = enricher(
        enabled,
        [{"summary": "Wind resource time series.", "license": "http://spdx.org/licenses/CC0-1.0"}],
    )

    result = subject.enrich(naked)
    applied = subject.apply(naked, result)

    assert "license" not in result.drafted
    assert "license" not in applied
    assert "fabricated licence" in result.refused["license"]


@pytest.mark.parametrize(
    "forbidden",
    [
        {"license": "MIT"},
        {"accessURL": "https://example.org/invented.zip"},
        {"downloadURL": "https://example.org/invented.zip"},
        {"byteSize": 123456},
        {"checksum": "sha256:0000"},
        {"persistentId": "https://doi.org/10.9999/invented"},
        {"conceptDoi": "https://doi.org/10.9999/invented"},
        {"upstreamSource": ["https://catalog.opengrid.org/ds/ecmwf-era5"]},
        {"supersedes": ["https://catalog.opengrid.org/ds/something"]},
        {"reviewState": "confirmed"},
        {"visibility": "public"},
        {"completenessLevel": 3},
        {"id": "https://catalog.opengrid.org/ds/renamed"},
        {"qualityGrade": ["https://catalog.opengrid.org/grade/a"]},
    ],
)
def test_every_forbidden_field_is_dropped(enabled, forbidden: dict) -> None:
    """One case per rule in PRD §7.4 and ADR-0005, because a filter with a hole
    in it looks exactly like a filter without one until the hole is used."""
    subject, _ = enricher(enabled, [{"summary": "A summary.", **forbidden}])

    result = subject.enrich(RECORD)

    term = next(iter(forbidden))
    assert term not in result.drafted
    assert term in result.refused
    assert result.refused[term]


def test_a_refusal_says_which_rule_it_broke(enabled) -> None:
    """So a steward reading a harvest report learns something, and so a change
    to the rules is visible in the log rather than only in a diff."""
    subject, _ = enricher(enabled, [{"byteSize": 1, "accessURL": "https://x.example"}])
    result = subject.enrich(RECORD)

    assert "looks measured" in result.refused["byteSize"]
    assert "does not resolve" in result.refused["accessURL"]


def test_an_unknown_field_is_dropped_too(enabled) -> None:
    """The allow-list is a whitelist, not a blacklist. A field nobody
    anticipated is refused by default rather than passed through."""
    subject, _ = enricher(enabled, [{"somethingNewAndPlausible": "value"}])
    result = subject.enrich(RECORD)

    assert result.drafted == {}
    assert "ADR-0005" in result.refused["somethingNewAndPlausible"]


def test_the_forbidden_list_and_the_allowed_list_do_not_overlap() -> None:
    from datahub.harvest.enrich.enricher import FORBIDDEN_REASON

    assert not (ENRICHABLE_FIELDS & set(FORBIDDEN_REASON))


def test_no_enrichable_field_states_a_fact_only_the_source_knows() -> None:
    """The line the allow-list draws: enrichment describes a dataset, it does
    not state facts about the world that only its publisher can state."""
    facts = {
        "license",
        "accessURL",
        "downloadURL",
        "byteSize",
        "checksum",
        "persistentId",
        "distribution",
        "upstreamSource",
        "id",
        "reviewState",
        "visibility",
    }
    assert not (ENRICHABLE_FIELDS & facts)


# ---- never overwrite -----------------------------------------------------


def test_a_value_the_source_stated_is_never_replaced(enabled) -> None:
    """The most damaging thing this component could do: replace a fact about
    the source with a nicer-sounding inference, indistinguishable from real
    metadata at the point of use."""
    subject, _ = enricher(enabled, [{"keyword": ["a much better keyword list"]}])

    result = subject.enrich(RECORD)
    applied = subject.apply(RECORD, result)

    assert applied["keyword"] == ["wind"]
    assert "keyword" not in result.drafted


def test_only_the_missing_fields_are_asked_for(enabled) -> None:
    """A model shown a field it was not asked about will sometimes return an
    improved version. Not asking is a cheaper guarantee than not accepting."""
    subject, client = enricher(enabled, [{"summary": "x"}])
    subject.enrich(RECORD)

    assert "keyword" not in client.prompts[0].split("Draft only these fields:")[1]
    assert "summary" in client.prompts[0].split("Draft only these fields:")[1]


def test_apply_returns_a_new_document(enabled) -> None:
    """The caller decides whether to keep it."""
    subject, _ = enricher(enabled, [{"summary": "Wind resource time series."}])
    result = subject.enrich(RECORD)

    applied = subject.apply(RECORD, result)

    assert applied is not RECORD
    assert "summary" not in RECORD


# ---- provenance on every drafted value -----------------------------------


def test_a_drafted_value_carries_its_model_and_prompt_version(enabled) -> None:
    """A SHACL constraint, not a convention: without both, a bad prompt's output
    cannot be identified or revoked in bulk (ADR-0005)."""
    subject, _ = enricher(enabled, [{"summary": "Wind resource time series."}])
    applied = subject.apply(RECORD, subject.enrich(RECORD))

    assert applied["enrichmentBasis"] == "inferred"
    assert applied["enrichmentModel"] == "scripted-model"
    assert applied["enrichmentPromptVersion"] == "test.1"
    assert applied["enrichedField"] == ["summary"]


def test_an_enriched_record_still_validates(enabled) -> None:
    """The end that matters. The X3 shape targets any node carrying
    og:enrichmentBasis, so a record that lost its model id fails here."""
    from datahub.harvest.validate import ValidationRunner, format_report

    complete = {
        **RECORD,
        "dataDomain": ["https://schema.opengrid.org/concept/data-domain/DD5"],
        "provenanceClass": "https://schema.opengrid.org/concept/provenance-class/modeled",
        "anonymousAccess": True,
        "accessRestriction": "https://schema.opengrid.org/concept/access-restriction/none",
        "modified": "2024-11-02T09:14:00Z",
        "documentationStatus": "external-standard-only",
        "harvestSource": "ckan",
    }
    subject, _ = enricher(enabled, [{"summary": "Modeled wind resource time series."}])
    applied = subject.apply(complete, subject.enrich(complete))

    report = ValidationRunner().validate_jsonld(applied, 1)
    assert report.conforms, format_report(report)


def test_a_record_missing_its_model_id_fails_validation() -> None:
    """Proves the previous test is checking something. Without this, a shape
    that silently stopped firing would look like a passing suite."""
    from datahub.harvest.validate import ValidationRunner

    broken = {
        **RECORD,
        "enrichmentBasis": "inferred",
        "enrichedField": ["summary"],
        "summary": "A summary with no attribution.",
    }
    report = ValidationRunner().validate_jsonld(broken, 1)

    assert not report.conforms
    assert any("prompt version" in v.message for v in report.violations)


# ---- gap markers ---------------------------------------------------------


def test_a_gap_is_recorded_rather_than_omitted(enabled) -> None:
    """X4: a field with no confident mapping carries a stated reason. A missing
    field means "not captured", never "does not exist"."""
    subject, _ = enricher(
        enabled,
        [
            {
                "summary": "Wind data.",
                "conceptGaps": [
                    {
                        "field": "wtk_cf",
                        "reason": "no concept for a turbine-specific capacity factor",
                    }
                ],
            }
        ],
    )
    result = subject.enrich(RECORD)
    applied = subject.apply(RECORD, result)

    assert len(applied["conceptGap"]) == 1
    assert applied["conceptGap"][0]["gapReason"].startswith("no concept for")
    assert applied["conceptGap"][0]["fieldId"] == "wtk_cf"


def test_a_gap_with_no_reason_is_not_a_gap_marker(enabled) -> None:
    """ "I could not map this", with no reason, is the silent omission the
    marker exists to replace."""
    subject, _ = enricher(
        enabled, [{"conceptGaps": [{"field": "x"}, {"field": "y", "reason": ""}]}]
    )
    assert subject.enrich(RECORD).gaps == []


# ---- concept values ------------------------------------------------------


def test_concept_codes_become_iris(enabled) -> None:
    """The model returns "DD5" and the record carries the IRI. A model asked
    for full IRIs will eventually invent one."""
    subject, _ = enricher(
        enabled, [{"dataDomain": ["DD5", "DD10"], "provenanceClass": "reanalysis"}]
    )
    result = subject.enrich(RECORD)

    assert result.drafted["dataDomain"] == [
        "https://schema.opengrid.org/concept/data-domain/DD5",
        "https://schema.opengrid.org/concept/data-domain/DD10",
    ]
    assert result.drafted["provenanceClass"].endswith("/reanalysis")


def test_a_runaway_summary_is_truncated(enabled) -> None:
    """Not wrong exactly, but a 3,000-character summary makes the list view
    unreadable and the index unhelpful."""
    subject, _ = enricher(enabled, [{"summary": "x" * 5000}])
    assert len(subject.enrich(RECORD).drafted["summary"]) <= 300


def test_a_runaway_list_is_capped(enabled) -> None:
    subject, _ = enricher(enabled, [{"spatialLabel": [f"place {n}" for n in range(60)]}])
    assert len(subject.enrich(RECORD).drafted["spatialLabel"]) <= 12


def test_an_empty_value_is_not_written(enabled) -> None:
    """Writing "" is worse than writing nothing: the field then looks captured
    and the completeness level counts it."""
    subject, _ = enricher(enabled, [{"summary": "   ", "spatialLabel": []}])
    assert subject.enrich(RECORD).drafted == {}


# ---- the switch ----------------------------------------------------------


def test_enrichment_is_off_by_default(settings) -> None:
    """An enricher that ran by default would be a bill and a third-party
    dependency nobody chose."""
    assert settings.enrichment_enabled is False
    assert isinstance(make_client(settings), DisabledClient)

    subject = Enricher(settings, client=ScriptedClient([{"summary": "x"}]))
    result = subject.enrich(RECORD)

    assert result.drafted == {}
    assert result.skipped_reason == "enrichment is disabled"


def test_an_unavailable_model_is_not_an_error(enabled) -> None:
    """A record that is not enriched is a record with fewer fields, which is a
    completeness level rather than a failure."""

    class Broken:
        def complete(self, prompt, *, schema, system=""):
            raise EnrichmentUnavailable("upstream 503")

    subject = Enricher(enabled, client=Broken())
    result = subject.enrich(RECORD)

    assert result.skipped_reason == "upstream 503"
    assert subject.apply(RECORD, result) == RECORD


def test_nothing_is_asked_when_nothing_is_missing(enabled) -> None:
    full = {**RECORD, **{term: ["x"] for term in ENRICHABLE_FIELDS}}
    subject, client = enricher(enabled, [{"summary": "x"}])

    assert subject.enrich(full).skipped_reason == "nothing enrichable is missing"
    assert client.prompts == []


# ---- the prompt ----------------------------------------------------------


def test_the_schema_is_restricted_to_the_fields_being_asked_for(enabled) -> None:
    """The schema constrains a cooperative model and the allow-list constrains
    every other kind. Both, because neither alone is enough."""
    subject, client = enricher(enabled, [{"summary": "x"}])
    subject.enrich(RECORD)

    properties = set(client.schemas[0]["properties"])
    assert "license" not in properties
    assert "accessURL" not in properties
    assert "summary" in properties


def test_the_system_prompt_states_the_rule_the_filter_enforces(enabled) -> None:
    """The prompt is a courtesy and the filter is the control — but a prompt
    that contradicted the filter would waste tokens producing output that is
    then thrown away."""
    subject, client = enricher(enabled, [{"summary": "x"}])
    subject.enrich(RECORD)

    system = client.systems[0].lower()
    assert "licence" in system or "license" in system
    assert "omit" in system
