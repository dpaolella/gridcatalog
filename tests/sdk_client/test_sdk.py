"""The Python SDK (WP-10.1).

PRD §F9's target is *from zero to first dataset pull in one line*, so the first
test is that line. The rest guard the properties an SDK erodes quietly: a
second copy of a rule the API owns, a silently-ignored filter, and a client
that starts moving bytes through the control plane.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from opengrid import AccessPlanUnusable, DataHubError, Dataset, NotFound

ERA5 = "ecmwf-era5"
GWA = "global-wind-atlas"
CUTOUTS = "pypsa-eur-weather-cutouts"
RESTRICTED = "utility-load-shapes-allowlisted"


# ---- the one-liner -------------------------------------------------------


def test_zero_to_a_dataset_in_one_line(hub) -> None:
    """PRD §F9's own example, minus the network."""
    ds = hub.search(domain="https://schema.opengrid.org/concept/data-domain/DD5")[0]

    assert isinstance(ds, Dataset)
    assert ds.title


def test_search_returns_objects_not_dicts(hub) -> None:
    """A dict lets a caller write ``record["quality"]["overall"]`` and find out
    in production. A typed object makes the absence of a composite visible
    where the code is written."""
    results = hub.search(q="solar")

    assert results
    assert all(isinstance(d, Dataset) for d in results)
    assert not hasattr(results[0].quality, "overall")


def test_a_result_set_knows_the_total(hub) -> None:
    """A caller who saw twenty results and no total would conclude there were
    twenty."""
    results = hub.search(limit=2)

    assert len(results) <= 2
    assert results.total >= len(results)
    if results.total > 2:
        assert results.has_more


# ---- reading a record ----------------------------------------------------


def test_get_returns_a_navigable_dataset(hub) -> None:
    ds = hub.get(ERA5)

    assert ds.id == ERA5
    assert ds.completeness_level == 3
    assert ds.fields()
    assert ds.distributions()


def test_a_field_gap_carries_its_reason(hub) -> None:
    """Rule X4: never a silent omission. A user who sees a blank concept column
    cannot tell an unmapped field from an unexamined one."""
    fields = hub.get("global-transmission-database").fields()

    gaps = [f for f in fields if f.concept_gap_reason]
    assert gaps
    assert all(len(f.concept_gap_reason) > 30 for f in gaps)


def test_a_resolved_field_carries_the_concept_definition(hub) -> None:
    """PRD §F4.2. A field documented only through CIM or CGMES is unreadable to
    someone who does not own the standard."""
    fields = hub.get(ERA5).fields()

    resolved = [f for f in fields if f.concept]
    assert resolved
    assert any(f.concept.definition for f in resolved)


def test_not_assessed_is_not_a_poor_grade(hub) -> None:
    """PRD §F5. A record below level 2 carries no field metadata to grade, and
    conflating that with grade D would defame every harvested record."""
    ds = hub.get("lbnl-queued-up")

    assert ds.completeness_level < 2
    assert ds.quality.provenance is None
    assert ds.quality.documentation is None


# ---- links ---------------------------------------------------------------


def test_a_correlated_pair_is_visible_before_you_combine_them(hub) -> None:
    links = hub.get(GWA).links()

    correlated = [link for link in links if not link.independent]
    assert correlated
    assert "ERA5" in correlated[0].correlation_warning
    assert correlated[0].shared_origin


def test_independent_is_a_property_not_a_string_check(hub) -> None:
    """``if link.correlation_warning:`` is easy to write as ``if not …`` and
    mean the opposite."""
    links = hub.get(GWA).links()

    for link in links:
        assert link.independent == (link.correlation_warning is None)


# ---- access --------------------------------------------------------------


def test_an_access_plan_carries_the_licence_it_binds_you_to(hub) -> None:
    """In the plan rather than in a page nobody read. A script handed a URL
    cannot know it may not redistribute what it downloads."""
    plan = hub.get(ERA5).access_plan()

    assert plan.location
    assert plan.license or plan.license_note
    assert plan.mode in ("redirect", "partial-read", "subsetting-protocol")


def test_a_time_slice_reaches_the_plan(hub) -> None:
    """``slice`` because that is what a user writing
    ``ds.open(time=slice(...))`` already has in their hand."""
    plan = hub.get(ERA5).access_plan(time=slice("2019-01-01", "2019-12-31"))

    assert plan.requested_slice.get("time")


def test_a_bad_time_argument_says_what_to_write_instead(hub) -> None:
    with pytest.raises(DataHubError, match="slice"):
        hub.get(ERA5).access_plan(time="2019")


