"""Activity & Audit read model (ZST-ENG-DASH-001 EVT-07 and the org Security & Audit view).

What an organization admin sees of `audit_logs`, and nothing else. Writers are free to record
what they need for compliance (services/event_audit, moderation.record, crud.commercial.audit,
crud.admin.create_audit_log); this module decides what of a row may leave the server:

* Scope is decided here, from the token's organization and an event the caller has already
  resolved through an org-scoped lookup. Nothing a client sends widens it, and an event of
  another organization never reaches this module (the routers answer it with a 404 first).
* Detail is WHITELISTED. A row's meta is reduced to a fixed set of keys whose values are codes,
  counts or statuses; everything else — emails, IPs, free text, tokens, URLs, internal ids —
  is dropped, so a careless writer still cannot leak through the console (EVT-07 notes 4).
* Identity is role-limited. A member is shown by name with the role recorded when they acted;
  platform staff are "ZoikoStream staff", never a named person or an email; an action with no
  actor is the platform's own.
* Outcome is the server's recorded decision (models.audit_log.AUDIT_OUTCOMES). A refused
  attempt is titled as an attempt, never with the past tense of the change it did not make
  (EVT-07 QA D).
* Integrity is stated as what is actually established. The rows live in an ordinary table: no
  hash chain, no immutable storage, no trusted time anchoring has been verified, and the
  response says exactly that rather than implying any of it (EVT-07 notes 8, QA F).
"""
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, case, false, func, literal, or_, select
from sqlalchemy.orm import Session

from ..models import AuditLog, Event, User
from .org import _role_label

# ── Categories ───────────────────────────────────────────────────────────────────────────
# The filter chips of the EVT-07 ledger ("All activity / Access & security / Production
# changes / Evidence") plus the two kinds of row an event and an organization also have.
CATEGORY_LABELS = {
    "access": "Access & security",
    "production": "Production changes",
    "evidence": "Evidence",
    "engagement": "Audience interaction",
    "commercial": "Billing & commercial",
    "other": "Other",
}
CATEGORIES = tuple(CATEGORY_LABELS)

# First match wins, so the order is the precedence: the go-live gate is a production decision
# even though it is recorded under commercial.*, and a delivery download (`recording.access`)
# is evidence even though it ends in ".access".
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (category, prefixes, suffixes)
    ("production", ("commercial.golive.", "commercial.readiness.", "commercial.incident.",
                    "commercial.event."), ()),
    ("evidence", ("report.", "export.", "media.legal_hold", "retention.", "developer_export.",
                  "recording."), (".access",)),
    ("engagement", ("live.message.", "live.question.", "live.poll.", "live.announcement."), ()),
    ("access", ("event.access_link.", "event.viewers.", "event.team.", "event.contributor.",
                "support_access.", "api_key.", "user.", "member.", "invitation.", "access_review.",
                "ownership_transfer.", "security_contact.", "sso.", "session.", "role."), ()),
    ("production", ("event.", "live.", "live_input."), ()),
    ("commercial", ("commercial.", "subscription.", "billing.", "payment."), ()),
)

# Platform-internal finance and maintenance bookkeeping. These rows can carry an org_id (they
# are ABOUT the organization) but they record ZoikoStream's own back-office work — settlement
# matching, period close, provider webhooks, catalog publication — not anything the
# organization did or decided, and they are not the organization's to read.
_INTERNAL_PREFIXES = (
    "commercial.maintenance.", "commercial.reconciliation_exception.", "commercial.period.",
    "commercial.settlement.", "commercial.provider_event.", "commercial.write_off.",
    "commercial.seller_entity.", "commercial.catalog.", "commercial.capacity_pool.",
    "commercial.capacity.", "commercial.cancellation_policy.", "commercial.service_profile.",
    "commercial.order.tax_determination", "commercial.refund_credit.", "commercial.lifecycle.",
    "commercial.payment.reconciliation_conflict", "commercial.payment.reconciled",
)

