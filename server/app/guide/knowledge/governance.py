"""Content governance for the public corpus (RBAC §5–8, §16–17, §22; C9 audit events).

Every source file in knowledge/sources carries its approval evidence in front matter:

    author: <who wrote this version>
    owner_role: product | commercial | legal | privacy | security | support | engineering |
                accessibility
    approved_by: <KB approver identity>          # R-KB-APPROVER
    approved_at: 2026-10-20                       # ISO date; expires with the review cadence
    domain_approvals:                             # RBAC §8 overlays
      - {domain: commercial, approved_by: ..., approved_at: 2026-10-20}

Rules enforced at ingest (refusals are audited as "content_rejected"):
  * approval present and not expired (approved_at + review cadence of the risk class);
  * the domain overlay that owns the business truth: pricing -> commercial (SOD-03),
    legal/privacy -> legal or privacy (SOD-04), public security/trust -> security (SOD-05);
    "platform admin is not business authority" — no other role substitutes;
  * separation of duties: the author never approves their own version, nor its domain
    overlay (SOD-01), and for HIGH-risk content is not also the publisher;
  * `approved_by: pending-approval` marks content whose approval has not been recorded yet.
    Outside production it may be indexed (GUIDE_KB_ALLOW_PENDING_APPROVAL) with a warning;
    production refuses it, always.

Break-glass (RBAC §17): emergency quarantine of a source, or disabling static retrieval, with
a mandatory reason code + incident reference, audited as "break_glass". It only ever REMOVES
eligibility; it can never publish or resurrect content (RAG §25.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

PENDING = "pending-approval"
OWNER_ROLES = ("product", "commercial", "legal", "privacy", "security", "support",
               "engineering", "accessibility")
# RBAC §8 overlays that are MANDATORY (SOD-03/04/05). Other classes may record an overlay
# ("as designated") but do not require one.
REQUIRED_DOMAINS = {
    "pricing": {"commercial"},
    "legal": {"legal", "privacy"},
    "trust": {"security"},
}
BREAK_GLASS_REASONS = ("incident", "legal_hold", "security_exposure", "content_error",
                       "privacy_request")


class GovernanceError(RuntimeError):
    pass


@dataclass
class ApprovalCheck:
    source_id: str
    version: str
    problems: list[str] = field(default_factory=list)   # refuse
    pending: list[str] = field(default_factory=list)    # refuse in production only

    @property
    def state(self) -> str:
        if self.problems:
            return "rejected"
        return "pending" if self.pending else "approved"


def _as_dt(value) -> datetime | None:
    if value in (None, "", PENDING):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _ident(value) -> str | None:
    v = str(value).strip().lower() if value not in (None, "") else None
    return v or None


def check_approval(meta: dict, *, review_days: int, now: datetime,
                   publisher: str | None = None) -> ApprovalCheck:
    chk = ApprovalCheck(str(meta.get("source_id")), str(meta.get("version")))
    author = _ident(meta.get("author"))
    approver = _ident(meta.get("approved_by"))
    owner_role = meta.get("owner_role")
    if owner_role not in OWNER_ROLES:
        chk.problems.append(f"owner_role must be one of {OWNER_ROLES}")

    def _approval(who, when, label: str) -> None:
        if who in (None, PENDING):
            chk.pending.append(f"{label} pending")
            return
        at = _as_dt(when)
        if at is None:
            chk.problems.append(f"{label} has no valid approved_at")
            return
        if at > now + timedelta(days=1):
            chk.problems.append(f"{label} approved_at is in the future")
        elif at + timedelta(days=review_days) < now:
            chk.pending.append(f"{label} expired (approved {at.date()}, review every "
                               f"{review_days} days)")
        if author and who == author:
            chk.problems.append(f"separation of duties: author also {label}")  # SOD-01

    _approval(approver, meta.get("approved_at"), "approver")

    overlays = meta.get("domain_approvals") or []
    if not isinstance(overlays, list):
        chk.problems.append("domain_approvals must be a list")
        overlays = []
    required = REQUIRED_DOMAINS.get(meta.get("source_type"), set())
    have = {}
    for o in overlays:
        if not isinstance(o, dict) or o.get("domain") not in OWNER_ROLES:
            chk.problems.append("domain_approvals entries need a known domain")
            continue
        have[o["domain"]] = o
    if required:
        matched = [have[d] for d in sorted(required) if d in have]
        if not matched:
            chk.problems.append(f"missing domain approval overlay ({' or '.join(sorted(required))})")
        for o in matched:
            _approval(_ident(o.get("approved_by")), o.get("approved_at"),
                      f"{o['domain']} domain approver")
    if (publisher and author and _ident(publisher) == author
            and meta.get("risk_class") == "HIGH"):
        chk.problems.append("separation of duties: author also publisher of HIGH-risk content")
    return chk


def enforce(checks: list[ApprovalCheck]) -> list[str]:
    """Raise GovernanceError when any source may not be indexed here. Returns warnings."""
    from ..settings import get_settings
    s = get_settings()
    allow_pending = s.GUIDE_KB_ALLOW_PENDING_APPROVAL and not s.is_production()
    errors, warnings = [], []
    for c in checks:
        for p in c.problems:
            errors.append(f"{c.source_id}@{c.version}: {p}")
        for p in c.pending:
            (warnings if allow_pending else errors).append(f"{c.source_id}@{c.version}: {p}")
    if errors:
        raise GovernanceError("content approval refused: " + "; ".join(errors))
    return warnings


def approval_snapshot(meta: dict, check: ApprovalCheck) -> dict:
    """Approval evidence recorded per source in the release manifest (RBAC §22
    auth.content.approved/published: actor, source/version, domain approval evidence)."""
    return {
        "version": meta["version"],
        "approval_state": check.state,
        "owner_role": meta.get("owner_role"),
        "approved_by": meta.get("approved_by"),
        "approved_at": str(meta.get("approved_at")) if meta.get("approved_at") else None,
        "domain_approvals": [
            {"domain": o.get("domain"), "approved_by": o.get("approved_by"),
             "approved_at": str(o.get("approved_at")) if o.get("approved_at") else None}
            for o in (meta.get("domain_approvals") or []) if isinstance(o, dict)],
    }


def audit_event(event_type: str, **metadata) -> None:
    """Write one governance audit row in its OWN transaction, so a refusal is recorded even
    though the ingest transaction that triggered it rolls back (C9: ids/codes/enums only)."""
    from ..db import new_session
    from ..sessions import audit
    db = new_session()
    try:
        audit(db, None, event_type, **metadata)
        db.commit()
    finally:
        db.close()
