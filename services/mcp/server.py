"""The MCP server (WP-10.2).

PRD §F9: *open-source, remotely hosted, thin client over the REST API. No
independent data access.*

This module is the protocol shell. Everything that could be wrong is in
:mod:`datahub.mcp.tools`, which has no MCP dependency and is tested without
one — so a change to the grounding rule or the payload cap is caught by a test
that does not need a protocol server, and the wiring here stays small enough to
read in one screen.

**Every tool is registered for every caller.** PRD §F9 is explicit that a
tier-gated tool must be *present* and refuse per call. An agent that cannot see
a tool does not report "I lack permission"; it invents a way around the gap,
which is worse for the user and invisible to us.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from datahub.logging import configure_logging, get_logger
from datahub.mcp.client import ApiClient, ApiError
from datahub.mcp.tools import NotEntitledForTool, Tools, resolve_tier
from pydantic import Field

log = get_logger(__name__)

INSTRUCTIONS = """\
The OpenGrid Data Hub: a catalog of grid-modelling datasets.

**This server never returns data.** It returns metadata, and access plans that
say where data is and how to read it. Fetch the data yourself using the plan.

**Absent means "not captured", never "no source".** A field missing from a
record is a gap in what has been catalogued, not a statement about the dataset.
Say so rather than filling it in.

**Nothing here is invented.** Every dataset, field and connection comes from
the catalog. If the catalog does not record something, this server says so;
prefer reporting the gap to producing a plausible answer.

**Check `explain_connection` before combining two datasets.** Datasets that
share an upstream source are not independent, and treating their agreement as
corroboration understates uncertainty.
"""


def build_tools(
    *,
    base_url: str | None = None,
    token: str | None = None,
    transport: Any = None,
) -> Tools:
    """A :class:`Tools` bound to one caller's identity."""
    client = ApiClient(
        base_url=base_url or os.environ.get("DATAHUB_API_URL", "http://localhost:8000"),
        token=token or os.environ.get("DATAHUB_API_TOKEN"),
        transport=transport,
    )
    return Tools(client=client, tier=resolve_tier(client))


def create_server(tools: Tools | None = None, **kwargs: Any) -> Any:
    """The FastMCP server, with all seven tools registered.

    ``fastmcp`` is imported here rather than at module scope so the tools and
    the client stay importable — and testable — in a deployment that does not
    install the MCP extra. The tools are where anything can be wrong; the
    protocol shell should not be what stops them being exercised.
    """
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError(
            "the MCP server needs fastmcp with server support. "
            'Install it with: pip install "opengrid-datahub[mcp]"'
        ) from exc

    tools = tools or build_tools(**kwargs)
    mcp: Any = FastMCP(name="opengrid-datahub", instructions=INSTRUCTIONS)

    def _wrap(fn: Any) -> Any:
        """Turn a tool's exceptions into something an agent can act on.

        A 403 says *what tier is needed*, because "forbidden" leaves an agent
        to guess whether to retry, to authenticate, or to give up — and it will
        guess wrong in a way the user sees as a fabrication.
        """

        def call(**params: Any) -> dict[str, Any]:
            try:
                return fn(**params).as_dict()
            except NotEntitledForTool as exc:
                return {"error": "forbidden", "status": 403, "detail": str(exc)}
            except ApiError as exc:
                return {"error": "api_error", "status": exc.status, "detail": exc.detail}

        return call

    @mcp.tool(name="search_datasets")
    def search_datasets(
        q: Annotated[str | None, Field(description="Free text.")] = None,
        data_domain: Annotated[list[str] | None, Field(description="Domain IRIs.")] = None,
        concept: Annotated[list[str] | None, Field(description="Concept IRIs.")] = None,
        license_id: str | None = None,
        anonymous_access: Annotated[
            bool | None, Field(description="Only datasets needing no account.")
        ] = None,
        bbox: Annotated[
            list[float] | None, Field(description="[minLon, minLat, maxLon, maxLat].")
        ] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the catalog. Entitlement-scoped and payload-capped."""
        return _wrap(tools.search_datasets)(
            q=q,
            data_domain=data_domain,
            concept=concept,
            license=license_id,
            anonymous_access=anonymous_access,
            bbox=bbox,
            limit=limit,
            offset=offset,
        )

    @mcp.tool(name="get_dataset")
    def get_dataset(dataset_id: str) -> dict[str, Any]:
        """One record in full, exactly as the catalog holds it."""
        return _wrap(tools.get_dataset)(dataset_id=dataset_id)

    @mcp.tool(name="get_dataset_schema")
    def get_dataset_schema(dataset_id: str) -> dict[str, Any]:
        """Field-level metadata, including the fields that resolve to nothing
        and the stated reason why."""
        return _wrap(tools.get_dataset_schema)(dataset_id=dataset_id)

    @mcp.tool(name="explain_connection")
    def explain_connection(dataset_id: str, other_dataset_id: str) -> dict[str, Any]:
        """Why two datasets are linked, including any shared-origin warning.

        Call this before combining two datasets. Two that trace to the same
        upstream are not independent, and their agreement is not corroboration.
        """
        return _wrap(tools.explain_connection)(
            dataset_id=dataset_id, other_dataset_id=other_dataset_id
        )

    @mcp.tool(name="preview_dataset")
    def preview_dataset(dataset_id: str, rows: int = 10) -> dict[str, Any]:
        """The dataset's shape — fields, types, units, access paths. Not its
        data: OpenGrid is a control plane and never returns bytes."""
        return _wrap(tools.preview_dataset)(dataset_id=dataset_id, rows=rows)

    @mcp.tool(name="get_access_plan")
    def get_access_plan(
        dataset_id: str,
        distribution_id: str | None = None,
        time_start: str | None = None,
        time_end: str | None = None,
        bbox: list[float] | None = None,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        """Where the data is and how to read it, under your identity. Carries
        the licence and attribution you are bound by."""
        return _wrap(tools.get_access_plan)(
            dataset_id=dataset_id,
            distribution_id=distribution_id,
            time_start=time_start,
            time_end=time_end,
            bbox=bbox,
            variables=variables,
        )

    @mcp.tool(name="author_workflow")
    def author_workflow(
        goal: str, dataset_ids: list[str], steps: list[str] | None = None
    ) -> dict[str, Any]:
        """Draft an inert workflow specification. **Requires tier 1.**

        Nothing executes: this returns a document. Every dataset it names is
        fetched from the catalog first, so a workflow cannot reference one that
        does not exist.
        """
        return _wrap(tools.author_workflow)(goal=goal, dataset_ids=dataset_ids, steps=steps)

    _ = (
        search_datasets,
        get_dataset,
        get_dataset_schema,
        explain_connection,
        preview_dataset,
        get_access_plan,
        author_workflow,
    )
    return mcp


def main() -> None:  # pragma: no cover - process entry point
    configure_logging()
    log.info("mcp server starting", api=os.environ.get("DATAHUB_API_URL"))
    create_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["INSTRUCTIONS", "build_tools", "create_server", "main"]