# ── High impact ──────────────────────────────────────────────────────────────────────────
# "High-impact actions (start/stop, source switch, viewer policy, revocation, exports)"
# (EVT-07 notes 6): counted and flagged separately so they cannot hide in the volume.
HIGH_IMPACT_ACTIONS = frozenset({
    "live.broadcast.golive", "live.broadcast.emergency_stop", "live.broadcast.pause",
    "live.recording.start", "live.recording.stop",
    "event.end", "event.delete", "event.archive",
    "event.access_link.revoke", "event.access_link.rotate", "event.access_link.delete",
    "event.contributor.revoke",
    "export.create", "export.revoke", "report.release",
    "commercial.golive.approved", "commercial.golive.approved_via_exception",
    "commercial.golive.blocked",
    "media.legal_hold_place", "media.legal_hold_release",
})
_HIGH_IMPACT_STATUSES = ("armed", "live", "ended", "cancelled")

OUTCOME_LABELS = {
    "applied": "Applied",
    "denied": "Denied",
    "rejected": "Rejected",
    "failed": "Failed",
    "pending": "Awaiting decision",
}
REFUSED_OUTCOMES = ("denied", "rejected")

# Why something was refused, as words (services/event_audit.REASON_*).
REASONS = {
    "not_event_host": "Only an organization admin or an assigned host can change this event.",
    "transition_not_allowed": "The event's current status does not allow this change.",
    "not_on_air": "The event was not live, so there was nothing to end.",
    "invalid_schedule": "The schedule supplied was not valid.",
}

INTEGRITY = {
    # Every entry the reader returns was selected by organization and, in the event view, by
    # event — on the server, from the token. That much is a property of this code.
    "tenant_scope": {"state": "explicit", "label": "Explicit in each record"},
    # None of these has a verifier in this deployment. Stated as such; never as a pass.
    "hash_verification": {"state": "not_established", "label": "Not established"},
    "immutability": {"state": "not_asserted", "label": "Not asserted"},
    "time_anchoring": {"state": "not_verified", "label": "Not verified"},
}
INTEGRITY_NOTE = ("A row appearing in this ledger is not proof of tamper-proof storage or "
                  "independently verified evidence.")

# Audit evidence export does not exist as a governed pathway yet (scope + purpose, independent
# approval, packaging, expiry, logged download). Until it does the console must not offer a
# download or imply one is coming (EVT-07 notes 7, QA C).
EXPORT_STATE = {
    "available": False,
    "state": "not_offered",
    "label": "Not offered",
    "message": ("Audit evidence export is not available yet. No evidence package has been "
                "generated or released for this scope."),
}