def test_the_sdk_never_downloads_through_the_hub(hub) -> None:
    """A control-plane client. A method that returned bytes would make every
    script holding a dataset it did not ask for."""
    url = hub.download_url("global-transmission-database")

    assert url.startswith("http")
    assert "testserver" not in url, "the redirect points at the source, not at us"


def test_a_missing_reader_names_the_package_to_install(hub) -> None:
    """ "Unusable" with no remedy is a dead end, and the remedy is one pip
    install."""
    pytest.importorskip("opengrid.readers")
    from opengrid.readers import execute

    plan = hub.get(ERA5).access_plan()
    try:
        execute(plan)
    except AccessPlanUnusable as exc:
        assert "pip install" in str(exc) or "no reader" in str(exc)
    else:  # pragma: no cover - only when xarray is installed
        pass


def test_geoparquet_does_not_resolve_to_the_parquet_reader() -> None:
    """The wrong reader loses the geometry column, silently."""
    from opengrid.models import AccessPlan
    from opengrid.readers import _reader_for

    geo = AccessPlan(
        dataset_id="x",
        distribution_id="y",
        mode="redirect",
        location="s3://b/f.parquet",
        format="geoparquet",
    )
    plain = AccessPlan(
        dataset_id="x",
        distribution_id="y",
        mode="redirect",
        location="s3://b/f.parquet",
        format="parquet",
    )

    assert _reader_for(geo) == "_read_geoparquet"
    assert _reader_for(plain) == "_read_parquet"


def test_an_unknown_format_refuses_rather_than_guessing() -> None:
    """A reader that fell back to "try pandas" would hand a user a DataFrame of
    gibberish for a Zarr store, which is worse than an error — they would plot
    it."""
    from opengrid.models import AccessPlan
    from opengrid.readers import execute

    plan = AccessPlan(
        dataset_id="x",
        distribution_id="y",
        mode="redirect",
        location="https://example.org/data.xyz",
        format="xyz",
    )

    with pytest.raises(AccessPlanUnusable, match="no reader"):
        execute(plan)


# ---- entitlement ---------------------------------------------------------


def test_a_hidden_record_raises_the_same_error_as_a_missing_one(hub) -> None:
    """An SDK that distinguished them would reconstruct the existence oracle
    the API spent M6 removing."""
    with pytest.raises(NotFound) as hidden:
        hub.get(RESTRICTED)
    with pytest.raises(NotFound) as absent:
        hub.get("no-such-dataset-at-all")

    assert type(hidden.value) is type(absent.value)
    assert hidden.value.status == absent.value.status == 404


def test_anonymous_search_works(hub) -> None:
    """PRD §F10: do not gate browsing."""
    assert hub.search(limit=5).total > 0
    assert hub.whoami()["authenticated"] is False


def test_the_sdk_holds_no_second_copy_of_the_entitlement_rules() -> None:
    """A second copy would eventually disagree with the first, and the one that
    disagreed would be the one a user was standing behind."""
    import opengrid.client as client_module

    source = Path(client_module.__file__).read_text()
    for forbidden in ("entitled_principals", "visibility ==", "custodian_of", "is_steward"):
        assert forbidden not in source


def test_the_sdk_talks_to_the_rest_api_and_nothing_else() -> None:
    """The architecture boundary, asserted. No SPARQL, no store client."""
    package = Path(__file__).resolve().parents[2] / "sdk" / "python" / "opengrid"

    for module in package.glob("*.py"):
        source = module.read_text()
        # Query text, not the word: the module docstrings say "no SPARQL",
        # which is the opposite of a violation.
        assert "SELECT ?" not in source
        assert "PREFIX " not in source
        assert "rdflib" not in source
        assert "from datahub" not in source
        assert "import datahub" not in source


# ---- ergonomics ----------------------------------------------------------


def test_domain_and_region_are_the_words_a_modeller_uses(hub) -> None:
    """PRD §F9's example writes `domain=` and `region=`. Making a user write
    `data_domain=` is a small tax collected on every line."""
    from opengrid.client import _ALIASES

    assert _ALIASES["domain"] == "data_domain"
    assert hub.search(domain="https://schema.opengrid.org/concept/data-domain/DD1").total >= 1


def test_an_unknown_filter_is_an_error_not_a_shrug(hub) -> None:
    """A typo that quietly widens a search returns results the caller trusts."""
    with pytest.raises(DataHubError):
        hub.search(nonsense_filter="x")


def test_closing_the_hub_does_not_close_a_client_it_was_given(http) -> None:
    """Closing a caller's client breaks the next thing that uses it, from a
    place with no obvious connection to the close."""
    from opengrid import DataHub

    with DataHub(base_url="http://testserver", client=http):
        pass

    assert http.get("/v1/health").status_code == 200
