import re
import uuid
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, event, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.request_context import current_request_id

# What an audited attempt came to. Stored, not inferred at read time, so a receipt states the
# decision the server actually made:
#   applied   the change was committed
#   denied    refused — permission, policy or a readiness gate; nothing changed
#   rejected  the request was not valid in the event's current state; nothing changed
#   failed    attempted and did not complete
#   pending   recorded and awaiting a separate decision (an approval, a review)
AUDIT_OUTCOMES = ("applied", "denied", "rejected", "failed", "pending")

_DENIED = re.compile(r"(denied|blocked|refused)")
_REJECTED = re.compile(r"(rejected|illegal_transition)")
_FAILED = re.compile(r"failed")
_PENDING = re.compile(r"(\.requested|_request|\.request)$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def outcome_for_action(action: str | None) -> str:
    """The outcome implied by an action's own name, for writers that do not state one.

    Only a fallback: every writer that knows its decision passes `outcome` explicitly. The
    names this reads are the ones the platform already uses for refusals
    (`commercial.golive.blocked`, `user.update.denied_no_elevation`, `*.transition_rejected`)
    and must agree with the backfill in create_tables._AUDIT_LOG_STATEMENTS."""
    name = (action or "").lower()
    if _DENIED.search(name):
        return "denied"
    if _REJECTED.search(name):
        return "rejected"
    if _FAILED.search(name):
        return "failed"
    if _PENDING.search(name):
        return "pending"
    return "applied"


def _as_uuid(value) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and _UUID.match(value):
        return uuid.UUID(value)
    return None


class AuditLog(Base):
    """Append-only record of every platform action. Actor is kept by id AND email so the
    entry survives a user deletion (no FK cascade — the log must outlive the actor)."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    # The actor's platform role WHEN they acted. A role read back from the users table at
    # display time would be the role they hold now, which is not what an audit row records.
    actor_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80), nullable=False)          # e.g. "organization.update"
    target_type: Mapped[str | None] = mapped_column(String(40))             # organization | user | subscription | setting
    target_id: Mapped[str | None] = mapped_column(String(64))
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The event this action concerns, when it concerns one. A column (indexed, with created_at)
    # rather than a key in `meta`, because the Event Workspace's Activity & Audit view reads
    # "everything for this event, newest first", which a JSON key cannot serve from an index.
    # Derived on insert from meta["event_id"] or an event target for writers that predate it.
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # See AUDIT_OUTCOMES. Derived from the action name on insert when a writer does not say.
    outcome: Mapped[str | None] = mapped_column(String(20))
    meta: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
    # Ties one financial event's whole chain together — provider event -> payment -> invoice
    # -> order -> event -> these audit rows (ZST-LE-COM-001 Section 30 "Event correlation":
    # "A single correlation chain must link event_id, event_order_id, capacity reservation,
    # payment/invoice, service profile, media session, recording and incident"). Indexed
    # because the operational question is always "show me everything for this correlation id".
    # Rows written inside an API request with no chain of their own carry that request's
    # reference (request_context), which is the id the console shows on a receipt.
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


@event.listens_for(AuditLog, "before_insert")
def _complete_audit_row(_mapper, _connection, row: AuditLog) -> None:
    """Fill the fields every writer would otherwise have to remember.

    There are several AuditLog writers (crud.admin.create_audit_log, crud.commercial.audit,
    support_access.record_action, custom_domains._audit, the live console through
    moderation.record). Deriving here, once, means each of them populates event_id, outcome and
    the request reference without being edited — and none of them can forget to."""
    if row.event_id is None:
        meta = row.meta if isinstance(row.meta, dict) else {}
        row.event_id = _as_uuid(meta.get("event_id")) or (
            _as_uuid(row.target_id) if row.target_type == "event" else None)
    if row.outcome is None:
        row.outcome = outcome_for_action(row.action)
    if row.correlation_id is None:
        row.correlation_id = current_request_id()
