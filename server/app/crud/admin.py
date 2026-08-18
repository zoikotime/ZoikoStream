"""Database access for the Super Admin API. Pure DB queries — no HTTP, no derived
business logic (that lives in services/admin.py). List helpers return (items, total)."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..models import (
    AuditLog,
    Event,
    FeatureFlag,
    GovernanceRecord,
    Incident,
    Organization,
    Plan,
    Release,
    STAFF_COMMERCIAL_ROLES,
    Subscription,
    SupportTicket,
    User,
)
from ..schemas.admin import (
    AdminUserOut,
    ApiKeyCreated,
    ApiKeyOut,
    AuditLogOut,
    FeatureFlagOut,
    GovernanceRecordOut,
    IncidentOut,
    OrgOut,
    PlanOut,
    ReleaseOut,
    SubscriptionOut,
    SupportTicketOut,
)

# Non-cancelled statuses rank above cancelled when picking an org's "current" subscription.
_ACTIVE_SUB = ("active", "trial", "past_due")


# ── Organizations ────────────────────────────────────────────────────────────

def _user_counts(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not org_ids:
        return {}
    rows = db.execute(
        select(User.org_id, func.count(User.id))
        .where(User.org_id.in_(org_ids))
        .group_by(User.org_id)
    ).all()
    return {oid: n for oid, n in rows}


def _event_counts(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Events owned directly by the org."""
    if not org_ids:
        return {}
    rows = db.execute(
        select(Event.org_id, func.count(Event.id))
        .where(Event.org_id.in_(org_ids), Event.deleted_at.is_(None))
        .group_by(Event.org_id)
    ).all()
    return {oid: n for oid, n in rows}


def _current_subs(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, Subscription]:
    """Best subscription per org: newest non-cancelled, else newest overall."""
    if not org_ids:
        return {}
    subs = db.scalars(
        select(Subscription)
        .where(Subscription.org_id.in_(org_ids))
        .order_by(Subscription.started_at.desc())
    ).all()
    best: dict[uuid.UUID, Subscription] = {}
    for s in subs:
        cur = best.get(s.org_id)
        if cur is None:
            best[s.org_id] = s
        elif cur.status == "cancelled" and s.status in _ACTIVE_SUB:
            best[s.org_id] = s
    return best