# ── Titles ───────────────────────────────────────────────────────────────────────────────
# action -> (what happened, what was attempted). The first is used when the change was
# applied (or, for a request, recorded); the second when it was refused or failed, so a
# rejected attempt never reads as a change that happened.
LABELS: dict[str, tuple[str, str]] = {
    "event.create": ("Event created", "Create event"),
    "event.update": ("Event details edited", "Edit event details"),
    "event.status.change": ("Event status changed", "Change event status"),
    "event.end": ("Event ended", "End event"),
    "event.archive": ("Event archived", "Archive event"),
    "event.unarchive": ("Event restored from archive", "Restore event from archive"),
    "event.delete": ("Event deleted", "Delete event"),
    "event.team.assign": ("Event team changed", "Change event team"),
    "event.viewers.invite": ("Viewers invited", "Invite viewers"),
    "event.access_link.create": ("Access link created", "Create access link"),
    "event.access_link.rotate": ("Access link rotated", "Rotate access link"),
    "event.access_link.revoke": ("Access link revoked", "Revoke access link"),
    "event.access_link.delete": ("Access link deleted", "Delete access link"),
    "event.contributor.invite": ("Contributor invited", "Invite contributor"),
    "event.contributor.revoke": ("Contributor access revoked", "Revoke contributor access"),
    "event.intake.complete": ("Event intake completed", "Complete event intake"),
    "event.planning.update": ("Planning item updated", "Update planning item"),
    "event.rehearsal.outcome": ("Rehearsal outcome recorded", "Record rehearsal outcome"),
    "event.brief.approve": ("Event brief approved", "Approve event brief"),
    "event.incident.open": ("Incident opened", "Open incident"),
    "event.incident.resume": ("Incident resolved and resumed", "Resume from incident"),
    "event.incident.cancel": ("Incident closed with cancellation", "Close incident with cancellation"),
    "live.broadcast.golive": ("Broadcast started", "Start broadcast"),
    "live.broadcast.pause": ("Broadcast paused", "Pause broadcast"),
    "live.broadcast.resume": ("Broadcast resumed", "Resume broadcast"),
    "live.broadcast.emergency_stop": ("Broadcast stopped", "Stop broadcast"),
    "live.broadcast.host": ("Broadcast host changed", "Change broadcast host"),
    "live.broadcast.settings": ("Broadcast settings changed", "Change broadcast settings"),
    "live.recording.start": ("Recording started", "Start recording"),
    "live.recording.stop": ("Recording stopped", "Stop recording"),
    "live.recording.paused": ("Recording paused", "Pause recording"),
    "live.recording.resumed": ("Recording resumed", "Resume recording"),
    "live.stage.admit": ("Speaker admitted to stage", "Admit speaker to stage"),
    "live.stage.admit_all": ("All waiting speakers admitted", "Admit all waiting speakers"),
    "live.stage.mute_all": ("Stage muted", "Mute stage"),
    "live.participant.request_unmute": ("Unmute requested", "Request unmute"),
    "live.contributor.admit": ("Contributor admitted", "Admit contributor"),
    "live.contributor.standby": ("Contributor put on standby", "Put contributor on standby"),
    "live.contributor.rehearsed": ("Contributor marked rehearsed", "Mark contributor rehearsed"),
    "live.contributor.help": ("Contributor asked for help", "Ask for help"),
    "live.message.auto_removed": ("Chat message removed automatically", "Remove chat message"),
    "live.question.respond": ("Question answered", "Answer question"),
    "live.question.delete": ("Question removed", "Remove question"),
    "live.poll.create": ("Poll created", "Create poll"),
    "live.poll.update": ("Poll updated", "Update poll"),
    "live.poll.delete": ("Poll deleted", "Delete poll"),
    "live.announcement.send": ("Announcement sent", "Send announcement"),
    "live.announcement.delete": ("Announcement removed", "Remove announcement"),
    "commercial.golive.approved": ("Go-live readiness approved", "Go-live readiness check"),
    "commercial.golive.approved_via_exception": ("Go-live approved under an exception",
                                                 "Go-live readiness check"),
    "commercial.golive.blocked": ("Go-live blocked by readiness gate", "Go live"),
    "commercial.readiness.record": ("Readiness evidence recorded", "Record readiness evidence"),
    "report.generate": ("Report generated", "Generate report"),
    "report.release": ("Report released", "Release report"),
    "export.create": ("Export created", "Create export"),
    "export.download": ("Export downloaded", "Download export"),
    "export.revoke": ("Export revoked", "Revoke export"),
    "media.legal_hold_place": ("Legal hold placed", "Place legal hold"),
    "media.legal_hold_release": ("Legal hold released", "Release legal hold"),
    "retention.extension_request": ("Retention extension requested", "Request retention extension"),
    "api_key.create": ("API key created", "Create API key"),
    "api_key.rotate": ("API key rotated", "Rotate API key"),
    "api_key.revoke": ("API key revoked", "Revoke API key"),
    "webhook.create": ("Webhook endpoint added", "Add webhook endpoint"),
    "webhook.update": ("Webhook endpoint updated", "Update webhook endpoint"),
    "webhook.delete": ("Webhook endpoint removed", "Remove webhook endpoint"),
    "webhook.secret_rotate": ("Webhook secret rotated", "Rotate webhook secret"),
    "support_access.approve": ("Support access approved", "Approve support access"),
    "support_access.denied_out_of_scope": ("Support access outside scope refused",
                                           "Support access outside scope"),
    "access_review.open": ("Access review opened", "Open access review"),
    "access_review.decide": ("Access review decision recorded", "Record access review decision"),
    "access_review.complete": ("Access review completed", "Complete access review"),
    "ownership_transfer.initiate": ("Ownership transfer started", "Start ownership transfer"),
    "ownership_transfer.confirm": ("Ownership transfer confirmed", "Confirm ownership transfer"),
    "ownership_transfer.complete": ("Ownership transferred", "Transfer ownership"),
    "ownership_transfer.cancel": ("Ownership transfer cancelled", "Cancel ownership transfer"),
    "security_contact.nominate": ("Security contact nominated", "Nominate security contact"),
    "security_contact.revoke": ("Security contact removed", "Remove security contact"),
    "notification_preferences.update": ("Notification preferences changed",
                                        "Change notification preferences"),
    "support_case.open": ("Support case opened", "Open support case"),
    "support_case.reopen": ("Support case reopened", "Reopen support case"),
}

