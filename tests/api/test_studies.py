"""``/v1/studies`` — the registry's other three kinds, served (#82).

The property under test throughout is *reachability of the thing that matters*.
A study's assumption set is the most useful thing the catalog knows about a
filing — that a number is an estimate, that it came from a named exhibit, that
it lands on a named field of the model — and it was writable, validated and
reachable by nobody.
"""

from __future__ import annotations

CASCADE = "cascade-pl-irp-2026"
COALITION = "coalition-intervention-ue-26-0142"
ROE = "Investments/Financials/TechnologyFinancialData#return_on_equity"


def test_a_study_arrives_with_its_assumptions_already_resolved(registry_client) -> None:
    """Resolved, not as IRIs to go and fetch.

    A caller handed `hasAssumptionSet: [...]` has to know that an assumption
    set is a record, which URL serves it, and that the assumptions inside it
    are nested — three facts about this catalog's internals standing between
    them and a table of numbers.
    """
    body = registry_client.get(f"/v1/studies/{CASCADE}").json()

    assert body["study_kind"] == "filing"
    assert body["docket"] == "UE-26-0142"
    assert len(body["assumption_sets"]) == 1

    rows = body["assumption_sets"][0]["assumptions"]
    assert len(rows) == 3
    roe = next(r for r in rows if r["path"] == ROE)
    assert roe["value"] == "0.098"
    # The load-bearing one. 0.098 asserted and 0.098 measured are different
    # claims, and a filing that does not distinguish them cannot be argued with.
    assert roe["value_basis"] == "estimated"
    assert roe["field_sources"] == ["https://catalog.opengrid.org/ds/cascade-rate-case-exhibits"]


def test_a_cited_source_the_catalog_does_not_hold_is_named_but_not_linkable(
    registry_client,
) -> None:
    """The honest state, and it has to survive the API.

    A rate-case exhibit is a real document this catalog has no record of. The
    citation is worth keeping — it is how the number is traceable at all — and
    rendering it as a link would promise a page that 404s. So the IRI is served
    and the slug is not, and the UI can tell the two apart without guessing.
    """
    body = registry_client.get(f"/v1/studies/{CASCADE}").json()
    rows = body["assumption_sets"][0]["assumptions"]

    assert all(r["field_sources"] for r in rows), "every value says where it came from"
    assert all(r["field_source_ids"] == [] for r in rows), "and none of them is catalogued"

    # The network is a different matter: it is a published record, so it links.
    assert body["bound_reference_model_ids"] == ["gb-osm-reference"]


def test_a_unit_arrives_with_a_label_rather_than_a_qudt_iri(registry_client) -> None:
    body = registry_client.get(f"/v1/studies/{CASCADE}").json()
    cap = next(
        r
        for r in body["assumption_sets"][0]["assumptions"]
        if r["path"] == "Investments/Requirements/CarbonCaps#max_mtons"
    )
    assert cap["unit"] == "http://qudt.org/vocab/unit/MegaTON_Metric"
    assert cap["unit_label"], "a reader should not have to resolve a QUDT IRI by eye"


def test_an_inherited_value_says_so_and_a_changed_one_carries_its_defence(
    registry_client,
) -> None:
    """What makes a single-factor intervention checkable.

    Three values, one changed. Without `inherited_from` a reader has to diff
    two tables by eye to find which; without `justification` on the changed one
    they find the difference and not the argument for it.
    """
    body = registry_client.get(f"/v1/studies/{COALITION}").json()
    rows = body["assumption_sets"][0]["assumptions"]

    changed = [r for r in rows if not r["inherited_from"]]
    assert [r["path"] for r in changed] == [ROE]
    assert changed[0]["value"] == "0.074"
    assert "Order 08-441" in (changed[0]["justification"] or "")
    assert all(r["justification"] is None for r in rows if r["inherited_from"])


def test_an_intervention_names_the_filing_it_contests(registry_client) -> None:
    body = registry_client.get(f"/v1/studies/{COALITION}").json()
    assert body["study_kind"] == "intervention"
    assert body["parent_study_id"] == CASCADE
    assert body["docket"] == "UE-26-0142", "same docket is what makes it one thread"


def test_a_run_record_names_who_ran_it_and_on_what(registry_client) -> None:
    """The Hub registers runs; it does not execute them, so `executed_by` is
    never the Hub."""
    body = registry_client.get(f"/v1/studies/{CASCADE}").json()
    assert len(body["run_records"]) == 1
    run = body["run_records"][0]
    assert run["tool"] == "PyPSA"
    assert run["solver"] == "HiGHS 1.7.2"
    assert "Cascade Power & Light" in run["executed_by"]
    assert run["case_hash"], "two runs of the same study on an edited case are not one run"
    assert run["on_reference_model_id"] == "gb-osm-reference"


def test_a_dataset_is_not_a_study(registry_client) -> None:
    """Not 200-with-empty-lists.

    A dataset served here as a study with no assumptions reads as a study that
    assumed nothing, which is a claim about the record rather than about the
    route.
    """
    assert registry_client.get("/v1/studies/ecmwf-era5").status_code == 404
    assert registry_client.get("/v1/studies/not-a-record-at-all").status_code == 404