def list_organizations(db, q=None, status=None, plan=None, page=1, page_size=20):
    stmt = select(Organization)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Organization.name).like(like),
                              func.lower(Organization.domain).like(like)))
    if status:
        stmt = stmt.where(Organization.status == status)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    orgs = db.scalars(
        stmt.order_by(Organization.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    ids = [o.id for o in orgs]
    users, events, subs = _user_counts(db, ids), _event_counts(db, ids), _current_subs(db, ids)

    items = [_org_out(o, users, events, subs) for o in orgs]
    # Plan filter applies post-join (plan lives on the subscription, not the org row).
    if plan:
        items = [o for o in items if o.plan == plan]
    return items, total


def _org_out(o, users, events, subs) -> OrgOut:
    sub = subs.get(o.id)
    return OrgOut(
        id=o.id, name=o.name, domain=o.domain, status=o.status, region=o.region,
        storage_used_gb=o.storage_used_gb, bandwidth_gb=o.bandwidth_gb, created_at=o.created_at,
        users_count=users.get(o.id, 0), events_count=events.get(o.id, 0),
        plan=sub.plan.name if sub else None,
        subscription_status=sub.status if sub else None,
    )


def get_organization(db, org_id) -> OrgOut | None:
    o = db.get(Organization, org_id)
    if not o:
        return None
    ids = [o.id]
    return _org_out(o, _user_counts(db, ids), _event_counts(db, ids), _current_subs(db, ids))


def create_organization(db, data) -> Organization:
    org = Organization(
        name=data.name, domain=data.domain, region=data.region or "US East",
        status=data.status or "active",
    )
    db.add(org)
    db.flush()
    if data.plan_slug:
        plan = get_plan_by_slug(db, data.plan_slug)
        if plan:
            db.add(Subscription(org_id=org.id, plan_id=plan.id, status="trial"))
    db.commit()
    db.refresh(org)
    return org


def update_organization(db, org: Organization, data) -> Organization:
    for field in ("name", "domain", "region", "status", "storage_used_gb", "bandwidth_gb"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(org, field, val)
    if data.plan_slug:
        plan = get_plan_by_slug(db, data.plan_slug)
        if plan:
            db.add(Subscription(org_id=org.id, plan_id=plan.id, status="active"))
    db.commit()
    db.refresh(org)
    return org


def delete_organization(db, org: Organization) -> None:
    db.query(Subscription).filter(Subscription.org_id == org.id).delete()
    # `memberships` predates the current org_id-on-User model and isn't SQLAlchemy-mapped,
    # but it still FK-references organizations — clean it up raw or the delete 500s.
    db.execute(text("DELETE FROM memberships WHERE org_id = :oid"), {"oid": org.id})
    db.delete(org)
    db.commit()


# ── Users ──────────────────────────────────────────────────────────────────

def list_users(db, q=None, role=None, org_id=None, is_active=None, page=1, page_size=20):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.full_name).like(like),
                              func.lower(User.email).like(like),
                              func.lower(User.username).like(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if org_id:
        stmt = stmt.where(User.org_id == org_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    users = db.scalars(
        stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_user_out(u) for u in users], total


def user_stats(db) -> dict:
    """Dataset-wide counts for the Users console's KPI row — one query instead of the four
    page_size=1 round trips the page used to make per load."""
    row = db.execute(
        select(
            func.count(),
            func.count().filter(User.is_active.is_(True)),
            func.count().filter(User.is_active.is_(False)),
            func.count().filter(User.role == "super_admin"),
        )
    ).one()
    total, active, inactive, super_admins = row
    return {"total": total, "active": active, "inactive": inactive, "super_admins": super_admins}


def _user_out(u: User) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = u.organization.name if u.organization else None
    return out


def update_user(db, user: User, data) -> AdminUserOut:
    for field in ("role", "is_active", "full_name"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(user, field, val)
    # "" clears it back to unscoped full-access super_admin (see schemas.admin.UserUpdate);
    # None means "not included in this PATCH", matching every other field's convention here.
    staff_role = getattr(data, "staff_commercial_role", None)
    if staff_role is not None:
        target_role = data.role if data.role is not None else user.role
        if staff_role == "":
            user.staff_commercial_role = None
        elif target_role != "super_admin":
            raise ValueError("staff_commercial_role only applies to super_admin users")
        elif staff_role not in STAFF_COMMERCIAL_ROLES:
            raise ValueError(f"staff_commercial_role must be one of {STAFF_COMMERCIAL_ROLES}")
        else:
            user.staff_commercial_role = staff_role
    db.commit()
    db.refresh(user)
    return _user_out(user)


def delete_user(db, user: User) -> None:
    # `memberships` predates the current org_id-on-User model and isn't SQLAlchemy-mapped,
    # but it still FK-references users — clean it up raw or the delete 500s.
    db.execute(text("DELETE FROM memberships WHERE user_id = :uid"), {"uid": user.id})
    db.delete(user)
    db.commit()


# ── Plans & Subscriptions ────────────────────────────────────────────────────

def get_plan_by_slug(db, slug) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.slug == slug))


def list_plans(db) -> list[PlanOut]:
    plans = db.scalars(select(Plan).order_by(Plan.price_monthly)).all()
    return [PlanOut.model_validate(p) for p in plans]


def list_subscriptions(db, status=None, page=1, page_size=20):
    stmt = select(Subscription)
    if status:
        stmt = stmt.where(Subscription.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    subs = db.scalars(
        stmt.order_by(Subscription.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_sub_out(s) for s in subs], total


def _sub_out(s: Subscription) -> SubscriptionOut:
    return SubscriptionOut(
        id=s.id, org_id=s.org_id,
        organization_name=s.organization.name if s.organization else None,
        plan=s.plan.name if s.plan else None,
        price_monthly=float(s.plan.price_monthly) if s.plan else None,
        status=s.status, seats=s.seats, started_at=s.started_at,
        current_period_end=s.current_period_end, trial_ends_at=s.trial_ends_at,
    )


def get_subscription(db, sub_id) -> Subscription | None:
    return db.get(Subscription, sub_id)


def update_subscription(db, sub: Subscription, data) -> SubscriptionOut:
    if data.status is not None:
        sub.status = data.status
    if data.seats is not None:
        sub.seats = data.seats
    if data.current_period_end is not None:
        sub.current_period_end = data.current_period_end
    if data.plan_slug:
        plan = get_plan_by_slug(db, data.plan_slug)
        if plan:
            sub.plan_id = plan.id
    db.commit()
    db.refresh(sub)
    return _sub_out(sub)


# ── Audit logs ───────────────────────────────────────────────────────────────

def create_audit_log(db, *, actor, action, target_type=None, target_id=None,
                     org_id=None, meta=None, ip=None) -> None:
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        org_id=org_id, meta=meta, ip=ip,
    ))
    db.commit()


def list_audit_logs(db, action=None, target_type=None, page=1, page_size=50):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    logs = db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [AuditLogOut.model_validate(x) for x in logs], total


# ── Feature flags ────────────────────────────────────────────────────────────

def list_feature_flags(db) -> list[FeatureFlagOut]:
    flags = db.scalars(select(FeatureFlag).order_by(FeatureFlag.key)).all()
    return [FeatureFlagOut.model_validate(f) for f in flags]


def get_feature_flag(db, flag_id) -> FeatureFlag | None:
    return db.get(FeatureFlag, flag_id)


def get_feature_flag_by_key(db, key) -> FeatureFlag | None:
    return db.scalar(select(FeatureFlag).where(FeatureFlag.key == key))


def create_feature_flag(db, data, updated_by: str) -> FeatureFlag:
    flag = FeatureFlag(key=data.key, name=data.name, description=data.description,
                       enabled=data.enabled, updated_by=updated_by)
    db.add(flag)
    db.commit()
    db.refresh(flag)
    return flag


def update_feature_flag(db, flag: FeatureFlag, data, updated_by: str) -> FeatureFlag:
    for field in ("name", "description", "enabled"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(flag, field, val)
    flag.updated_by = updated_by
    db.commit()
    db.refresh(flag)
    return flag


def delete_feature_flag(db, flag: FeatureFlag) -> None:
    db.delete(flag)
    db.commit()


# ── Release center ───────────────────────────────────────────────────────────

def list_releases(db, page=1, page_size=50):
    stmt = select(Release)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    releases = db.scalars(
        stmt.order_by(Release.released_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [ReleaseOut.model_validate(r) for r in releases], total


def get_release(db, release_id) -> Release | None:
    return db.get(Release, release_id)


def create_release(db, data, released_by: str) -> Release:
    release = Release(version=data.version, title=data.title, notes=data.notes,
                      channel=data.channel or "production", released_by=released_by)
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


def delete_release(db, release: Release) -> None:
    db.delete(release)
    db.commit()


# ── Support tickets ──────────────────────────────────────────────────────────

def _ticket_out(t: SupportTicket) -> SupportTicketOut:
    out = SupportTicketOut.model_validate(t)
    out.organization_name = t.organization.name if t.organization else None
    return out


def list_support_tickets(db, status=None, org_id=None, page=1, page_size=50):
    stmt = select(SupportTicket)
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if org_id:
        stmt = stmt.where(SupportTicket.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    tickets = db.scalars(
        stmt.order_by(SupportTicket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_ticket_out(t) for t in tickets], total


def get_support_ticket(db, ticket_id) -> SupportTicket | None:
    return db.get(SupportTicket, ticket_id)


def create_support_ticket(db, data) -> SupportTicket:
    ticket = SupportTicket(
        org_id=data.org_id, subject=data.subject, message=data.message,
        priority=data.priority or "normal", requester_email=data.requester_email,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return _ticket_out(ticket)


def update_support_ticket(db, ticket: SupportTicket, data) -> SupportTicket:
    if data.status is not None:
        ticket.status = data.status
        if data.status in ("resolved", "closed") and ticket.resolved_at is None:
            ticket.resolved_at = datetime.now(timezone.utc)
        elif data.status in ("open", "in_progress"):
            ticket.resolved_at = None
    if data.priority is not None:
        ticket.priority = data.priority
    db.commit()
    db.refresh(ticket)
    return _ticket_out(ticket)


def delete_support_ticket(db, ticket: SupportTicket) -> None:
    db.delete(ticket)
    db.commit()


# ── Incidents (Trust & Safety console) ───────────────────────────────────────

def _incident_out(i: Incident) -> IncidentOut:
    out = IncidentOut.model_validate(i)
    out.organization_name = i.organization.name if i.organization else None
    return out


def _incident_ref(now: datetime) -> str:
    # INC-20260814-0630-a1b2 — date+time makes it human-scannable, the hex tail keeps
    # concurrent opens from colliding on the same second.
    return f"INC-{now:%Y%m%d}-{now:%H%M}-{secrets.token_hex(2)}"


def list_incidents(db, status=None, kind=None, org_id=None, page=1, page_size=50):
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
    if kind:
        stmt = stmt.where(Incident.kind == kind)
    if org_id:
        stmt = stmt.where(Incident.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(Incident.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_incident_out(i) for i in rows], total


def get_incident(db, incident_id) -> Incident | None:
    return db.get(Incident, incident_id)


def create_incident(db, data, commander_default: str | None) -> Incident:
    now = datetime.now(timezone.utc)
    incident = Incident(
        ref=_incident_ref(now), title=data.title, detail=data.detail,
        severity=data.severity, kind=data.kind, org_id=data.org_id,
        commander=data.commander or commander_default, started_at=now,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return _incident_out(incident)


def update_incident(db, incident: Incident, data) -> Incident:
    if data.status is not None:
        incident.status = data.status
        if data.status == "resolved" and incident.resolved_at is None:
            incident.resolved_at = datetime.now(timezone.utc)
        elif data.status != "resolved":
            incident.resolved_at = None
    if data.severity is not None:
        incident.severity = data.severity
    if data.commander is not None:
        incident.commander = data.commander
    db.commit()
    db.refresh(incident)
    return _incident_out(incident)


# ── Governance records ────────────────────────────────────────────────────────

def _governance_record_out(r: GovernanceRecord) -> GovernanceRecordOut:
    out = GovernanceRecordOut.model_validate(r)
    out.organization_name = r.organization.name if r.organization else None
    return out


def list_governance_records(db, kind=None, status=None, org_id=None, page=1, page_size=50):
    stmt = select(GovernanceRecord)
    if kind:
        stmt = stmt.where(GovernanceRecord.kind == kind)
    if status:
        stmt = stmt.where(GovernanceRecord.status == status)
    if org_id:
        stmt = stmt.where(GovernanceRecord.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(GovernanceRecord.opened_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_governance_record_out(r) for r in rows], total


def get_governance_record(db, record_id) -> GovernanceRecord | None:
    return db.get(GovernanceRecord, record_id)


def create_governance_record(db, data) -> GovernanceRecord:
    record = GovernanceRecord(
        kind=data.kind, org_id=data.org_id, event_id=data.event_id,
        detail=data.detail, due_at=data.due_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _governance_record_out(record)


def update_governance_record(db, record: GovernanceRecord, data) -> GovernanceRecord:
    if data.status is not None:
        record.status = data.status
        if data.status == "resolved" and record.resolved_at is None:
            record.resolved_at = datetime.now(timezone.utc)
        elif data.status != "resolved":
            record.resolved_at = None
    if data.detail is not None:
        record.detail = data.detail
    if data.due_at is not None:
        record.due_at = data.due_at
    db.commit()
    db.refresh(record)
    return _governance_record_out(record)


# ── Developer / API keys (stored on Organization.api_keys JSON) ─────────────

def list_api_keys(db, org: Organization) -> list[ApiKeyOut]:
    records = org.api_keys or []
    return [ApiKeyOut(id=r["id"], label=r["label"], prefix=r["prefix"],
                      created_at=r["created_at"], expires_at=r.get("expires_at"),
                      revoked=r.get("revoked", False))
            for r in records]


def create_api_key(db, org: Organization, label: str, expires_in_days: int | None = None) -> ApiKeyCreated:
    now = datetime.now(timezone.utc)
    raw = f"zk_live_{secrets.token_urlsafe(32)}"
    record = {
        "id": str(uuid.uuid4()),
        "label": label,
        "prefix": raw[:12],
        "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "created_at": now.isoformat(),
        # An expiry makes credential rotation a real, surfaceable obligation (the org
        # console's "expires in N days" item reads this). None = non-expiring, which is
        # what every key created before this field existed remains.
        "expires_at": (now + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None,
        "revoked": False,
    }
    org.api_keys = [*(org.api_keys or []), record]
    db.commit()
    return ApiKeyCreated(id=record["id"], label=label, prefix=record["prefix"],
                         created_at=record["created_at"], revoked=False, key=raw,
                         expires_at=record["expires_at"])


def revoke_api_key(db, org: Organization, key_id: str) -> bool:
    records = org.api_keys or []
    found = False
    updated = []
    for r in records:
        if r["id"] == key_id:
            found = True
            r = {**r, "revoked": True}
        updated.append(r)
    if not found:
        return False
    org.api_keys = updated
    db.commit()
    return True
