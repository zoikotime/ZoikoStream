"""Tool Gateway (ZST-UX-GUIDE-001 §14). Importing the package registers every read tool."""
from . import analytics, events, live, media, org  # noqa: F401  (registration side effect)
from .base import REGISTRY, Tool, ToolContext, ToolResult

__all__ = ["REGISTRY", "Tool", "ToolContext", "ToolResult", "run"]


def run(tool_id: str, db, actor, ctx: ToolContext) -> ToolResult:
    """Run one registered tool. The caller has already resolved the actor, the capability and
    (for event tools) a tenant-checked event."""
    tool = REGISTRY[tool_id]
    if tool.needs_event and ctx.event is None:
        raise ValueError(f"{tool_id} needs an event in context")
    return tool.handler(db, actor, ctx)
