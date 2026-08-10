"""Super Admin platform API. Every route is gated by require_super_admin (403 otherwise).
Thin controllers: DB access -> crud.admin, aggregation -> services.admin, and every
mutation writes an audit log."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crud import admin as crud
from ..crud import event as event_crud
from ..db import get_db
from ..models import ElevationSession, Event, Organization, PlatformSetting, User
from ..schemas.admin import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    ElevationRequest,
    FeatureFlagCreate,
    FeatureFlagOut,
    FeatureFlagUpdate,
    OrgCreate,
    OrgOut,
    OrgUpdate,
    Page,
    ReleaseCreate,
    ReleaseOut,
    SettingsUpdate,
    SubscriptionUpdate,
    SupportTicketCreate,
    SupportTicketOut,
    SupportTicketUpdate,
    UserUpdate,
)
from .. import security
from ..security import require_super_admin
from ..services import admin as svc
from ..services import broadcast as broadcast_svc
from ..services import moderation as mod
from ..services import ops as ops_svc

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


def _audit(db, admin, request, action, **kw):
    crud.create_audit_log(db, actor=admin, action=action, ip=security.client_ip(request), **kw)


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return svc.dashboard_summary(db)


# ── Command Center ───────────────────────────────────────────────────────────

@router.get("/command-center")
def command_center(
    range_: str = Query("live", alias="range", pattern="^(live|1h|24h|7d|custom)$"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    region: str | None = Query(None, pattern="^(na|eu|apac|sa)$"),
    scope: str = Query("core_live", pattern="^(core_live|core|live)$"),
    include_test: bool = Query(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_super_admin),
):
    """Whole-page payload for /admin/dashboard. One call because every region reports on the
    same window and the same instant.

    range=custom requires `from`; `to` defaults to now. Validated here rather than in the
    service so a bad window is a 400 the console can explain, not a silently empty page."""
    if range_ == "custom":
        if from_ is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "range=custom requires a 'from' timestamp")
        until = to or datetime.now(timezone.utc)
        if from_.tzinfo is None:
            from_ = from_.replace(tzinfo=timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if from_ >= until:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "'from' must be before 'to'")
        return ops_svc.command_center(db, admin, range_=range_, since=from_, until=until,
                                      region=region, scope=scope, include_test=include_test)
    return ops_svc.command_center(db, admin, range_=range_, region=region,
                                  scope=scope, include_test=include_test)


@router.get("/console-state")
def console_state(db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    """Small payload the admin shell polls: overall health, sidebar badge counts, and the
    caller's elevation session."""
    return ops_svc.console_state(db, admin)


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=120),
    db: Session = Depends(get_db),
):
    """Global entity search for the console command bar."""
    return ops_svc.search(db, q)


# ── Elevation (step-up privilege) ────────────────────────────────────────────

@router.post("/elevation", status_code=status.HTTP_201_CREATED)
def start_elevation(data: ElevationRequest, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    """Open a scoped, expiring elevation. An existing active one is returned unchanged so a
    double-click can't extend a grant."""
    existing = ops_svc.current_elevation(db, admin)
    if existing:
        return existing
    row = ElevationSession(
        user_id=admin.id, scope=data.scope, scopes=data.scopes, reason=data.reason,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=data.minutes),
    )
    db.add(row)
    db.commit()
    _audit(db, admin, request, "elevation.start", target_type="elevation", target_id=row.id,
           meta={"scope": data.scope, "minutes": data.minutes, "reason": data.reason})
    return ops_svc.current_elevation(db, admin)