_STATUS_WORDS = {
    "draft": "Draft", "published": "Published", "scheduled": "Scheduled",
    "ready_to_arm": "Ready to arm", "armed": "Armed", "live": "Live", "degraded": "Degraded",
    "ended": "Ended", "cancelled": "Cancelled", "archived": "Archived",
}
_FIELD_WORDS = {
    "title": "Title", "description": "Description", "start_time": "Start time",
    "end_time": "End time", "timezone": "Time zone", "visibility": "Visibility",
    "category": "Category", "capacity": "Capacity", "cover_image_url": "Cover image",
    "recording_enabled": "Recording", "chat_enabled": "Chat", "qa_enabled": "Q&A",
    "registration_required": "Registration", "slug": "Event link",
}

# A meta value is shown only if it looks like a server-generated code: lower-case words,
# digits and separators, short. Free text a person typed never matches this, which is the
# point — a writer that put a reason or a name in meta cannot surface it here.
_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,59}$")


def humanize(code: str) -> str:
    text = re.sub(r"[_.:-]+", " ", code or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def _code(value) -> str | None:
    return value if isinstance(value, str) and _CODE.match(value) else None


def _status_word(value) -> str | None:
    code = _code(value)
    return _STATUS_WORDS.get(code, humanize(code)) if code else None


def title_for(action: str, outcome: str | None) -> str:
    done, attempt = LABELS.get(action, (None, None))
    if done is None:
        # An action this module has no wording for still reads as words, and still never
        # reads as a success when it was not one.
        done = attempt = humanize(action)
    if outcome in ("denied", "rejected", "failed"):
        return f"{attempt} — {OUTCOME_LABELS[outcome].lower()}"
    return done


# What a bare `status` key means depends on who wrote it.
_STATUS_KEY_LABEL = {
    "event.create": "Initial status",
    "event.planning.update": "Planning status",
    "event.rehearsal.outcome": "Result",
}


def _details(action: str, meta: dict | None) -> tuple[list[dict], str | None]:
    """(whitelisted detail rows, reason sentence). See the module docstring."""
    meta = meta if isinstance(meta, dict) else {}
    rows: list[dict] = []

    def add(label: str, value) -> None:
        if value not in (None, "", []):
            rows.append({"label": label, "value": str(value)})

    before, after = _status_word(meta.get("from_status")), _status_word(meta.get("to_status"))
    if before and after:
        add("Status", f"{before} → {after}")
    elif after:
        add("Status", after)
    elif before:
        add("Status before", before)
    if action in _STATUS_KEY_LABEL:
        add(_STATUS_KEY_LABEL[action], _status_word(meta.get("status")))
    add("Requested state", _status_word(meta.get("target_state")))

    fields = meta.get("fields")
    if isinstance(fields, list):
        names = [_FIELD_WORDS.get(f, humanize(f)) for f in fields if _code(f)][:12]
        add("Changed", ", ".join(names))

    role = _code(meta.get("role"))
    if all(isinstance(meta.get(k), int) for k in ("added", "removed", "total")):
        noun = humanize(role).lower() + "s" if role else "members"
        add("Team", f"+{meta['added']} / −{meta['removed']} · {meta['total']} {noun} now")
    elif role:
        add("Role", _role_label(role) if role != "member" else "Member")

    kind = _code(meta.get("kind"))
    if kind:
        add("Contributor type", {"speaker": "Speaker", "guest": "Guest contributor"}.get(kind, humanize(kind)))
    if isinstance(meta.get("count"), int):
        add("Recipients", meta["count"])
    if isinstance(meta.get("expires_in_days"), int):
        add("Expires after", f"{meta['expires_in_days']} days")
    if meta.get("forced") is True:
        add("Forced", "Yes — no live session answered the stop request")
    category = _code(meta.get("category"))
    if category:
        add("Planning area", humanize(category))
    state = _code(meta.get("state"))
    if state:
        add("Operational state", humanize(state))
    reason_category = _code(meta.get("reason_category"))
    if reason_category:
        add("Incident reason", humanize(reason_category))
    if isinstance(meta.get("version"), int):
        add("Version", meta["version"])

    reason = REASONS.get(meta.get("reason_code")) if isinstance(meta.get("reason_code"), str) else None
    blocking = meta.get("blocking_reasons")
    if isinstance(blocking, list):
        codes = [humanize(b) for b in blocking if _code(b)][:5]
        if codes:
            add("Blocked by", ", ".join(codes))
            reason = reason or "The go-live readiness gate refused it. Nothing went live."
    exceptions = meta.get("exceptions_applied")
    if isinstance(exceptions, list) and exceptions:
        add("Exceptions applied", len(exceptions))
    return rows, reason


# ── SQL expressions shared by filters, facets and counts ─────────────────────────────────

def _like_prefix(prefix: str):
    return AuditLog.action.like(prefix.replace("_", r"\_").replace("%", r"\%") + "%", escape="\\")


def _like_suffix(suffix: str):
    return AuditLog.action.like("%" + suffix.replace("_", r"\_").replace("%", r"\%"), escape="\\")


def _category_expr():
    whens = []
    for category, prefixes, suffixes in _CATEGORY_RULES:
        conditions = [_like_prefix(p) for p in prefixes] + [_like_suffix(s) for s in suffixes]
        whens.append((or_(*conditions), literal(category)))
    return case(*whens, else_=literal("other"))


def _high_impact_expr():
    return or_(
        AuditLog.action.in_(HIGH_IMPACT_ACTIONS),
        and_(AuditLog.action == "event.status.change",
             AuditLog.meta["to_status"].as_string().in_(_HIGH_IMPACT_STATUSES)),
    )


def _not_internal():
    return and_(*[~_like_prefix(p) for p in _INTERNAL_PREFIXES])


def _org_member_ids(org_id):
    return select(User.id).where(User.org_id == org_id)


def scope_clause(org_id: uuid.UUID, event_id: uuid.UUID | None = None):
    """Everything this organization may read, optionally narrowed to one of its events.

    For an event, `event_id` is the binding condition and `org_id` may be absent on a row a
    platform job wrote without one — but it may never be ANOTHER organization's id."""
    if event_id is not None:
        return and_(AuditLog.event_id == event_id,
                    or_(AuditLog.org_id == org_id, AuditLog.org_id.is_(None)),
                    _not_internal())
    return and_(AuditLog.org_id == org_id, _not_internal())


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("_", r"\_").replace("%", r"\%")


def filter_clause(org_id, *, category: str | None = None, outcome: str | None = None,
                  actor: str | None = None, since: datetime | None = None,
                  until: datetime | None = None, q: str | None = None,
                  high_impact: bool | None = None):
    clauses = []
    if category:
        clauses.append(_category_expr() == category)
    if outcome == "refused":
        clauses.append(AuditLog.outcome.in_(REFUSED_OUTCOMES))
    elif outcome:
        clauses.append(AuditLog.outcome == outcome)
    if actor == "platform":
        clauses.append(AuditLog.actor_id.is_(None))
    elif actor == "staff":
        clauses.append(and_(AuditLog.actor_id.is_not(None), AuditLog.actor_id.not_in(_org_member_ids(org_id))))
    elif actor:
        try:
            clauses.append(AuditLog.actor_id == uuid.UUID(actor))
        except ValueError:
            clauses.append(false())
    if since is not None:
        clauses.append(AuditLog.created_at >= since)
    if until is not None:
        clauses.append(AuditLog.created_at < until)
    if high_impact:
        clauses.append(_high_impact_expr())
    if q:
        needle = _escape_like(q.strip()[:100])
        member_match = select(User.id).where(User.org_id == org_id,
                                             User.full_name.ilike(f"%{needle}%", escape="\\"))
        clauses.append(or_(
            AuditLog.action.ilike(f"%{needle}%", escape="\\"),
            AuditLog.correlation_id.ilike(f"{needle}%", escape="\\"),
            AuditLog.actor_id.in_(member_match),
        ))
    return and_(*clauses) if clauses else None


# ── Rows → entries ───────────────────────────────────────────────────────────────────────

def _actors(db: Session, org_id, actor_ids) -> dict:
    ids = {a for a in actor_ids if a is not None}
    if not ids:
        return {}
    users = db.execute(select(User.id, User.full_name, User.org_id, User.deleted_at)
                       .where(User.id.in_(ids))).all()
    return {u.id: u for u in users}


def _actor_out(row: AuditLog, users: dict, org_id) -> dict:
    if row.actor_id is None:
        return {"kind": "platform", "name": "ZoikoStream platform", "role": None, "role_label": "Automated"}
    user = users.get(row.actor_id)
    if user is not None and user.org_id != org_id:
        # Platform staff acting on this organization (support, live ops, finance). Their
        # identity is not the organization's to read; the fact that staff acted is.
        return {"kind": "staff", "name": "ZoikoStream staff", "role": None, "role_label": "Platform staff"}
    if user is None or user.deleted_at is not None:
        return {"kind": "former_member", "name": "Former member", "role": row.actor_role,
                "role_label": _role_label(row.actor_role) if row.actor_role else None}
    return {"kind": "member", "id": str(user.id), "name": user.full_name or "Member",
            "role": row.actor_role,
            "role_label": _role_label(row.actor_role) if row.actor_role else None}


def entry_out(row: AuditLog, category: str, high_impact: bool, users: dict, org_id,
              events: dict | None = None) -> dict:
    outcome = row.outcome or "applied"
    details, reason = _details(row.action, row.meta)
    out = {
        "id": str(row.id),
        "at": row.created_at.astimezone(timezone.utc).isoformat() if row.created_at else None,
        "action": row.action,
        "title": title_for(row.action, outcome),
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, "Other"),
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS.get(outcome, humanize(outcome)),
        "changed_state": outcome == "applied",
        "high_impact": bool(high_impact),
        "actor": _actor_out(row, users, org_id),
        "details": details,
        "reason": reason,
        "reference": row.correlation_id,
    }
    if events is not None:
        ev = events.get(row.event_id) if row.event_id else None
        out["event"] = {"id": str(ev.id), "title": ev.title or "Untitled event"} if ev else None
    return out


