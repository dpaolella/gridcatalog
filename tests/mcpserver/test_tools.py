"""The seven MCP tools (WP-10.2).

The milestone's done-criterion is here: *an agent can search, inspect, explain a
connection and receive an access plan without ever receiving bulk data, and an
out-of-tier tool returns 403 rather than being absent.*
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.mcp import TOOL_TIERS, ApiError, NotEntitledForTool, Tools

ERA5 = "ecmwf-era5"
GWA = "global-wind-atlas"
CUTOUTS = "pypsa-eur-weather-cutouts"
RESTRICTED = "utility-load-shapes-allowlisted"


# ---- the done-criterion --------------------------------------------------


def test_an_agent_can_go_from_search_to_access_plan(tools) -> None:
    """Search, inspect, explain, plan — and no bytes at any point."""
    found = tools.search_datasets(q="wind")
    assert found.data["results"]

    dataset_id = found.data["results"][0]["id"]
    record = tools.get_dataset(dataset_id)
    schema = tools.get_dataset_schema(dataset_id)
    plan = tools.get_access_plan(dataset_id)

    assert record.data["id"] == dataset_id
    assert "fields" in schema.data
    assert plan.data["location"], "the plan says where the data is"
    assert plan.data["mode"] in ("redirect", "partial-read", "subsetting-protocol")


def test_an_out_of_tier_tool_returns_403_rather_than_being_absent(tools) -> None:
    """PRD §F9. Hiding a tool makes the agent hallucinate around the gap: it
    invents a way to do the thing rather than reporting that it cannot."""
    assert "author_workflow" in TOOL_TIERS, "the tool exists for every caller"

    with pytest.raises(NotEntitledForTool) as raised:
        tools.author_workflow(goal="anything", dataset_ids=[ERA5])

    assert "tier 1" in str(raised.value)


def test_the_tier_1_tool_works_for_a_tier_1_caller(tier1_tools) -> None:
    result = tier1_tools.author_workflow(goal="Site a wind farm", dataset_ids=[ERA5, GWA])

    assert result.data["inert"] is True
    assert {d["dataset_id"] for d in result.data["datasets"]} == {ERA5, GWA}


def test_no_tool_returns_bulk_data(tools) -> None:
    """The Hub is a control plane. `preview_dataset` is the tool most likely to
    be misread as "give me rows", so it says in the payload that it is not."""
    preview = tools.preview_dataset(ERA5)

    assert "does not proxy data hosted elsewhere" in preview.data["note"]
    assert "rows" not in preview.data


# ---- grounding: the single most important correctness property -----------


def test_no_tool_invents_a_dataset(tools, catalog) -> None:
    """PRD §F9: *the server never fabricates a dataset or a field. A plausible
    fabricated dataset is worse than no answer.*

    Every id in every response is checked against the catalog itself, not
    against another API call — the point is to catch a tool that composed an id
    rather than copying one.
    """
    from datahub.graph.records import slug_of

    real = {slug_of(str(iri)) for iri in catalog.list_ids()}
    real |= set(catalog.list_ids())

    results = [
        tools.search_datasets(q="solar"),
        tools.get_dataset(ERA5),
        tools.get_dataset_schema(ERA5),
        tools.explain_connection(GWA, CUTOUTS),
        tools.preview_dataset(ERA5),
        tools.get_access_plan(ERA5),
    ]
    for result in results:
        for dataset_id in result.dataset_ids:
            assert dataset_id in real, f"{dataset_id!r} is not in the catalog"


def test_an_unlinked_pair_gets_an_honest_answer_not_an_invented_one(tools) -> None:
    """A plausible sentence about two datasets that are not linked is exactly
    the failure this module is arranged to prevent."""
    result = tools.explain_connection(ERA5, "eia-natural-gas-prices")

    if not result.data.get("connected"):
        assert "records no connection" in result.data["explanation"]
        assert "not a claim that they are unrelated" in result.data["explanation"]


def test_a_missing_dataset_is_an_error_not_a_guess(tools) -> None:
    with pytest.raises(ApiError) as raised:
        tools.get_dataset("this-dataset-does-not-exist")

    assert raised.value.status == 404


def test_a_field_with_no_concept_carries_its_reason(tools) -> None:
    """An agent told "this column is unmapped because no concept covers a
    compiler's confidence class" will not invent one."""
    schema = tools.get_dataset_schema("global-transmission-database")

    gaps = [f for f in schema.data["fields"] if f.get("concept_gap_reason")]
    assert gaps
    assert all(len(f["concept_gap_reason"]) > 30 for f in gaps)


# ---- the payload cap -----------------------------------------------------


def test_every_read_tool_is_capped(client) -> None:
    """PRD §F9: *payload cap on every read tool so bulk data can never enter
    the agent's context. Default 100 KB per response.*"""
    from datahub.mcp.client import DEFAULT_PAYLOAD_CAP

    assert DEFAULT_PAYLOAD_CAP == 100 * 1024

    tiny = Tools(client=client, payload_cap=400)
    result = tiny.search_datasets(limit=50)

    assert result.truncated
    assert len(json.dumps(result.data).encode()) <= 400


