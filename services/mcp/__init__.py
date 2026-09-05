"""The MCP server (PRD §F9). A thin client over the REST API; no data access.

The boundary is load-bearing rather than tidy: a server that could read the
store could also compose and infer, and each of those is a place where
something plausible and untrue can be produced. A server that can only forward
what the API returned cannot fabricate a dataset, because it has no way to make
one.
"""

from datahub.mcp.client import ApiClient, ApiError
from datahub.mcp.tools import TOOL_TIERS, NotEntitledForTool, ToolResult, Tools

__all__ = [
    "TOOL_TIERS",
    "ApiClient",
    "ApiError",
    "NotEntitledForTool",
    "ToolResult",
    "Tools",
]
