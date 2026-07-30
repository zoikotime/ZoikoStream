"""Super-admin (platform) console: manage organizations, users, and events across the
whole platform, plus platform-wide analytics. Every endpoint here is super_admin-only.

Every mutation writes an AuditLog row (see services/audit.log_action) right before its
own db.commit() -- GET /admin/audit-logs is the un-fabricated history of everything
done through this console."""
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.db import get_db
from app.models import Channel, Membership, Organization, User
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.chat import ChatMessage
from app.models.feature_flag import FeatureFlag
from app.models.plan import Plan
from app.models.platform_setting import PlatformSetting
from app.models.poll import Poll, PollOption, PollVote
from app.models.qa import QaQuestion, QaVote
from app.models.recording import Recording
from app.models.registration import Registration
from app.models.release import Release
from app.models.stream import Stream
from app.models.subscription import Subscription
from app.models.support_ticket import SupportTicket
from app.models.view import StreamView
from app.schemas.admin import (
    AdminEventOut,
    AdminUserOut,
    ApiKeyCreateIn,
    ApiKeyCreateOut,
    ApiKeyOut,
    AuditLogOut,
    FeatureFlagCreateIn,
    FeatureFlagOut,
    FeatureFlagUpdateIn,
    LiveEventOut,
    OrgAdminOut,
    OrgCreateIn,
    OrgDetailOut,
    OrgOut,
    OrgUpdateIn,
    PlanOut,
    PlatformSettingsUpdateIn,
    ReleaseCreateIn,
    ReleaseOut,
    RoleOut,
    SubscriptionOut,
    SubscriptionUpdateIn,
    SupportTicketCreateIn,
    SupportTicketOut,
    SupportTicketUpdateIn,
    UserUpdateIn,
)
from app.security import get_current_user, hash_password
from app.services import presence
from app.services.analytics import PLATFORM_ORG_NAME
from app.services.audit import log_action

router = APIRouter(prefix="/admin", tags=["Admin"])

MONTHS_OF_HISTORY = 6


def _require_super_admin(user: User) -> None:
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can access this")


def _org_counts(db: Session, org_id) -> tuple[int, int]:
    users = db.scalar(select(func.count(User.id)).where(User.org_id == org_id)) or 0
    events = db.scalar(select(func.count(Stream.id)).where(Stream.org_id == org_id)) or 0
    return users, events


def _org_out(db: Session, org: Organization) -> OrgOut:
    users, events = _org_counts(db, org.id)
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
    plan = db.get(Plan, sub.plan_id) if sub and sub.plan_id else None
    return OrgOut(
        id=org.id,
        name=org.name,
        domain=org.domain,
        region=org.region,
        status=org.status,
        plan=plan.name if plan else None,
        subscription_status=sub.status if sub else None,
        users_count=users,
        events_count=events,
        bandwidth_gb=org.bandwidth_gb,
        storage_used_gb=org.storage_used_gb,
        created_at=org.created_at,
    )


def _get_org(db: Session, org_id: str) -> Organization:
    org = db.get(Organization, org_id)
    if not org or org.name == PLATFORM_ORG_NAME:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def _apply_plan_switch(db: Session, org: Organization, plan_slug: str) -> None:
    """Look up plan_slug and either create the org's first Subscription or switch its
    plan. plan_slug=None means "no change" -- callers only invoke this when a
    non-null slug was actually sent (see OrgModal's "keep current plan" default)."""
    plan = db.scalar(select(Plan).where(Plan.slug == plan_slug))
    if not plan:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan '{plan_slug}'")
    sub = db.scalar(select(Subscription).where(Subscription.org_id == org.id))
    if sub:
        sub.plan_id = plan.id
    else:
        trial_end = datetime.now(timezone.utc) + timedelta(days=14)
        db.add(
            Subscription(
                org_id=org.id,
                plan_id=plan.id,
                status="trial",
                seats=1,
                current_period_end=trial_end,
                trial_ends_at=trial_end,
            )
        )