def test_truncation_is_reported_not_silent(client) -> None:
    """An agent handed a truncated list that looked complete answers "there are
    12 such datasets" when there are 400, and is confident about it."""
    tiny = Tools(client=client, payload_cap=400)

    result = tiny.search_datasets(limit=50)

    assert "INCOMPLETE" in result.truncation_note
    assert result.as_dict()["truncated"] is True


def test_truncation_trims_the_long_list_not_the_envelope(client) -> None:
    """A search response is a small envelope around a long list. Trimming the
    envelope removes the total count and leaves what was actually too big."""
    tiny = Tools(client=client, payload_cap=600)

    result = tiny.search_datasets(limit=50)

    assert "total" in result.data, "the count survived"
    assert result.data["returned"] == len(result.data["results"])


def test_a_response_under_the_cap_is_untouched(tools) -> None:
    result = tools.get_dataset(ERA5)

    assert not result.truncated
    assert result.as_dict() == {"data": result.data}


# ---- entitlement ---------------------------------------------------------


def test_an_anonymous_agent_cannot_see_a_restricted_record(tools) -> None:
    """Identity propagates and there is no privilege escalation path: the MCP
    server has no credentials of its own to fall back to."""
    with pytest.raises(ApiError) as raised:
        tools.get_dataset(RESTRICTED)

    assert raised.value.status == 404, "the same 404 an absent record gets"


def test_a_restricted_record_is_absent_from_search(tools) -> None:
    result = tools.search_datasets(limit=50)

    assert RESTRICTED not in {r["id"] for r in result.data["results"]}


def test_the_tier_comes_from_the_api_not_from_the_agent(client) -> None:
    """An agent that could state its own tier would state the one it wanted."""
    from datahub.mcp.tools import resolve_tier

    assert resolve_tier(client) == 0, "anonymous"


# ---- the workflow tool ---------------------------------------------------


def test_a_workflow_cannot_reference_a_dataset_that_does_not_exist(tier1_tools) -> None:
    """The fabrication failure wearing a different hat — and it would be handed
    to a user as a plan."""
    with pytest.raises(ApiError):
        tier1_tools.author_workflow(goal="x", dataset_ids=["not-a-real-dataset"])


def test_the_workflow_is_inert(tier1_tools) -> None:
    """Execution tiers 2 and 3 are out of scope. Nothing here runs, and the
    payload says so rather than leaving it to be inferred."""
    result = tier1_tools.author_workflow(goal="x", dataset_ids=[ERA5], steps=["fetch", "plot"])

    assert result.data["inert"] is True
    assert "OpenGrid will not run it" in result.data["note"]
    body = json.dumps(result.data)
    assert "http" not in body.replace("https://catalog.opengrid.org", ""), "no endpoint to call"


# ---- the tool surface ----------------------------------------------------


def test_all_seven_prd_tools_exist(tools) -> None:
    """PRD §F9's table, asserted so a tool cannot go missing quietly."""
    expected = {
        "search_datasets",
        "get_dataset",
        "get_dataset_schema",
        "explain_connection",
        "preview_dataset",
        "get_access_plan",
        "author_workflow",
    }
    assert set(TOOL_TIERS) == expected
    for name in expected:
        assert callable(getattr(tools, name))


def test_the_server_registers_every_tool_for_every_caller(tools) -> None:
    """Registration is unconditional; the tier check happens per call."""
    try:
        from datahub.mcp.server import create_server
    except ImportError as exc:  # pragma: no cover - depends on the install
        pytest.skip(f"fastmcp server support not installed: {exc}")

    server = create_server(tools=tools)
    names = {t.name for t in _tools_of(server)}

    assert set(TOOL_TIERS) <= names


def test_a_403_reaches_the_agent_as_a_payload_not_an_exception(tools) -> None:
    """The protocol shell's one job. An exception crossing the MCP boundary
    becomes a transport error, which an agent reads as "the tool is broken"
    rather than "I am not allowed" — and it will try something else."""
    try:
        from datahub.mcp.server import create_server
    except ImportError as exc:  # pragma: no cover - depends on the install
        pytest.skip(f"fastmcp server support not installed: {exc}")

    server = create_server(tools=tools)
    result = _call(server, "author_workflow", {"goal": "x", "dataset_ids": [ERA5]})

    assert result["error"] == "forbidden"
    assert result["status"] == 403
    assert "tier 1" in result["detail"]


def test_a_tool_call_through_the_server_returns_real_catalog_data(tools) -> None:
    """End to end through the protocol shell, not just the tool object."""
    try:
        from datahub.mcp.server import create_server
    except ImportError as exc:  # pragma: no cover - depends on the install
        pytest.skip(f"fastmcp server support not installed: {exc}")

    server = create_server(tools=tools)
    result = _call(server, "get_dataset", {"dataset_id": ERA5})

    assert result["data"]["id"] == ERA5


