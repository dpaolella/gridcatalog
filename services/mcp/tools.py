"""The seven MCP tools (WP-10.2), independent of the MCP wire protocol.

Split from :mod:`datahub.mcp.server` so the rules PRD §F9 cares about — the
payload cap, tier gating, grounding — are testable without standing up a
protocol server, and so the SDK can reuse them.

Three rules enforced here, all of them server-side because **the agent is an
untrusted client fully outside OpenGrid's control** and no guardrail may depend
on its cooperation:

1. **Nothing is fabricated.** Every field of every response is copied from an
   API payload. There is no code path that composes a dataset, and
   ``tests/mcp/test_grounding.py`` asserts that every id in every response
   exists in the catalog.
2. **Payload cap on every read tool.** Default 100 KB. Truncation is *reported*
   in the response rather than silent: an agent that received a truncated list
   and believed it complete would answer "there are 12 such datasets" when
   there are 400.
3. **Tier-gated tools are present for every caller and return 403 per call.**
   Hiding a tool makes the agent hallucinate around the gap — it invents a way
   to do the thing rather than reporting that it cannot.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from datahub.mcp.client import DEFAULT_PAYLOAD_CAP, ApiClient, ApiError

#: Tool name → the tier a caller needs. Tier 0 is everyone, including
#: anonymous: PRD §F10 says *do not gate browsing*, and an agent reading public
#: metadata is browsing.
TOOL_TIERS: dict[str, int] = {
    "search_datasets": 0,
    "get_dataset": 0,
    "get_dataset_schema": 0,
    "explain_connection": 0,
    "preview_dataset": 0,
    "get_access_plan": 0,
    "author_workflow": 1,
}

#: Hard caps on the preview tool, on top of the byte cap. Rows because a
#: hundred-column table hits neither the row cap nor the byte cap at ten rows,
#: and bytes because ten rows of a wide table can be a megabyte.
PREVIEW_MAX_ROWS = 20


class NotEntitledForTool(RuntimeError):
    """Raised where PRD §F9 wants a 403 rather than an absent tool."""

    status = 403


@dataclass
class ToolResult:
    """What a tool returns, and what happened to it on the way out."""

    data: Any
    truncated: bool = False
    truncation_note: str | None = None
    #: Every id the response mentions. The grounding test walks this rather
    #: than re-parsing the payload, so a tool that added a new id-bearing field
    #: cannot quietly escape the check.
    dataset_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"data": self.data}
        if self.truncated:
            payload["truncated"] = True
            payload["truncation_note"] = self.truncation_note
        return payload


@dataclass
class Tools:
    """The seven tools, over one API client."""

    client: ApiClient
    payload_cap: int = DEFAULT_PAYLOAD_CAP
    #: The caller's tier. Set from the API's own view of the caller rather than
    #: from anything the agent said: an agent that could declare its tier would
    #: declare the one it wanted.
    tier: int = 0
    _tier_resolved: bool = field(default=False, repr=False)

    # -- tier 0 ------------------------------------------------------------

    def search_datasets(
        self,
        q: str | None = None,
        *,
        data_domain: list[str] | None = None,
        concept: list[str] | None = None,
        license_id: str | None = None,
        anonymous_access: bool | None = None,
        bbox: list[float] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ToolResult:
        """Catalog search, entitlement-scoped and payload-capped."""
        self._require("search_datasets")
        body = self.client.get(
            "/v1/datasets",
            q=q,
            data_domain=data_domain,
            concept=concept,
            license=license_id,
            anonymous_access=anonymous_access,
            bbox=",".join(str(v) for v in bbox) if bbox else None,
            limit=min(limit, 50),
            offset=offset,
        )
        results = body.get("results", [])
        return self._capped(
            {
                "total": body.get("total", 0),
                "returned": len(results),
                "results": results,
            },
            ids=[r.get("id") for r in results],
            what="search results",
        )

    def get_dataset(self, dataset_id: str) -> ToolResult:
        """The full record, as the catalog holds it."""
        self._require("get_dataset")
        body = self.client.get(f"/v1/datasets/{dataset_id}")
        return self._capped(body, ids=[body.get("id")], what="the record")

    def get_dataset_schema(self, dataset_id: str) -> ToolResult:
        """Field-level metadata: names, definitions, units, concepts, gaps.

        The gaps are the part an agent most needs. A field with no concept
        carries a stated reason, and an agent told "this column is unmapped
        because no concept covers a compiler's confidence class" will not
        invent one.
        """
        self._require("get_dataset_schema")
        body = self.client.get(f"/v1/datasets/{dataset_id}/schema")
        return self._capped(body, ids=[body.get("dataset_id")], what="the field list")

    def explain_connection(self, dataset_id: str, other_dataset_id: str) -> ToolResult:
        """Why two datasets are linked — including the correlation warning.

        The warning is the reason this tool exists rather than being a filter
        over `search_datasets`. An agent assembling a study from two datasets
        that share an upstream will otherwise report their agreement as
        corroboration, and no amount of prompt text fixes that as reliably as
        the fact arriving in the payload.
        """
        self._require("explain_connection")
        body = self.client.get(f"/v1/datasets/{dataset_id}/links")
        links = body.get("links", [])
        match = next((link for link in links if link.get("dataset_id") == other_dataset_id), None)
        if match is None:
            # Not an error, and emphatically not an invented explanation. "No
            # recorded connection" is an answer; a plausible sentence about two
            # datasets that are not linked is the failure mode this whole
            # module is arranged to prevent.
            return ToolResult(
                data={
                    "dataset_id": dataset_id,
                    "other_dataset_id": other_dataset_id,
                    "connected": False,
                    "explanation": (
                        "The catalog records no connection between these two datasets. That is a "
                        "statement about what has been catalogued, not a claim that they are "
                        "unrelated."
                    ),
                },
                dataset_ids=(dataset_id,),
            )
        return self._capped(
            {"dataset_id": dataset_id, "connected": True, **match},
            ids=[dataset_id, other_dataset_id],
            what="the explanation",
        )

    def preview_dataset(self, dataset_id: str, rows: int = 10) -> ToolResult:
        """A bounded preview. Hard row and byte caps.

        **This does not read the data.** The Hub is a control plane and never
        streams bytes (PRD §F8), so what a preview can honestly offer is the
        record's own description of its shape: the fields, their types, their
        units, and where to get the rest. An agent that wanted rows is told,
        in the payload, how to fetch them itself.
        """
        self._require("preview_dataset")
        rows = max(1, min(rows, PREVIEW_MAX_ROWS))
        schema = self.client.get(f"/v1/datasets/{dataset_id}/schema")
        # `/distributions` returns a bare list, not an envelope. Handling both
        # rather than assuming: the two shapes are one refactor apart and the
        # failure is an AttributeError deep in a tool an agent is mid-call on.
        distributions = self.client.get(f"/v1/datasets/{dataset_id}/distributions")
        if isinstance(distributions, dict):
            distributions = distributions.get("distributions", [])
        fields = schema.get("fields", [])[:rows]
        return self._capped(
            {
                "dataset_id": dataset_id,
                "note": (
                    "OpenGrid is a control plane and never returns data. This is the record's "
                    "description of the dataset's shape. Use get_access_plan to read the data "
                    "yourself; the plan says where it is and how to read it."
                ),
                "fields": fields,
                "completeness_level": schema.get("completeness_level"),
                "distributions": list(distributions)[:5],
            },
            ids=[dataset_id],
            what="the preview",
        )

    def get_access_plan(
        self,
        dataset_id: str,
        *,
        distribution_id: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
        bbox: list[float] | None = None,
        variables: list[str] | None = None,
    ) -> ToolResult:
        """An access plan, issued under the caller's identity.

        The plan is a document saying where the data is and how to read it. It
        carries the licence, the attribution and the quality grades, which is
        what makes agentic access defensible: an agent handed a URL cannot know
        it may not redistribute what it downloads; an agent handed a plan is
        told in a field it cannot miss.
        """
        self._require("get_access_plan")
        body = self.client.post(
            f"/v1/datasets/{dataset_id}/access-plan",
            {
                "distribution_id": distribution_id,
                "time_start": time_start,
                "time_end": time_end,
                "bbox": bbox,
                "variables": variables or [],
            },
        )
        return self._capped(body, ids=[body.get("dataset_id")], what="the plan")

    # -- tier 1 ------------------------------------------------------------

    def author_workflow(
        self,
        goal: str,
        dataset_ids: list[str],
        *,
        steps: list[str] | None = None,
    ) -> ToolResult:
        """A structured, **inert** workflow specification. Nothing executes.

        Tier 1, and the gate is the point: this tool is present for every
        caller and refuses per call. A caller without the tier gets a 403
        naming the tier, not a missing tool — an agent that cannot see a tool
        invents a way around the gap rather than reporting it.

        Inert means inert. The specification references catalog datasets by id
        and describes steps; it contains no code, no credentials and no
        endpoint that would run it. Execution tiers 2 and 3 belong to Workflow
        Orchestration and are out of scope (PRD §F9).
        """
        self._require("author_workflow")

        # Every referenced dataset is fetched, not assumed. A workflow naming a
        # dataset that does not exist is the fabrication failure wearing a
        # different hat, and it would be handed to a user as a plan.
        resolved = []
        for dataset_id in dataset_ids:
            record = self.client.get(f"/v1/datasets/{dataset_id}")
            resolved.append(
                {
                    "dataset_id": record["id"],
                    "title": record.get("title"),
                    "license": record.get("license_id"),
                    "access": f"get_access_plan(dataset_id={record['id']!r})",
                }
            )

        return self._capped(
            {
                "goal": goal,
                "inert": True,
                "note": (
                    "A specification, not an execution. Nothing here runs, and OpenGrid will not "
                    "run it. Fetch each dataset with the access plan named against it."
                ),
                "datasets": resolved,
                "steps": steps or [],
            },
            ids=[d["dataset_id"] for d in resolved],
            what="the workflow",
        )

    # -- gating and caps ---------------------------------------------------

    def _require(self, tool: str) -> None:
        needed = TOOL_TIERS.get(tool, 0)
        if self.tier >= needed:
            return
        raise NotEntitledForTool(
            f"{tool} needs tier {needed}; this caller is tier {self.tier}. The tool is present "
            "for every caller and refuses per call, so an agent is told what it cannot do "
            "rather than left to infer it from an absence."
        )

    def _capped(self, data: Any, *, ids: list[str | None], what: str) -> ToolResult:
        """Enforce the byte cap, and say so when it bites.

        Truncation is never silent. An agent handed a truncated list that
        looked complete will answer "there are 12 such datasets" when there are
        400, and will be confident about it.
        """
        clean_ids = tuple(i for i in ids if i)
        encoded = json.dumps(data, default=str)
        if len(encoded.encode("utf-8")) <= self.payload_cap:
            return ToolResult(data=data, dataset_ids=clean_ids)

        trimmed, removed = _trim(data, self.payload_cap)
        return ToolResult(
            data=trimmed,
            truncated=True,
            truncation_note=(
                f"{what} exceeded the {self.payload_cap // 1024} KB payload cap and was cut to "
                f"{removed} fewer item(s). This response is INCOMPLETE: do not report a count or "
                "a conclusion from it. Narrow the query or page through it."
            ),
            dataset_ids=clean_ids,
        )


def _trim(data: Any, cap: int) -> tuple[Any, int]:
    """Shorten the longest list in a payload until it fits.

    The longest list rather than an arbitrary field: a search response is a
    small envelope around a long list, and trimming the envelope would remove
    the total count while leaving the thing that was actually too big.
    """
    if not isinstance(data, dict):
        return data, 0

    trimmed = dict(data)
    key = max(
        (k for k, v in trimmed.items() if isinstance(v, list)),
        key=lambda k: len(trimmed[k]),
        default=None,
    )
    if key is None:
        return trimmed, 0

    items = list(trimmed[key])
    removed = 0
    while items and len(json.dumps({**trimmed, key: items}, default=str).encode()) > cap:
        items.pop()
        removed += 1
    trimmed[key] = items
    if "returned" in trimmed:
        trimmed["returned"] = len(items)
    return trimmed, removed


def resolve_tier(client: ApiClient) -> int:
    """The caller's tier, from the API's view of them.

    From the API, never from the agent. An agent that could state its own tier
    would state the one it wanted, and PRD §F9 is explicit that every
    enforceable control lives server-side.
    """
    try:
        me = client.get("/v1/auth/me")
    except ApiError:
        return 0
    if not me.get("authenticated"):
        return 0
    return 1


Verb = Literal["search", "read", "plan", "author"]
ToolFn = Callable[..., ToolResult]

__all__ = [
    "PREVIEW_MAX_ROWS",
    "TOOL_TIERS",
    "NotEntitledForTool",
    "ToolResult",
    "Tools",
    "resolve_tier",
]