def _monthly_series(db: Session, model, date_col, extra_where=None, months=MONTHS_OF_HISTORY) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(days=months * 31)
    stmt = select(func.date_trunc("month", date_col).label("bucket"), func.count(model.id)).where(date_col >= start)
    if extra_where is not None:
        stmt = stmt.where(extra_where)
    rows = db.execute(stmt.group_by("bucket").order_by("bucket")).all()
    return [{"label": bucket.strftime("%b %Y"), "value": count} for bucket, count in rows]


def _revenue_series(db: Session, months=MONTHS_OF_HISTORY) -> list[dict]:
    start = datetime.now(timezone.utc) - timedelta(days=months * 31)
    rows = db.execute(
        select(func.date_trunc("month", Subscription.started_at).label("bucket"), func.sum(Plan.price_monthly))
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.started_at >= start)
        .group_by("bucket")
        .order_by("bucket")
    ).all()
    return [{"label": bucket.strftime("%b %Y"), "value": round(float(total or 0), 2)} for bucket, total in rows]


def _current_mrr(db: Session) -> float:
    total = db.scalar(
        select(func.coalesce(func.sum(Plan.price_monthly), 0))
        .select_from(Subscription)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.status.in_(("active", "trial")))
    )
    return round(float(total or 0), 2)