#: Every tool, with arguments that exercise the ones the shell has to forward
#: by name. Kept as data so adding a tool without adding a row here is a
#: visible omission rather than a silent gap.
SERVER_CALLS: list[tuple[str, dict]] = [
    ("search_datasets", {"q": "wind", "license_id": "CC-BY-4.0", "limit": 3}),
    ("get_dataset", {"dataset_id": ERA5}),
    ("get_dataset_schema", {"dataset_id": ERA5}),
    ("preview_dataset", {"dataset_id": ERA5, "rows": 3}),
    ("get_access_plan", {"dataset_id": ERA5}),
    ("explain_connection", {"dataset_id": ERA5, "other_dataset_id": ERA5}),
    ("author_workflow", {"goal": "x", "dataset_ids": [ERA5], "steps": ["a"]}),
]


@pytest.mark.parametrize(("name", "arguments"), SERVER_CALLS, ids=[c[0] for c in SERVER_CALLS])
def test_every_tool_is_wired_to_the_signature_it_calls(tools, name, arguments) -> None:
    """The protocol shell forwards arguments **by keyword**, so a renamed
    parameter is a TypeError at call time and nothing catches it earlier.

    `search_datasets` passed `license=` to a method taking `license_id=` and had
    therefore never worked. The end-to-end test above did not catch it because
    it exercised one tool of seven, and the tool tests did not because they call
    the `Tools` object directly and never cross the shell.

    A 403 is a pass here: a tier-gated tool answering "you need tier 1" has
    been wired correctly and refused correctly, which is what PRD §F9 asks for.
    """
    try:
        from datahub.mcp.server import create_server
    except ImportError as exc:  # pragma: no cover - depends on the install
        pytest.skip(f"fastmcp server support not installed: {exc}")

    result = _call(create_server(tools=tools), name, arguments)

    assert "error" not in result or result.get("status") == 403, result


def test_the_instructions_state_the_control_plane_rule() -> None:
    from datahub.mcp.server import INSTRUCTIONS

    assert "Data hosted elsewhere is not returned here" in INSTRUCTIONS
    assert "not captured" in INSTRUCTIONS
    assert "explain_connection" in INSTRUCTIONS


def _call(server, name: str, arguments: dict) -> dict:
    """Invoke a tool the way a client would, and unwrap the structured result."""
    import anyio

    async def _run():
        return await server.call_tool(name, arguments)

    result = anyio.run(_run)
    payload = getattr(result, "structured_content", None) or getattr(result, "data", None)
    if payload is None and getattr(result, "content", None):
        import json as _json

        payload = _json.loads(result.content[0].text)
    return payload


def _tools_of(server) -> list:
    import anyio

    async def _list() -> list:
        return list(await server.list_tools())

    return anyio.run(_list)


# ---- schema paging -------------------------------------------------------


def test_a_schema_can_be_paged_through(tools) -> None:
    """Reading a dataset's own schema surface made large schemas normal.

    ERA5 has 273 fields and does not fit under the 100 KB payload cap.
    Truncation was already reported rather than silent, which is honest and
    not on its own useful: an agent told the answer is incomplete and given no
    way to ask for the rest has been informed of a dead end.
    """
    first = tools.get_dataset_schema(ERA5, limit=2, offset=0).data
    second = tools.get_dataset_schema(ERA5, limit=2, offset=2).data

    assert first["returned"] == 2
    assert first["offset"] == 0
    assert second["offset"] == 2
    assert first["total"] == second["total"]
    names = {f["local_name"] for f in first["fields"]}
    assert not names & {f["local_name"] for f in second["fields"]}, "pages do not overlap"


def test_a_page_is_a_page_of_a_stable_order(tools) -> None:
    """Sliced after sorting, so page two is the second page of one order
    rather than of whatever order the graph happened to return."""
    whole = [f["local_name"] for f in tools.get_dataset_schema(ERA5, limit=10).data["fields"]]
    halves = [f["local_name"] for f in tools.get_dataset_schema(ERA5, limit=5).data["fields"]] + [
        f["local_name"] for f in tools.get_dataset_schema(ERA5, limit=5, offset=5).data["fields"]
    ]
    assert whole == halves


def test_an_incomplete_answer_says_how_to_ask_for_the_rest(tools) -> None:
    """`next_offset` in the payload, because an agent should not have to work
    out that this tool takes an offset."""
    result = tools.get_dataset_schema(ERA5, limit=2).data

    assert result["next_offset"] == 2
    assert "not in this response" in result["more"]
    assert "offset=2" in result["more"]


def test_the_last_page_does_not_offer_a_next_one(tools) -> None:
    total = tools.get_dataset_schema(ERA5, limit=1).data["total"]
    last = tools.get_dataset_schema(ERA5, limit=50, offset=max(0, total - 1)).data

    assert last["returned"] >= 1
    assert "next_offset" not in last
    assert "more" not in last


def test_an_unpaged_call_still_works_and_is_what_it_always_was(tools) -> None:
    """`limit` is absent by default, so a caller written before paging existed
    gets exactly what it got before — plus the count it needs to notice that
    more exist."""
    result = tools.get_dataset_schema(ERA5).data

    assert result["fields"]
    assert result["total"] >= result["returned"]