# ---------------------------------------------------------------------------
# The other direction
# ---------------------------------------------------------------------------


def test_a_reference_model_knows_which_studies_stand_on_it(registry_client) -> None:
    body = registry_client.get("/v1/datasets/gb-osm-reference/studies").json()

    assert body["total"] == 2
    ids = sorted(s["id"] for s in body["studies"])
    assert ids == [CASCADE, COALITION]
    assert all("reference-model" in s["roles"] for s in body["studies"])


def test_a_record_with_no_registered_study_says_zero_rather_than_failing(
    registry_client,
) -> None:
    """Zero is an answer. A 404 here would read as "this dataset does not
    exist", which is a different and false statement."""
    body = registry_client.get("/v1/datasets/ecmwf-era5/studies").json()
    assert body == {"dataset_id": "ecmwf-era5", "total": 0, "studies": []}


def test_the_route_is_absent_for_a_record_that_is(registry_client) -> None:
    assert registry_client.get("/v1/datasets/nope/studies").status_code == 404


def test_a_run_registered_after_the_filing_was_frozen_is_still_found(
    loaded, registry_client
) -> None:
    """Both directions, and the second one is not redundant.

    A study carries `og:frozenAt` — its inputs stopped moving — so a run
    registered afterwards cannot be added by editing the study without editing
    a record that is supposed to be fixed. The run names the assumption set it
    ran, which is the arrow that still points the right way. Here the study's
    own list is emptied and the run has to be found anyway.
    """
    from fixtures.loader import load_record

    document = load_record("coalition-intervention-ue-26-0142")
    del document["@graph"][0]["hasRunRecord"]
    loaded.put(document)

    body = registry_client.get(f"/v1/studies/{COALITION}").json()
    assert [run["id"] for run in body["run_records"]] == ["R-0537"]


def test_the_two_runs_are_one_assumption_apart(registry_client) -> None:
    """The point of the whole arrangement, asserted as a number.

    A single-factor intervention is cheap to check and hard to argue with
    *because* the two objectives are comparable: same network, same tool, same
    solver, one value changed. If either study lost its run record the
    comparison would silently become impossible, and nothing else in the suite
    would notice.
    """
    filing = registry_client.get(f"/v1/studies/{CASCADE}").json()["run_records"][0]
    fork = registry_client.get(f"/v1/studies/{COALITION}").json()["run_records"][0]

    assert filing["on_reference_model_id"] == fork["on_reference_model_id"]
    assert (filing["tool"], filing["solver"]) == (fork["tool"], fork["solver"])
    assert filing["objective_value"] > fork["objective_value"]
    assert filing["case_hash"] != fork["case_hash"]


def test_a_study_is_indexed_by_what_it_answers_and_how_to_cite_it(registry_client) -> None:
    """Both are on the *document*, not only on the record.

    A reader who found the study through search should be able to see what kind
    of analysis it is and quote it without opening anything, and a facet on
    analysis type should be able to separate an IRP from a power-flow study.
    Read out of the graph per request, neither is possible: an aggregation
    cannot walk the store.
    """
    page = registry_client.get("/v1/datasets?record_type=study&facets=analysis_type").json()
    assert page["total"] == 2

    row = next(r for r in page["results"] if r["id"] == CASCADE)
    assert row["citation_id"] == "opengrid:study/cascade-pl-irp-2026@1.0"
    irp = next(t for t in row["analysis_types"] if "integratedResourcePlan" in t["iri"])
    # The label, joined from the vocabulary graph. The projector's CONSTRUCT
    # pulled labels for the three predicates that say what a *dataset* feeds
    # and not for the one that says what a study *is*, so the published site
    # tagged a filing `integratedResourcePlan` — the IRI tail, which is the
    # fallback when no label joined.
    assert irp["label"] == "Integrated Resource Plan"

    buckets = {b["value"]: b["count"] for b in page["facets"]["analysis_type"]}
    assert buckets["https://schema.opengrid.org/concept/analysis-type/capacityExpansion"] == 2, (
        "both studies do capacity expansion; only the filing is an IRP"
    )

    # And the filter the facet implies actually filters.
    only_irp = registry_client.get(
        "/v1/datasets?analysis_type="
        "https://schema.opengrid.org/concept/analysis-type/integratedResourcePlan"
    ).json()
    assert [r["id"] for r in only_irp["results"]] == [CASCADE]


def test_a_dataset_carries_no_study_axes(registry_client) -> None:
    """The property that makes them safe to facet on: absent means "not a
    study", never "a study that did not say"."""
    row = registry_client.get("/v1/datasets/ecmwf-era5").json()
    for field in ("study_kind", "docket", "jurisdiction", "parent_study", "frozen_at"):
        assert row[field] is None, field
    assert row["analysis_types"] == []
    # And the reverse: `supported_analysis` is a dataset's fitness to feed an
    # analysis, which is not the same field and must not have been reused.
    assert row["supported_analysis"], "ERA5 supports analyses; that axis is unaffected"