def _select_rows(scope, filters):
    category = _category_expr().label("category")
    high = _high_impact_expr().label("high_impact")
    stmt = select(AuditLog, category, high).where(scope)
    if filters is not None:
        stmt = stmt.where(filters)
    return stmt


def _event_titles(db: Session, org_id, rows) -> dict:
    ids = {r.event_id for r in rows if r.event_id}
    if not ids:
        return {}
    events = db.scalars(select(Event).where(Event.id.in_(ids), Event.org_id == org_id)).all()
    return {e.id: e for e in events}


def summary(db: Session, scope) -> dict:
    """Counts over the WHOLE scope (not the filtered page): what the cards and chips show."""
    # Grouped over a subquery: the CASE carries bound LIKE patterns, and Postgres will not
    # match a GROUP BY expression to a SELECT expression whose parameters are numbered apart.
    rows = select(_category_expr().label("category"), AuditLog.outcome.label("outcome")).where(scope).subquery()
    by_category = {c: 0 for c in CATEGORIES}
    by_outcome = {o: 0 for o in OUTCOME_LABELS}
    total = 0
    for cat, outcome, n in db.execute(
        select(rows.c.category, rows.c.outcome, func.count()).group_by(rows.c.category, rows.c.outcome)
    ).all():
        total += n
        by_category[cat] = by_category.get(cat, 0) + n
        key = outcome or "applied"
        by_outcome[key] = by_outcome.get(key, 0) + n
    high = db.scalar(select(func.count()).select_from(AuditLog).where(scope, _high_impact_expr())) or 0
    last = db.scalar(select(func.max(AuditLog.created_at)).where(scope))
    return {
        "total": total,
        "high_impact": high,
        "refused": sum(by_outcome.get(o, 0) for o in REFUSED_OUTCOMES),
        "failed": by_outcome.get("failed", 0),
        "pending": by_outcome.get("pending", 0),
        "by_category": by_category,
        "by_outcome": by_outcome,
        "last_recorded_at": last.astimezone(timezone.utc).isoformat() if last else None,
    }


