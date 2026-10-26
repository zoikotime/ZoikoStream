"""Tool Gateway (ZST-UX-GUIDE-001 §14). Importing the package registers every read tool."""
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .. import errors
from ..config import get_settings
from . import analytics, events, live, media, org  # noqa: F401  (registration side effect)
from .base import REGISTRY, Tool, ToolContext, ToolResult

__all__ = ["REGISTRY", "Tool", "ToolContext", "ToolResult", "run"]

QUERY_CANCELED = "57014"   # PostgreSQL: statement_timeout reached


def run(tool_id: str, db, actor, ctx: ToolContext) -> ToolResult:
    """Run one registered tool. The caller has already resolved the actor, the capability and
    (for event tools) a tenant-checked event.

    On PostgreSQL each tool runs in its own savepoint under a statement budget
    (OPGUIDE_TOOL_TIMEOUT_SECONDS). A read that exceeds it is rolled back to the savepoint —
    the surrounding request stays usable — and surfaces as errors.Timeout (§14)."""
    tool = REGISTRY[tool_id]
    if tool.needs_event and ctx.event is None:
        raise ValueError(f"{tool_id} needs an event in context")
    if db.get_bind().dialect.name != "postgresql":
        return tool.handler(db, actor, ctx)
    budget_ms = max(int(get_settings().OPGUIDE_TOOL_TIMEOUT_SECONDS * 1000), 1)
    savepoint = db.begin_nested()
    try:
        # SET LOCAL inside a savepoint: reverted by the rollback below; reset on success.
        previous = db.execute(text("SHOW statement_timeout")).scalar()
        db.execute(text(f"SET LOCAL statement_timeout = {budget_ms}"))
        result = tool.handler(db, actor, ctx)
        db.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": previous})
        savepoint.commit()
        return result
    except OperationalError as exc:
        savepoint.rollback()
        if getattr(exc.orig, "pgcode", None) == QUERY_CANCELED:
            raise errors.Timeout(errors.timed_out(tool.owner)) from None
        raise
    except BaseException:
        if savepoint.is_active:
            savepoint.rollback()
        raise
