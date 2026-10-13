"""purge_after resolution (ZST-DB-PUB-GUIDE-001 §12; Security/Privacy §16).

The schema requires purge_after but never hard-codes a duration: every write asks this module,
which reads the active policy from settings. POLICY_VERSION is recorded in audit metadata so a
purge decision can be traced to the policy that produced it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .settings import get_settings

POLICY_VERSION = "2026.09"


def now() -> datetime:
    return datetime.now(timezone.utc)


def purge_after(data_class: str, *, at: datetime | None = None) -> datetime:
    s = get_settings()
    base = at or now()
    spans = {
        "session": timedelta(days=s.GUIDE_RETENTION_SESSION_DAYS),
        "conversation": timedelta(days=s.GUIDE_RETENTION_CONVERSATION_DAYS),
        "event_context": timedelta(hours=s.GUIDE_RETENTION_EVENT_CONTEXT_HOURS),
        "support_flow": timedelta(days=s.GUIDE_RETENTION_SUPPORT_FLOW_DAYS),
        "lead": timedelta(days=s.GUIDE_RETENTION_LEAD_FAILED_DAYS),
        "handoff": timedelta(days=s.GUIDE_RETENTION_HANDOFF_DAYS),
        "feedback_comment": timedelta(days=s.GUIDE_RETENTION_FEEDBACK_COMMENT_DAYS),
        "feedback": timedelta(days=s.GUIDE_RETENTION_FEEDBACK_DAYS),
        "consent": timedelta(days=s.GUIDE_RETENTION_CONSENT_DAYS),
        "audit": timedelta(days=s.GUIDE_RETENTION_AUDIT_DAYS),
    }
    return base + spans[data_class]
