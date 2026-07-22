"""Super Admin platform API. Every route is gated by require_super_admin (403 otherwise).
Thin controllers: DB access -> crud.admin, aggregation -> services.admin, and every
mutation writes an audit log."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crud import admin as crud
from ..db import get_db
from ..models import Organization, PlatformSetting, Subscription, User
from ..schemas.admin import (
    OrgCreate,
    OrgOut,
    OrgUpdate,
    Page,
    SettingsUpdate,
    SubscriptionUpdate,
    UserUpdate,
)
from ..security import require_super_admin
from ..services import admin as svc

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit(db, admin, request, action, **kw):
    crud.create_audit_log(db, actor=admin, action=action, ip=_ip(request), **kw)


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return svc.dashboard_summary(db)


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
def live_events(db: Session = Depends(get_db)):
    return svc.live_events(db)


@router.get("/platform-health")
def platform_health(db: Session = Depends(get_db)):
    return svc.platform_health(db)


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