def actor_facets(db: Session, scope, org_id) -> list[dict]:
    """The actors present in this scope, for the "All actors" filter. Members by name; staff
    and the platform collapsed into one option each (same rule as _actor_out)."""
    counts = db.execute(select(AuditLog.actor_id, func.count()).where(scope)
                        .group_by(AuditLog.actor_id).order_by(func.count().desc()).limit(200)).all()
    users = _actors(db, org_id, [a for a, _ in counts])
    members, staff, platform = [], 0, 0
    for actor_id, n in counts:
        if actor_id is None:
            platform += n
            continue
        user = users.get(actor_id)
        if user is not None and user.org_id != org_id:
            staff += n
        elif user is not None and user.deleted_at is None:
            members.append({"value": str(actor_id), "label": user.full_name or "Member", "count": n})
    members.sort(key=lambda m: m["label"].lower())
    out = members[:50]
    if staff:
        out.append({"value": "staff", "label": "ZoikoStream staff", "count": staff})
    if platform:
        out.append({"value": "platform", "label": "ZoikoStream platform", "count": platform})
    return out


def page(db: Session, *, org_id, scope, filters, page_no: int, page_size: int,
         with_events: bool = False) -> dict:
    stmt = _select_rows(scope, filters)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                      .offset((page_no - 1) * page_size).limit(page_size)).all()
    users = _actors(db, org_id, [r.AuditLog.actor_id for r in rows])
    events = _event_titles(db, org_id, [r.AuditLog for r in rows]) if with_events else None
    return {
        "items": [entry_out(r.AuditLog, r.category, r.high_impact, users, org_id, events) for r in rows],
        "total": total,
        "page": page_no,
        "page_size": page_size,
    }


def entry(db: Session, *, org_id, scope, entry_id: uuid.UUID, with_events: bool = False) -> dict | None:
    row = db.execute(_select_rows(scope, AuditLog.id == entry_id)).first()
    if row is None:
        return None
    users = _actors(db, org_id, [row.AuditLog.actor_id])
    events = _event_titles(db, org_id, [row.AuditLog]) if with_events else None
    return entry_out(row.AuditLog, row.category, row.high_impact, users, org_id, events)


def boundaries() -> dict:
    return {"integrity": INTEGRITY, "integrity_note": INTEGRITY_NOTE, "export": EXPORT_STATE}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