# ── Organizations ─────────────────────────────────────────────────────────────────
@router.get("/organizations")
def list_organizations(
    q: str = "",
    status_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    stmt = select(Organization).where(Organization.name != PLATFORM_ORG_NAME)
    if q.strip():
        stmt = stmt.where(Organization.name.ilike(f"%{q.strip()}%"))
    if status_filter:
        stmt = stmt.where(Organization.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    orgs = db.scalars(stmt.order_by(Organization.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_org_out(db, o) for o in orgs], "total": total}


@router.post("/organizations", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
def create_organization(
    data: OrgCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    org = Organization(name=data.name, domain=data.domain, region=data.region, status=data.status, is_active=data.status != "suspended")
    db.add(org)
    db.flush()

    if data.plan_slug:
        _apply_plan_switch(db, org, data.plan_slug)

    log_action(db, user, "organization.create", target_type="organization", target_id=org.id, org_id=org.id, meta={"name": org.name}, request=request)
    db.commit()
    db.refresh(org)
    return _org_out(db, org)


@router.get("/organizations/{org_id}", response_model=OrgDetailOut)
def get_organization(org_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    org = _get_org(db, org_id)
    admins = db.scalars(
        select(User).where(User.org_id == org.id, User.role == "org_admin").order_by(User.created_at)
    ).all()
    out = _org_out(db, org)
    return OrgDetailOut(
        **out.model_dump(),
        admins=[OrgAdminOut(id=a.id, full_name=a.full_name, email=a.email, role=a.role, is_active=a.is_active) for a in admins],
    )


@router.patch("/organizations/{org_id}", response_model=OrgOut)
def update_organization(
    org_id: str,
    data: OrgUpdateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    org = _get_org(db, org_id)
    if data.name is not None:
        org.name = data.name
    if data.domain is not None:
        org.domain = data.domain
    if data.region is not None:
        org.region = data.region
    if data.status is not None:
        org.status = data.status
        org.is_active = data.status != "suspended"
    if data.plan_slug:
        _apply_plan_switch(db, org, data.plan_slug)

    log_action(db, user, "organization.update", target_type="organization", target_id=org.id, org_id=org.id, meta={"name": org.name}, request=request)
    db.commit()
    db.refresh(org)
    return _org_out(db, org)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(org_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    org = _get_org(db, org_id)
    if org.id == user.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't delete your own organization")

    # Cascade-delete everything hanging off this org's streams and users. Bulk .delete()
    # queries don't trigger ORM relationship cascades, so each dependent table is cleared
    # explicitly, in FK-safe order (children before parents).
    # ponytail: a user who belongs to more than one org (via Membership) still loses their
    # whole account if their *current* org_id is the one being deleted -- multi-org cleanup
    # on org deletion isn't handled yet.
    db.query(SupportTicket).filter(SupportTicket.org_id == org.id).delete(synchronize_session=False)
    db.query(ApiKey).filter(ApiKey.org_id == org.id).delete(synchronize_session=False)
    db.query(Subscription).filter(Subscription.org_id == org.id).delete(synchronize_session=False)

    stream_ids = list(db.scalars(select(Stream.id).where(Stream.org_id == org.id)))
    if stream_ids:
        poll_ids = list(db.scalars(select(Poll.id).where(Poll.stream_id.in_(stream_ids))))
        if poll_ids:
            db.query(PollVote).filter(PollVote.poll_id.in_(poll_ids)).delete(synchronize_session=False)
            db.query(PollOption).filter(PollOption.poll_id.in_(poll_ids)).delete(synchronize_session=False)
            db.query(Poll).filter(Poll.id.in_(poll_ids)).delete(synchronize_session=False)

        question_ids = list(db.scalars(select(QaQuestion.id).where(QaQuestion.stream_id.in_(stream_ids))))
        if question_ids:
            db.query(QaVote).filter(QaVote.question_id.in_(question_ids)).delete(synchronize_session=False)
            db.query(QaQuestion).filter(QaQuestion.id.in_(question_ids)).delete(synchronize_session=False)

        db.query(ChatMessage).filter(ChatMessage.stream_id.in_(stream_ids)).delete(synchronize_session=False)
        db.query(Recording).filter(Recording.stream_id.in_(stream_ids)).delete(synchronize_session=False)
        db.query(Registration).filter(Registration.stream_id.in_(stream_ids)).delete(synchronize_session=False)
        db.query(StreamView).filter(StreamView.stream_id.in_(stream_ids)).delete(synchronize_session=False)
        db.query(Stream).filter(Stream.id.in_(stream_ids)).delete(synchronize_session=False)

    user_ids = list(db.scalars(select(User.id).where(User.org_id == org.id)))
    if user_ids:
        db.query(Channel).filter(Channel.owner_id.in_(user_ids)).delete(synchronize_session=False)

    db.query(Membership).filter(Membership.org_id == org.id).delete(synchronize_session=False)
    db.query(User).filter(User.org_id == org.id).delete(synchronize_session=False)

    # org_id intentionally omitted here (unlike the other org actions): the audit_logs.org_id
    # FK would otherwise block this same-transaction delete once the row referenced it.
    log_action(db, user, "organization.delete", target_type="organization", target_id=org.id, meta={"name": org.name}, request=request)
    db.delete(org)
    db.commit()


# ── Users ─────────────────────────────────────────────────────────────────────────
def _user_out(u: User) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = u.organization.name if u.organization else None
    return out


@router.get("/users")
def list_users(
    q: str = "",
    role: str = "",
    org_id: str = "",
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    stmt = select(User)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like), User.username.ilike(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if org_id:
        stmt = stmt.where(User.org_id == org_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_user_out(u) for u in rows], "total": total}


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_user(
    user_id: str,
    data: UserUpdateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target.id == user.id and ((data.role is not None and data.role != "super_admin") or data.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't demote or deactivate your own account")

    if data.full_name is not None:
        target.full_name = data.full_name
    if data.role is not None:
        target.role = data.role
    if data.is_active is not None:
        target.is_active = data.is_active

    log_action(db, user, "user.update", target_type="user", target_id=target.id, org_id=target.org_id, meta={"name": target.full_name, "email": target.email}, request=request)
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    if user_id == str(user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't delete your own account")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    db.query(Membership).filter(Membership.user_id == target.id).delete(synchronize_session=False)
    db.query(Channel).filter(Channel.owner_id == target.id).delete(synchronize_session=False)

    log_action(db, user, "user.delete", target_type="user", target_id=target.id, org_id=target.org_id, meta={"name": target.full_name, "email": target.email}, request=request)
    db.delete(target)
    db.commit()


# ── Events (platform-wide, read-only) ───────────────────────────────────────────────
@router.get("/events", response_model=list[AdminEventOut])
def list_events(
    q: str = "",
    status_filter: str = "",
    org_id: str = "",
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    Host = User
    stmt = (
        select(Stream, Organization.name, Host.full_name)
        .join(Organization, Organization.id == Stream.org_id)
        .outerjoin(Host, Host.id == Stream.host_id)
    )
    if q.strip():
        stmt = stmt.where(Stream.title.ilike(f"%{q.strip()}%"))
    if status_filter:
        stmt = stmt.where(Stream.status == status_filter)
    if org_id:
        stmt = stmt.where(Stream.org_id == org_id)
    rows = db.execute(stmt.order_by(Stream.created_at.desc()).offset(offset).limit(limit)).all()

    return [
        AdminEventOut(
            id=s.id,
            title=s.title,
            status=s.status,
            visibility=s.visibility,
            scheduled_date=s.scheduled_date.isoformat() if s.scheduled_date else None,
            org_id=s.org_id,
            organization_name=org_name,
            host_name=host_name,
            created_at=s.created_at,
        )
        for s, org_name, host_name in rows
    ]


# ── Plans ─────────────────────────────────────────────────────────────────────────
@router.get("/plans", response_model=list[PlanOut])
def list_plans(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    plans = db.scalars(select(Plan).where(Plan.is_active == True).order_by(Plan.price_monthly)).all()  # noqa: E712
    return [PlanOut.model_validate(p, from_attributes=True) for p in plans]


# ── Subscriptions ─────────────────────────────────────────────────────────────────
def _subscription_out(db: Session, sub: Subscription) -> SubscriptionOut:
    org = db.get(Organization, sub.org_id)
    plan = db.get(Plan, sub.plan_id) if sub.plan_id else None
    return SubscriptionOut(
        id=sub.id,
        org_id=sub.org_id,
        organization_name=org.name if org else None,
        plan=plan.name if plan else None,
        price_monthly=plan.price_monthly if plan else None,
        status=sub.status,
        seats=sub.seats,
        started_at=sub.started_at,
        current_period_end=sub.current_period_end,
    )


@router.get("/subscriptions")
def list_subscriptions(
    status_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    stmt = select(Subscription).join(Organization, Organization.id == Subscription.org_id).where(Organization.name != PLATFORM_ORG_NAME)
    if status_filter:
        stmt = stmt.where(Subscription.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Subscription.started_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_subscription_out(db, s) for s in rows], "total": total}


@router.patch("/subscriptions/{sub_id}", response_model=SubscriptionOut)
def update_subscription(
    sub_id: str,
    data: SubscriptionUpdateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    if data.status is not None:
        sub.status = data.status
    if data.seats is not None:
        sub.seats = data.seats
    if data.current_period_end is not None:
        sub.current_period_end = data.current_period_end
    if data.plan_slug:
        plan = db.scalar(select(Plan).where(Plan.slug == data.plan_slug))
        if not plan:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan '{data.plan_slug}'")
        sub.plan_id = plan.id

    org = db.get(Organization, sub.org_id)
    log_action(db, user, "subscription.update", target_type="subscription", target_id=sub.id, org_id=sub.org_id, meta={"name": org.name if org else None}, request=request)
    db.commit()
    db.refresh(sub)
    return _subscription_out(db, sub)


# ── Feature flags ─────────────────────────────────────────────────────────────────
@router.get("/feature-flags", response_model=list[FeatureFlagOut])
def list_feature_flags(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    flags = db.scalars(select(FeatureFlag).order_by(FeatureFlag.created_at.desc())).all()
    return [FeatureFlagOut.model_validate(f, from_attributes=True) for f in flags]


@router.post("/feature-flags", response_model=FeatureFlagOut, status_code=status.HTTP_201_CREATED)
def create_feature_flag(data: FeatureFlagCreateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    if db.scalar(select(FeatureFlag).where(FeatureFlag.key == data.key)):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Flag key '{data.key}' already exists")
    flag = FeatureFlag(**data.model_dump())
    db.add(flag)
    db.flush()
    log_action(db, user, "feature_flag.create", target_type="feature_flag", target_id=flag.id, meta={"name": flag.name}, request=request)
    db.commit()
    db.refresh(flag)
    return FeatureFlagOut.model_validate(flag, from_attributes=True)


@router.patch("/feature-flags/{flag_id}", response_model=FeatureFlagOut)
def update_feature_flag(flag_id: str, data: FeatureFlagUpdateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    flag = db.get(FeatureFlag, flag_id)
    if not flag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature flag not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(flag, field, value)
    log_action(db, user, "feature_flag.update", target_type="feature_flag", target_id=flag.id, meta={"name": flag.name}, request=request)
    db.commit()
    db.refresh(flag)
    return FeatureFlagOut.model_validate(flag, from_attributes=True)


@router.delete("/feature-flags/{flag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feature_flag(flag_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    flag = db.get(FeatureFlag, flag_id)
    if not flag:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature flag not found")
    log_action(db, user, "feature_flag.delete", target_type="feature_flag", target_id=flag.id, meta={"name": flag.name}, request=request)
    db.delete(flag)
    db.commit()


# ── Audit logs ────────────────────────────────────────────────────────────────────
@router.get("/audit-logs")
def list_audit_logs(
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [AuditLogOut.model_validate(r, from_attributes=True) for r in rows]}


# ── Releases ──────────────────────────────────────────────────────────────────────
@router.get("/releases")
def list_releases(
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    rows = db.scalars(select(Release).order_by(Release.released_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [ReleaseOut.model_validate(r, from_attributes=True) for r in rows]}


@router.post("/releases", response_model=ReleaseOut, status_code=status.HTTP_201_CREATED)
def create_release(data: ReleaseCreateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    release = Release(**data.model_dump(), released_by=user.full_name)
    db.add(release)
    db.flush()
    log_action(db, user, "release.create", target_type="release", target_id=release.id, meta={"name": release.version}, request=request)
    db.commit()
    db.refresh(release)
    return ReleaseOut.model_validate(release, from_attributes=True)


@router.delete("/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_release(release_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    release = db.get(Release, release_id)
    if not release:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Release not found")
    log_action(db, user, "release.delete", target_type="release", target_id=release.id, meta={"name": release.version}, request=request)
    db.delete(release)
    db.commit()


# ── Support tickets ───────────────────────────────────────────────────────────────
def _ticket_out(db: Session, t: SupportTicket) -> SupportTicketOut:
    org = db.get(Organization, t.org_id)
    out = SupportTicketOut.model_validate(t, from_attributes=True)
    out.organization_name = org.name if org else None
    return out


@router.get("/support-tickets")
def list_support_tickets(
    status_filter: str = "",
    limit: int = 50,
    offset: int = 0,
    page_size: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_super_admin(user)
    limit = page_size or limit
    stmt = select(SupportTicket)
    if status_filter:
        stmt = stmt.where(SupportTicket.status == status_filter)
    rows = db.scalars(stmt.order_by(SupportTicket.created_at.desc()).offset(offset).limit(limit)).all()
    return {"items": [_ticket_out(db, t) for t in rows]}


@router.post("/support-tickets", response_model=SupportTicketOut, status_code=status.HTTP_201_CREATED)
def create_support_ticket(data: SupportTicketCreateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    if not db.get(Organization, data.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    ticket = SupportTicket(**data.model_dump())
    db.add(ticket)
    db.flush()
    log_action(db, user, "support_ticket.create", target_type="support_ticket", target_id=ticket.id, org_id=ticket.org_id, meta={"name": ticket.subject}, request=request)
    db.commit()
    db.refresh(ticket)
    return _ticket_out(db, ticket)


@router.patch("/support-tickets/{ticket_id}", response_model=SupportTicketOut)
def update_support_ticket(ticket_id: str, data: SupportTicketUpdateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    ticket.status = data.status
    log_action(db, user, "support_ticket.update", target_type="support_ticket", target_id=ticket.id, org_id=ticket.org_id, meta={"name": ticket.subject, "status": ticket.status}, request=request)
    db.commit()
    db.refresh(ticket)
    return _ticket_out(db, ticket)


@router.delete("/support-tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_support_ticket(ticket_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    ticket = db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    log_action(db, user, "support_ticket.delete", target_type="support_ticket", target_id=ticket.id, org_id=ticket.org_id, meta={"name": ticket.subject}, request=request)
    db.delete(ticket)
    db.commit()


# ── API keys ──────────────────────────────────────────────────────────────────────
@router.get("/organizations/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(org_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    _get_org(db, org_id)
    keys = db.scalars(select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc())).all()
    return [ApiKeyOut.model_validate(k, from_attributes=True) for k in keys]


@router.post("/organizations/{org_id}/api-keys", response_model=ApiKeyCreateOut, status_code=status.HTTP_201_CREATED)
def create_api_key(org_id: str, data: ApiKeyCreateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    org = _get_org(db, org_id)
    raw = secrets.token_urlsafe(32)
    key = f"zk_live_{raw}"
    prefix = key[:12]
    entry = ApiKey(org_id=org.id, label=data.label, prefix=prefix, key_hash=hash_password(key))
    db.add(entry)
    db.flush()
    log_action(db, user, "api_key.create", target_type="api_key", target_id=entry.id, org_id=org.id, meta={"name": entry.label, "organization": org.name}, request=request)
    db.commit()
    db.refresh(entry)
    return ApiKeyCreateOut(id=entry.id, label=entry.label, prefix=entry.prefix, revoked=entry.revoked, created_at=entry.created_at, key=key)


@router.delete("/organizations/{org_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(org_id: str, key_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    entry = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id))
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    entry.revoked = True
    entry.revoked_at = datetime.now(timezone.utc)
    log_action(db, user, "api_key.revoke", target_type="api_key", target_id=entry.id, org_id=entry.org_id, meta={"name": entry.label}, request=request)
    db.commit()


# ── Roles (static reference — reflects the role checks actually enforced in code) ──
_ROLE_REFERENCE = [
    {
        "role": "super_admin", "rank": 1, "label": "Super Admin",
        "description": "Platform operator. Every /admin/* endpoint in this console is gated to this role alone.",
        "capabilities": [
            "Create, edit, suspend, and delete any organization",
            "Edit or delete any user on the platform, across every organization",
            "Manage subscriptions, plans, feature flags, and platform settings",
            "View the full platform audit log and analytics",
        ],
    },
    {
        "role": "org_admin", "rank": 2, "label": "Organization Admin",
        "description": "Full control within one organization.",
        "capabilities": [
            "Create, edit, and manage events/streams (routers/streams.py)",
            "Invite and manage org members — the only role that can (routers/members.py)",
            "Manage recordings and registrations for the org's events",
            "Moderate chat, Q&A, polls, and the stage during any event",
        ],
    },
    {
        "role": "host", "rank": 3, "label": "Host",
        "description": "Runs events on behalf of an organization.",
        "capabilities": [
            "Create, edit, and manage events/streams (routers/streams.py)",
            "Manage recordings and registrations for events they run",
            "Moderate chat, Q&A, polls, and the stage during any event",
        ],
    },
    {
        "role": "moderator", "rank": 4, "label": "Moderator",
        "description": "Manages audience interaction during a live event.",
        "capabilities": [
            "Moderate chat messages (routers/chat.py)",
            "Manage Q&A and polls (routers/qa.py, routers/polls.py)",
            "Promote raised hands to the stage (services/stage.py)",
        ],
    },
    {
        "role": "speaker", "rank": 5, "label": "Speaker",
        "description": "Promoted to the stage during an event; no management endpoints are gated to this role specifically.",
        "capabilities": [
            "Appear on stage once promoted from a raised hand",
            "Participate in chat, Q&A, and polls like any attendee",
        ],
    },
    {
        "role": "viewer", "rank": 6, "label": "Viewer",
        "description": "Baseline authenticated attendee.",
        "capabilities": [
            "Watch public/unlisted events and register where required",
            "Post chat messages, ask questions, vote in polls, and raise a hand",
        ],
    },
]


@router.get("/roles", response_model=list[RoleOut])
def list_roles(user: User = Depends(get_current_user)):
    _require_super_admin(user)
    return _ROLE_REFERENCE


# ── Platform settings ─────────────────────────────────────────────────────────────
_SETTINGS_DEFAULTS = {
    "brand": {"name": "ZoikoStream", "primary_color": "#8b5cf6", "support_email": ""},
    "storage_limits": {"default_gb": 50, "max_gb": 5000},
    "streaming_limits": {"default_hours": 20, "max_bitrate_kbps": 8000},
    "global": {"signups_enabled": True, "maintenance_mode": False},
}
_SETTINGS_CATEGORIES = {"brand": "brand", "storage_limits": "limits", "streaming_limits": "limits", "global": "config"}


@router.get("/settings")
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    rows = {r.key: r for r in db.scalars(select(PlatformSetting))}
    return {
        key: {
            "value": rows[key].value if key in rows else default,
            "updated_at": rows[key].updated_at if key in rows else None,
        }
        for key, default in _SETTINGS_DEFAULTS.items()
    }


@router.patch("/settings")
def update_settings(data: PlatformSettingsUpdateIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    for key, value in data.values.items():
        if key not in _SETTINGS_DEFAULTS:
            continue
        row = db.get(PlatformSetting, key)
        if row:
            row.value = value
            row.updated_by = user.email
        else:
            db.add(PlatformSetting(key=key, value=value, category=_SETTINGS_CATEGORIES.get(key), updated_by=user.email))
    log_action(db, user, "settings.update", target_type="platform_setting", meta={"groups": list(data.values.keys())}, request=request)
    db.commit()
    return get_settings(user, db)


# ── Platform health ───────────────────────────────────────────────────────────────
def _platform_health(db: Session) -> dict:
    started = time.perf_counter()
    try:
        db.execute(select(1))
        db_latency = round((time.perf_counter() - started) * 1000)
        db_status = "ok"
    except Exception:
        db_latency = None
        db_status = "down"

    services = [
        {"id": "database", "name": "Database", "note": "Live round-trip ping on every request to this endpoint", "status": db_status, "latency_ms": db_latency},
        {
            "id": "email", "name": "Email Delivery (Resend)",
            "note": "Configured" if app_settings.RESEND_API_KEY else "RESEND_API_KEY not set — welcome/invite emails are skipped, not sent",
            "status": "ok" if app_settings.RESEND_API_KEY else "not_configured", "latency_ms": None,
        },
        {
            "id": "livekit", "name": "Live Streaming (LiveKit)",
            "note": "Configured" if (app_settings.LIVEKIT_URL and app_settings.LIVEKIT_API_KEY and app_settings.LIVEKIT_API_SECRET) else "LIVEKIT_* not set — streaming endpoints will fail",
            "status": "ok" if (app_settings.LIVEKIT_URL and app_settings.LIVEKIT_API_KEY and app_settings.LIVEKIT_API_SECRET) else "not_configured", "latency_ms": None,
        },
        {
            "id": "recording_storage", "name": "Recording Storage (GCS)",
            "note": "Configured" if (app_settings.GCS_BUCKET and app_settings.GCS_CREDENTIALS_FILE) else "GCS_BUCKET/GCS_CREDENTIALS_FILE not set — recording uploads will fail",
            "status": "ok" if (app_settings.GCS_BUCKET and app_settings.GCS_CREDENTIALS_FILE) else "not_configured", "latency_ms": None,
        },
    ]
    overall = "down" if db_status == "down" else ("warn" if any(s["status"] == "warn" for s in services) else "ok")
    return {"overall": overall, "services": services}


@router.get("/platform-health")
def platform_health(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    return _platform_health(db)


# ── Live events ───────────────────────────────────────────────────────────────────
@router.get("/live-events", response_model=list[LiveEventOut])
def live_events(state: str = "live", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    if state == "live":
        rows = db.execute(
            select(Stream, Organization.name, Channel.name)
            .join(Organization, Organization.id == Stream.org_id)
            .join(Channel, Channel.id == Stream.channel_id)
            .where(Stream.status == "live")
            .order_by(Stream.started_at.desc())
        ).all()
        return [
            LiveEventOut(
                id=s.id, organization=org_name, title=s.title, channel=chan_name,
                started_at=s.started_at, ended_at=None,
                viewers=presence.viewer_count(str(s.id)),
            )
            for s, org_name, chan_name in rows
        ]

    rows = db.execute(
        select(Stream, Organization.name, Channel.name)
        .join(Organization, Organization.id == Stream.org_id)
        .join(Channel, Channel.id == Stream.channel_id)
        .where(Stream.status == "completed")
        .order_by(Stream.ended_at.desc())
        .limit(20)
    ).all()
    return [
        LiveEventOut(
            id=s.id, organization=org_name, title=s.title, channel=chan_name,
            started_at=s.started_at, ended_at=s.ended_at,
            viewers=db.scalar(select(func.count(StreamView.id)).where(StreamView.stream_id == s.id)) or 0,
        )
        for s, org_name, chan_name in rows
    ]


# ── Dashboard ─────────────────────────────────────────────────────────────────────
@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    not_platform = Organization.name != PLATFORM_ORG_NAME

    total_organizations = db.scalar(select(func.count(Organization.id)).where(not_platform)) or 0
    total_users = db.scalar(
        select(func.count(User.id)).join(Organization, Organization.id == User.org_id).where(not_platform)
    ) or 0
    live_stream_ids = list(db.scalars(select(Stream.id).where(Stream.status == "live")))
    concurrent_viewers = sum(presence.viewer_count(str(sid)) for sid in live_stream_ids)
    storage_used_gb = db.scalar(select(func.coalesce(func.sum(Organization.storage_used_gb), 0)).where(not_platform)) or 0

    health = _platform_health(db)

    org_growth = _monthly_series(db, Organization, Organization.created_at, extra_where=not_platform)
    user_growth = _monthly_series(
        db, User, User.created_at,
        extra_where=User.org_id.in_(select(Organization.id).where(not_platform)),
    )
    revenue_growth = _revenue_series(db)

    latest_orgs = db.scalars(
        select(Organization).where(not_platform).order_by(Organization.created_at.desc()).limit(5)
    ).all()
    latest_users = db.scalars(
        select(User).join(Organization, Organization.id == User.org_id).where(not_platform).order_by(User.created_at.desc()).limit(5)
    ).all()

    return {
        "summary": {
            "platform_health": health["overall"],
            "total_organizations": total_organizations,
            "total_users": total_users,
            "live_events": len(live_stream_ids),
            "concurrent_viewers": concurrent_viewers,
            "monthly_revenue": _current_mrr(db),
            "storage_used_gb": round(float(storage_used_gb), 1),
        },
        "organization_growth": org_growth,
        "user_growth": user_growth,
        "revenue_growth": revenue_growth,
        "recent_alerts": [
            {
                "id": s["id"],
                "title": f"{s['name']} {'down' if s['status'] == 'down' else 'degraded'}",
                "detail": s["note"],
                "severity": s["status"],
            }
            for s in health["services"] if s["status"] in ("warn", "down")
        ],
        "latest_organizations": [{"id": o.id, "name": o.name, "created_at": o.created_at} for o in latest_orgs],
        "latest_signups": [
            {"id": u.id, "full_name": u.full_name, "role": u.role, "organization_name": u.organization.name if u.organization else None, "created_at": u.created_at}
            for u in latest_users
        ],
    }


# ── Analytics ───────────────────────────────────────────────────────────────────────
@router.get("/analytics")
def get_analytics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_super_admin(user)
    not_platform = Organization.name != PLATFORM_ORG_NAME

    streaming_seconds = db.scalars(
        select(Stream).where(Stream.status == "completed", Stream.started_at.isnot(None), Stream.ended_at.isnot(None))
    ).all()
    streaming_hours = round(
        sum((s.ended_at - s.started_at).total_seconds() for s in streaming_seconds) / 3600, 1
    )

    return {
        "mrr": _current_mrr(db),
        "streaming_hours": streaming_hours,
        "revenue": _revenue_series(db),
        "organizations": _monthly_series(db, Organization, Organization.created_at, extra_where=not_platform),
        "users": _monthly_series(
            db, User, User.created_at,
            extra_where=User.org_id.in_(select(Organization.id).where(not_platform)),
        ),
        "note": "Traffic and bandwidth aren't shown — no request-metering pipeline exists for them yet.",
    }