@router.delete("/elevation", status_code=status.HTTP_204_NO_CONTENT)
def end_elevation(request: Request, db: Session = Depends(get_db),
                  admin: User = Depends(require_super_admin)):
    """End the caller's elevation early ("End now" in the console footer)."""
    row = db.scalar(
        select(ElevationSession).where(
            ElevationSession.user_id == admin.id,
            ElevationSession.ended_at.is_(None),
        ).order_by(ElevationSession.granted_at.desc())
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active elevation")
    row.ended_at = datetime.now(timezone.utc)
    db.commit()
    _audit(db, admin, request, "elevation.end", target_type="elevation", target_id=row.id,
           meta={"scope": row.scope})


# ── Organizations ────────────────────────────────────────────────────────────

@router.get("/organizations", response_model=Page)
def list_organizations(
    q: str | None = None,
    status_: str | None = Query(None, alias="status"),
    plan: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = crud.list_organizations(db, q=q, status=status_, plan=plan, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/organizations/{org_id}", response_model=OrgOut)
def get_organization(org_id: uuid.UUID, db: Session = Depends(get_db)):
    org = crud.get_organization(db, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


@router.post("/organizations", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_organization(data: OrgCreate, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = crud.create_organization(db, data)
    _audit(db, admin, request, "organization.create", target_type="organization",
           target_id=org.id, org_id=org.id, meta={"name": org.name})
    return crud.get_organization(db, org.id)


@router.patch("/organizations/{org_id}", response_model=OrgOut)
def update_organization(org_id: uuid.UUID, data: OrgUpdate, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    crud.update_organization(db, org, data)
    _audit(db, admin, request, "organization.update", target_type="organization",
           target_id=org.id, org_id=org.id, meta=data.model_dump(exclude_none=True))
    return crud.get_organization(db, org.id)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(org_id: uuid.UUID, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if org.id == admin.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot delete your own organization")
    # Block orphaning users — the admin must reassign/remove members first.
    user_count = db.scalar(select(func.count(User.id)).where(User.org_id == org.id)) or 0
    if user_count:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Organization has {user_count} user(s); remove them before deleting")
    name = org.name
    crud.delete_organization(db, org)
    _audit(db, admin, request, "organization.delete", target_type="organization",
           target_id=org_id, meta={"name": name})


# ── Users ──────────────────────────────────────────────────────────────────

@router.get("/users", response_model=Page)
def list_users(
    q: str | None = None,
    role: str | None = None,
    org_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = crud.list_users(db, q=q, role=role, org_id=org_id, is_active=is_active,
                                   page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, data: UserUpdate, request: Request,
               db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    # Guard against self-lockout: can't deactivate or demote your own account.
    if user.id == admin.id and (data.is_active is False or (data.role and data.role != "super_admin")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or demote yourself")
    out = crud.update_user(db, user, data)
    _audit(db, admin, request, "user.update", target_type="user", target_id=user.id,
           org_id=user.org_id, meta=data.model_dump(exclude_none=True))
    return out


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: uuid.UUID, request: Request,
               db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    email, org_id = user.email, user.org_id
    crud.delete_user(db, user)
    _audit(db, admin, request, "user.delete", target_type="user", target_id=user_id,
           org_id=org_id, meta={"email": email})


# ── Plans & Subscriptions ────────────────────────────────────────────────────

@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    return crud.list_plans(db)


@router.get("/subscriptions", response_model=Page)
def list_subscriptions(
    status_: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    items, total = crud.list_subscriptions(db, status=status_, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.patch("/subscriptions/{sub_id}")
def update_subscription(sub_id: uuid.UUID, data: SubscriptionUpdate, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    sub = crud.get_subscription(db, sub_id)
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    out = crud.update_subscription(db, sub, data)
    _audit(db, admin, request, "subscription.update", target_type="subscription",
           target_id=sub.id, org_id=sub.org_id, meta=data.model_dump(exclude_none=True))
    return out


# ── Analytics / Live monitoring / Health ─────────────────────────────────────

@router.get("/analytics")
def analytics(db: Session = Depends(get_db)):
    return svc.analytics(db)


@router.get("/live-events")
def live_events(state: str = Query("live", pattern="^(live|recent)$"), db: Session = Depends(get_db)):
    return svc.live_events(db, state=state)


@router.get("/platform-health")
def platform_health(db: Session = Depends(get_db)):
    return svc.platform_health(db)


@router.get("/events/{event_id}")
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)):
    """Cross-org event detail. /events/{id} (routers/events.py) scopes to the caller's own
    org_id, which for a super admin is the platform org — it 404s on every other org's
    event. This is the super-admin-safe read, used by the Live Operations detail view."""
    detail = svc.event_detail(db, event_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return detail


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Delete any event, any org. If it's currently live/paused, force-ends the broadcast
    first (stops the recording, closes the LiveKit room) so nothing is orphaned — a soft
    delete alone would leave an active broadcast_sessions row with no way to reach it."""
    ev = db.get(Event, event_id)
    if ev is None or ev.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    was_live = ev.status in ("live", "paused")
    if was_live:
        ctx = mod.Ctx(
            event_id=ev.id, org_id=ev.org_id, room=f"event_{ev.id}",
            user_id=admin.id, name=admin.full_name or admin.email,
            identity=f"admin-{admin.id}", role=admin.role,
            can_moderate=True, can_host=True,
        )
        await broadcast_svc._end(ctx, {}, emergency=True)
        db.refresh(ev)

    event_crud.soft_delete_event(db, ev)
    _audit(db, admin, request, "event.delete", target_type="event", target_id=ev.id,
           org_id=ev.org_id, meta={"title": ev.title, "force_ended": was_live})


# ── Audit logs ───────────────────────────────────────────────────────────────

@router.get("/audit-logs", response_model=Page)
def audit_logs(
    action: str | None = None,
    target_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = crud.list_audit_logs(db, action=action, target_type=target_type,
                                        page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


# ── Platform settings ────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.scalars(select(PlatformSetting)).all()
    return {r.key: {"value": r.value, "category": r.category, "updated_at": r.updated_at} for r in rows}


@router.patch("/settings")
def update_settings(data: SettingsUpdate, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    for key, value in data.values.items():
        row = db.get(PlatformSetting, key)
        if row:
            row.value = value
            row.updated_by = admin.email
        else:
            db.add(PlatformSetting(key=key, value=value, updated_by=admin.email))
    db.commit()
    _audit(db, admin, request, "settings.update", target_type="setting",
           meta={"keys": list(data.values.keys())})
    rows = db.scalars(select(PlatformSetting)).all()
    return {r.key: {"value": r.value, "category": r.category, "updated_at": r.updated_at} for r in rows}


# ── Roles & permissions (read-only reference) ────────────────────────────────

@router.get("/roles")
def roles(db: Session = Depends(get_db)):
    return svc.roles()


# ── Feature flags ────────────────────────────────────────────────────────────

@router.get("/feature-flags", response_model=list[FeatureFlagOut])
def list_feature_flags(db: Session = Depends(get_db)):
    return crud.list_feature_flags(db)


@router.post("/feature-flags", response_model=FeatureFlagOut, status_code=status.HTTP_201_CREATED)
def create_feature_flag(data: FeatureFlagCreate, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    if crud.get_feature_flag_by_key(db, data.key):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Flag '{data.key}' already exists")
    flag = crud.create_feature_flag(db, data, updated_by=admin.email)
    _audit(db, admin, request, "feature_flag.create", target_type="feature_flag",
           target_id=flag.id, meta={"key": flag.key, "enabled": flag.enabled})
    return flag


@router.patch("/feature-flags/{flag_id}", response_model=FeatureFlagOut)
def update_feature_flag(flag_id: uuid.UUID, data: FeatureFlagUpdate, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    flag = crud.get_feature_flag(db, flag_id)
    if not flag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature flag not found")
    flag = crud.update_feature_flag(db, flag, data, updated_by=admin.email)
    _audit(db, admin, request, "feature_flag.update", target_type="feature_flag",
           target_id=flag.id, meta=data.model_dump(exclude_none=True))
    return flag


@router.delete("/feature-flags/{flag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feature_flag(flag_id: uuid.UUID, request: Request,
                        db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    flag = crud.get_feature_flag(db, flag_id)
    if not flag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature flag not found")
    key = flag.key
    crud.delete_feature_flag(db, flag)
    _audit(db, admin, request, "feature_flag.delete", target_type="feature_flag",
           target_id=flag_id, meta={"key": key})


# ── Release center ───────────────────────────────────────────────────────────

@router.get("/releases", response_model=Page)
def list_releases(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = crud.list_releases(db, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/releases", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
def create_release(data: ReleaseCreate, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    release = crud.create_release(db, data, released_by=admin.email)
    _audit(db, admin, request, "release.create", target_type="release",
           target_id=release.id, meta={"version": release.version, "title": release.title})
    return release


@router.delete("/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_release(release_id: uuid.UUID, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    release = crud.get_release(db, release_id)
    if not release:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Release not found")
    version = release.version
    crud.delete_release(db, release)
    _audit(db, admin, request, "release.delete", target_type="release",
           target_id=release_id, meta={"version": version})


# ── Support tickets ──────────────────────────────────────────────────────────

@router.get("/support-tickets", response_model=Page)
def list_support_tickets(
    status_: str | None = Query(None, alias="status"),
    org_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = crud.list_support_tickets(db, status=status_, org_id=org_id, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/support-tickets", response_model=SupportTicketOut, status_code=status.HTTP_201_CREATED)
def create_support_ticket(data: SupportTicketCreate, request: Request,
                          db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, data.org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    ticket = crud.create_support_ticket(db, data)
    _audit(db, admin, request, "support_ticket.create", target_type="support_ticket",
           target_id=ticket.id, org_id=data.org_id, meta={"subject": data.subject})
    return ticket


@router.patch("/support-tickets/{ticket_id}", response_model=SupportTicketOut)
def update_support_ticket(ticket_id: uuid.UUID, data: SupportTicketUpdate, request: Request,
                          db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    org_id = ticket.org_id
    out = crud.update_support_ticket(db, ticket, data)
    _audit(db, admin, request, "support_ticket.update", target_type="support_ticket",
           target_id=ticket_id, org_id=org_id, meta=data.model_dump(exclude_none=True))
    return out


@router.delete("/support-tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_support_ticket(ticket_id: uuid.UUID, request: Request,
                          db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    org_id = ticket.org_id
    crud.delete_support_ticket(db, ticket)
    _audit(db, admin, request, "support_ticket.delete", target_type="support_ticket",
           target_id=ticket_id, org_id=org_id)


# ── Developer / API keys ─────────────────────────────────────────────────────

@router.get("/organizations/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(org_id: uuid.UUID, db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return crud.list_api_keys(db, org)


@router.post("/organizations/{org_id}/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(org_id: uuid.UUID, data: ApiKeyCreate, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    created = crud.create_api_key(db, org, data.label, expires_in_days=data.expires_in_days)
    _audit(db, admin, request, "api_key.create", target_type="api_key",
           target_id=created.id, org_id=org_id,
           meta={"label": data.label, "prefix": created.prefix,
                 "expires_in_days": data.expires_in_days})
    return created


@router.delete("/organizations/{org_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(org_id: uuid.UUID, key_id: str, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if not crud.revoke_api_key(db, org, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    _audit(db, admin, request, "api_key.revoke", target_type="api_key", target_id=key_id, org_id=org_id)
