"""Super Admin platform API. Every route is gated by require_super_admin (403 otherwise).
Thin controllers: DB access -> crud.admin, aggregation -> services.admin, and every
mutation writes an audit log."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..crud import admin as crud
from ..crud import event as event_crud
from ..db import get_db
from ..services import account_lifecycle as lifecycle
from ..services import org_comms
from ..services import org_governance as governance
from ..services import developer_comms
from ..services import support_access as support_svc
from ..services import support_comms
from ..services import trust_center
from ..services import vuln_disclosure
from ..services import marketing
from ..services import media_retention
from ..services import tenant_access
from ..services.tenant_access import SupportContext, support_context
from ..models import (
    ESCALATION_REASONS,
    EVIDENCE_ACCESS_TTL_HOURS,
    FeatureAnnouncement,
    FeatureAvailability,
    Incident,
    MarketingWebinar,
    Release,
    ReleaseDigest,
    SecurityAdvisory,
    TrustEvidenceRequest,
    VulnerabilityReport,
    LiveRecording,
    RetentionExtension,
    ORG_STATE_ACTIVE,
    ORG_STATE_DELETED,
    SUPPORT_REASONS,
    ElevationSession,
    Event,
    Organization,
    PlatformSetting,
    SupportAccessRequest,
    User,
)
from ..schemas.admin import (
    AdvisoryCloseIn,
    AdvisoryDraftIn,
    AdvisoryImpactIn,
    AdvisoryRemediationIn,
    AdvisoryUpdateIn,
    AnnouncementDraftIn,
    DigestDraftIn,
    EvidenceDecisionIn,
    FeatureAvailabilityIn,
    FeatureGrantIn,
    ReleaseApprovalIn,
    TrustDocumentIn,
    VulnCloseIn,
    VulnCoordinateIn,
    VulnLifecycleIn,
    WebinarCompleteIn,
    WebinarIn,
    WebinarRescheduleIn,
    LegalHoldIn,
    RetentionDecisionIn,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    ElevationRequest,
    SupportAccessAmend,
    SupportAccessCreate,
    SupportAccessOut,
    FeatureFlagCreate,
    FeatureFlagOut,
    FeatureFlagUpdate,
    GovernanceRecordCreate,
    GovernanceRecordOut,
    GovernanceRecordUpdate,
    IncidentCreate,
    IncidentOut,
    IncidentUpdate,
    OrgCreate,
    OrgOut,
    OrgUpdate,
    Page,
    ReleaseCreate,
    ReleaseOut,
    SettingsUpdate,
    SubscriptionUpdate,
    SupportActionRequestIn,
    SupportCaseUpdateIn,
    SupportCloseIn,
    SupportEscalateIn,
    SupportIncidentLinkIn,
    SupportOwnerChangeIn,
    SupportResolveIn,
    SupportTicketCreate,
    SupportTicketOut,
    SupportTicketUpdate,
    UserUpdate,
)
from .. import security
from ..security import require_super_admin
from ..services import admin as svc
from ..services import broadcast as broadcast_svc
from ..services import livekit
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
    """Open a scoped, expiring elevation for PLATFORM operations.

    ZST-EC-001 ORG-009 boundary: this route may no longer be used to reach into a customer
    tenant. Access to a specific Organization goes through /admin/support-access, which is
    gated on that Organization's own approval and is announced to them. Elevation here
    carries no org scope, so nothing about it is customer-invisible in the way ORG-009
    exists to prevent.
    """
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


# ── ORG-009 Authorized support access (ZST-EC-001) ───────────────────────────
# Replaces silent, self-approved, org-unscoped elevation as the route to customer data.
#
# Before this, POST /elevation opened an ElevationSession immediately: the requesting
# super admin approved their own access, the record carried no organization, no support
# case and no engineer identity for the customer, and the customer was never told. The
# three routes below split that into request -> (customer approves) -> start, with the
# approval bound to the exact terms.


def _support_out(req: SupportAccessRequest) -> dict:
    """Staff-side view. Includes the parsed action list, which the ORM stores newline-joined."""
    payload = SupportAccessOut.model_validate(req).model_dump()
    payload["allowed_action_list"] = support_svc.actions_list(req.allowed_actions)
    return payload


@router.post("/support-access", status_code=status.HTTP_201_CREATED)
def request_support_access(data: SupportAccessCreate, request: Request,
                          background: BackgroundTasks,
                          db: Session = Depends(get_db),
                          admin: User = Depends(require_super_admin)):
    """Ask an Organization for scoped, time-limited access. Grants nothing."""
    org = db.get(Organization, data.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if data.reason_category not in SUPPORT_REASONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown reason category")
    if data.emergency and not (data.emergency_reason or "").strip():
        # Emergency access without a declared reason is exactly the disguise the canonical
        # controls forbid, so the domain refuses it rather than recording a blank.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Emergency access requires a declared reason")

    # Capabilities must come from the enforced vocabulary. Free text could not be checked at
    # the authorization boundary, so an approval of "look at their stuff" would have been
    # unenforceable — and therefore meaningless.
    capabilities = tenant_access.normalize_capabilities(data.allowed_actions)
    if not capabilities:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"allowed_actions must name known capabilities: "
            f"{sorted(tenant_access.CAPABILITIES)}")

    req = support_svc.create_request(
        db, org_id=org.id, case_reference=data.case_reference,
        reason_category=data.reason_category, engineer=admin,
        engineer_display=data.engineer_display, requested_scope=data.requested_scope,
        allowed_actions=capabilities, minutes=data.minutes,
        emergency=data.emergency, emergency_reason=data.emergency_reason,
        emergency_authorizer=None,
    )
    _audit(db, admin, request, "support_access.request", target_type="support_access",
           target_id=req.id, org_id=org.id,
           meta={"case": req.case_reference, "scope": req.requested_scope,
                 "minutes": req.requested_minutes, "emergency": req.emergency})
    # The request itself is announced to the customer, because a request for access to their
    # data is something they are entitled to see even if it is never approved.
    support_svc.notify_requested(db, background, req)
    return _support_out(req)


@router.patch("/support-access/{req_id}")
def amend_support_access(req_id: uuid.UUID, data: SupportAccessAmend, request: Request,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_super_admin)):
    """Change the terms. Any change invalidates an existing approval."""
    req = db.get(SupportAccessRequest, req_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support request not found")
    if req.status not in ("requested", "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only a pending or approved request can be amended")
    support_svc.amend_terms(db, req, **data.model_dump(exclude_unset=True))
    _audit(db, admin, request, "support_access.amend", target_type="support_access",
           target_id=req.id, org_id=req.org_id,
           meta={"approval_invalidated": True, "status": req.status})
    return _support_out(req)


@router.post("/support-access/{req_id}/countersign")
def countersign_support_access(req_id: uuid.UUID, request: Request,
                               db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Second independent authorization for emergency (break-glass) support access.

    Deliberately a platform route rather than a tenant one: break-glass exists precisely
    for the case where the customer cannot be reached in time. What it is NOT is a
    single-operator decision, so the caller here must be someone other than the engineer
    who raised the request.
    """
    req = db.get(SupportAccessRequest, req_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support request not found")
    outcome = support_svc.countersign_emergency(db, req, admin)
    if outcome == support_svc.SELF_AUTHORIZED:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You cannot countersign your own emergency request")
    if outcome == "not_emergency":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This request is not an emergency request")
    if outcome != support_svc.OK:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request is already {outcome}")
    _audit(db, admin, request, "support_access.countersign",
           target_type="support_access", target_id=req.id, org_id=req.org_id,
           meta={"case": req.case_reference, "engineer": req.engineer_display})
    return _support_out(req)


@router.post("/support-access/{req_id}/start")
def start_support_access(req_id: uuid.UUID, request: Request, background: BackgroundTasks,
                        db: Session = Depends(get_db),
                        admin: User = Depends(require_super_admin)):
    """Begin an approved session. THIS is the gate."""
    req = db.get(SupportAccessRequest, req_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support request not found")

    outcome, _elevation = support_svc.start(db, req)
    if outcome == support_svc.NOT_APPROVED:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This Organization has not approved this support request")
    if outcome == support_svc.NO_SECOND_AUTHORIZER:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Emergency access requires an independent second authorizer. Have another "
            "privileged operator countersign this request first.")
    if outcome == support_svc.SELF_AUTHORIZED:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "The requesting engineer cannot authorize their own emergency "
                            "access")
    if outcome == support_svc.TERMS_CHANGED:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The terms changed after approval; request a new approval")
    if outcome == support_svc.ALREADY_ACTIVE:
        return _support_out(req)

    _audit(db, admin, request, "support_access.start", target_type="support_access",
           target_id=req.id, org_id=req.org_id,
           meta={"case": req.case_reference, "expires_at": req.expires_at.isoformat(),
                 "emergency": req.emergency})
    if req.emergency:
        # Announced at once, not after the fact.
        support_svc.notify_emergency(db, background, req)
    support_svc.notify_started(db, background, req)
    return _support_out(req)


@router.post("/support-access/{req_id}/end")
def end_support_access(req_id: uuid.UUID, request: Request, background: BackgroundTasks,
                      db: Session = Depends(get_db),
                      admin: User = Depends(require_super_admin)):
    """End an active session early. The sweeper handles expiry on its own."""
    req = db.get(SupportAccessRequest, req_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support request not found")
    if not support_svc.end(db, req):
        raise HTTPException(status.HTTP_409_CONFLICT, "No active session to end")
    _audit(db, admin, request, "support_access.end", target_type="support_access",
           target_id=req.id, org_id=req.org_id, meta={"case": req.case_reference})
    support_svc.notify_ended(db, background, req)
    return _support_out(req)


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
def get_organization(org_id: uuid.UUID, db: Session = Depends(get_db),
                    ctx: SupportContext = Depends(support_context)):
    # ORG-009 gate. Reading one tenant's record is support access, not a platform-global
    # operation, so it needs that Organization's own approval.
    ctx.authorize(org_id, tenant_access.CAP_TENANT_READ,
                  resource="organization", resource_id=org_id)
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
                       background: BackgroundTasks,
                       db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    previous_state = org.status
    crud.update_organization(db, org, data)
    _audit(db, admin, request, "organization.update", target_type="organization",
           target_id=org.id, org_id=org.id, meta=data.model_dump(exclude_none=True))

    # ORG-010 — only when the operational state actually changed, and only after commit. A
    # name, domain or usage edit is not a restriction and must not notify.
    governance.announce_org_state(
        db, background, org_id=org.id, org_name=org.name,
        previous_state=previous_state, state=org.status,
        reason_category=data.reason_category,
    )
    return crud.get_organization(db, org.id)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(org_id: uuid.UUID, request: Request, background: BackgroundTasks,
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
    previous_state = org.status
    crud.delete_organization(db, org)
    # ORG-010 "deleted". The event carries org_name and no FK, so the record survives the
    # organization row it describes. Recipient resolution runs before the delete would have
    # orphaned it — this path already refuses to delete an organization that still has
    # users, so in practice there is nobody left to notify and notify_org_state no-ops.
    governance.announce_org_state(db, background, org_id=org.id, org_name=name,
                                 previous_state=previous_state, state=ORG_STATE_DELETED,
                                 reason_category=None)
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


@router.get("/users/summary")
def user_summary(db: Session = Depends(get_db)):
    """Dataset-wide counts for the Users console's KPI row, independent of whatever search/
    filter is active — one query instead of the four page_size=1 round trips the page used
    to make just to total/active/inactive/super_admin counts."""
    return crud.user_stats(db)


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, data: UserUpdate, request: Request,
               background: BackgroundTasks,
               ctx: SupportContext = Depends(support_context),
               db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    # Guard against self-lockout: can't deactivate or demote your own account.
    if user.id == admin.id and (data.is_active is False or (data.role and data.role != "super_admin")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate or demote yourself")
    # ORG-009 gate. Editing a tenant's member is support access to that tenant, so the
    # Organization must have approved it. Platform enforcement of the ORGANIZATION itself
    # (suspend/restrict/delete) is a separate, deliberately ungated path — see
    # update_organization — because requiring the customer's consent to restrict the
    # customer would make that control meaningless.
    ctx.authorize(user.org_id, tenant_access.CAP_MEMBERS_WRITE,
                  resource="user", resource_id=user.id,
                  summary=", ".join(sorted(data.model_dump(exclude_none=True))))
    was_active = user.is_active
    previous_access = org_comms.describe_access(role=user.role, org=user.organization,
                                                active=user.is_active)
    try:
        out = crud.update_user(db, user, data)
    except HTTPException:
        raise
    except ValueError as e:
        # A failed update sends nothing: the state was never committed, so there is no
        # transition to report.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    _audit(db, admin, request, "user.update", target_type="user", target_id=user.id,
           org_id=user.org_id, meta=data.model_dump(exclude_none=True))

    # ORG-003 — committed access-policy change, from the super-admin path. Same normalized
    # snapshot comparison as the org-admin path, so a rename sends nothing here either.
    org_comms.announce_access_changed(
        db, background, user=user, org_id=user.org_id,
        previous_access=previous_access,
        current_access=org_comms.describe_access(role=user.role, org=user.organization,
                                                 active=user.is_active),
    )

    # IDN-008 — only when the ACTIVE state actually flipped, and only after the change is
    # committed. A rename or role edit is not a restriction and must not notify.
    if data.is_active is not None and data.is_active != was_active:
        lifecycle.announce(
            db, background, user=user, email=user.email, org_id=user.org_id,
            state=lifecycle.STATE_RESTRICTED if not user.is_active else lifecycle.STATE_REACTIVATED,
            reason_category="administrative_action",
        )
    return out


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: uuid.UUID, request: Request, background: BackgroundTasks,
               ctx: SupportContext = Depends(support_context),
               db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    ctx.authorize(user.org_id, tenant_access.CAP_MEMBERS_WRITE,
                  resource="user", resource_id=user.id, summary="hard delete")
    email, org_id = user.email, user.org_id
    previous_access = org_comms.describe_access(role=user.role, org=user.organization,
                                                active=user.is_active)
    crud.delete_user(db, user)
    # ORG-004 — the Organization membership ended. user=None because this path hard-deletes
    # the row; OrgMembershipEvent carries no FK precisely so the record outlives it.
    org_comms.announce_membership_removed(db, background, user=None, email=email,
                                          org_id=org_id, previous_access=previous_access,
                                          identity_also_restricted=True)
    _audit(db, admin, request, "user.delete", target_type="user", target_id=user_id,
           org_id=org_id, meta={"email": email})
    # IDN-008 "Deletion completed", after the delete is committed. The address was captured
    # above because this is a hard delete — there is no row left to read it from. user=None
    # for the same reason: AccountStateEvent must outlive the identity it describes.
    lifecycle.announce(db, background, user=None, email=email, org_id=org_id,
                       state=lifecycle.STATE_DELETION_COMPLETED,
                       reason_category="administrative_action")


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
                       ctx: SupportContext = Depends(support_context),
                       db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    _sub = db.get(Subscription, sub_id)
    if _sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    ctx.authorize(_sub.org_id, tenant_access.CAP_BILLING_WRITE,
                  resource="subscription", resource_id=sub_id)
    sub = crud.get_subscription(db, sub_id)
    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    try:
        out = crud.update_subscription(db, sub, data)
    except ValueError as e:
        # An illegal or unknown §12 transition is a business validation failure, not a server
        # error — same posture as the commercial routes' ValueError handling.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
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


@router.get("/recordings")
def list_recordings(
    status_: str | None = Query(None, alias="status"),
    org_id: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Cross-org recordings for the Media console (pages/admin/Media.jsx) — real
    LiveRecording rows, nothing fabricated."""
    return svc.list_recordings(db, status=status_, org_id=org_id, limit=limit)


@router.get("/recordings/{recording_id}/playback-url")
def recording_playback_url(recording_id: uuid.UUID, db: Session = Depends(get_db),
                          ctx: SupportContext = Depends(support_context)):
    # The most sensitive tenant read in the console: this returns a playable URL for a
    # customer's recorded session. The owning Organization is resolved from the recording
    # itself so the approval is checked against the tenant whose media it actually is.
    owner_org_id = svc.recording_org_id(db, recording_id)
    ctx.authorize(owner_org_id, tenant_access.CAP_RECORDINGS_READ,
                  resource="recording", resource_id=recording_id)
    url = svc.recording_playback_url(db, recording_id)
    if url is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No playable file for this recording")
    return {"url": url}


@router.get("/event-readiness")
def event_readiness(
    include_test: bool = Query(False),
    high_impact_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """The full Event Readiness pipeline (pages/admin/EventReadiness.jsx) — the same real
    gate computation the Command Center's badge count and "upcoming high-impact" widget
    already use (ops_svc.event_readiness), just without the dashboard's top-8/high-impact-
    only narrowing, so every upcoming event with a scheduled start time shows up here."""
    return ops_svc.event_readiness(db, include_test=include_test, limit=limit, high_impact_only=high_impact_only)


@router.get("/events/{event_id}")
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db),
             ctx: SupportContext = Depends(support_context)):
    _ev_org = svc.event_org_id(db, event_id)
    ctx.authorize(_ev_org, tenant_access.CAP_EVENTS_READ,
                  resource="event", resource_id=event_id)
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
    ctx: SupportContext = Depends(support_context),
    db: Session = Depends(get_db),
):
    _ev_org = svc.event_org_id(db, event_id)
    ctx.authorize(_ev_org, tenant_access.CAP_EVENTS_WRITE,
                  resource="event", resource_id=event_id, summary="delete event")
    """Delete any event, any org. If it's currently live/paused, force-ends the broadcast
    first (stops the recording, closes the LiveKit room) so nothing is orphaned — a soft
    delete alone would leave an active broadcast_sessions row with no way to reach it."""
    ev = db.get(Event, event_id)
    if ev is None or ev.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    was_live = ev.status in ("live", "paused")
    if was_live:
        ctx = mod.Ctx(
            event_id=ev.id, org_id=ev.org_id, room=livekit.room_for_event(ev.id),
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


# ── Incidents (Trust & Safety console) ───────────────────────────────────────
# Same `incidents` table the Command Center's Incidents panel and action queues already
# read (services/ops.py) — this is the write side that table never had, and the console a
# super admin uses to open/track/resolve a security case for real.

@router.get("/incidents", response_model=Page)
def list_incidents(
    status_: str | None = Query(None, alias="status"),
    kind: str | None = None,
    org_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = crud.list_incidents(db, status=status_, kind=kind, org_id=org_id, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/incidents", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(data: IncidentCreate, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    if data.org_id and db.get(Organization, data.org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    incident = crud.create_incident(db, data, commander_default=admin.full_name or admin.email)
    _audit(db, admin, request, "incident.create", target_type="incident",
           target_id=incident.id, org_id=data.org_id, meta={"title": data.title, "severity": data.severity})
    return incident


@router.patch("/incidents/{incident_id}", response_model=IncidentOut)
def update_incident(incident_id: uuid.UUID, data: IncidentUpdate, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    incident = crud.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    org_id = incident.org_id
    out = crud.update_incident(db, incident, data)
    _audit(db, admin, request, "incident.update", target_type="incident",
           target_id=incident_id, org_id=org_id, meta=data.model_dump(exclude_none=True))
    return out


# ── Media retention, legal hold and extension approval (ZST-EC-001 MED-011) ───
# Platform governance only. The asymmetry is deliberate and matches legal holds: the party
# whose data is being retained is not the party who decides how long it is retained.

@router.post("/recordings/{recording_id}/legal-hold")
def place_recording_legal_hold(recording_id: uuid.UUID, data: LegalHoldIn,
                               background: BackgroundTasks,
                               admin: User = Depends(require_super_admin),
                               db: Session = Depends(get_db)):
    """Place a legal hold on one recording. Blocks deletion until released."""
    rec = db.get(LiveRecording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    try:
        media_retention.place_hold(db, background, rec, actor=admin,
                                   category=data.category,
                                   hold_reference=data.hold_reference)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    svc.create_audit_log(db, actor=admin, action="media.legal_hold_place",
                         target_type="live_recording", target_id=rec.id,
                         org_id=rec.org_id, meta={"category": data.category})
    return {"recording_id": str(rec.id), "legal_hold": True,
            "hold_reference": rec.hold_reference}


@router.delete("/recordings/{recording_id}/legal-hold")
def release_recording_legal_hold(recording_id: uuid.UUID, background: BackgroundTasks,
                                 admin: User = Depends(require_super_admin),
                                 db: Session = Depends(get_db)):
    rec = db.get(LiveRecording, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    media_retention.release_hold(db, background, rec, actor=admin)
    svc.create_audit_log(db, actor=admin, action="media.legal_hold_release",
                         target_type="live_recording", target_id=rec.id, org_id=rec.org_id)
    return {"recording_id": str(rec.id), "legal_hold": False}


@router.post("/retention-extensions/{extension_id}/decision")
def decide_retention_extension(extension_id: uuid.UUID, data: RetentionDecisionIn,
                               background: BackgroundTasks,
                               admin: User = Depends(require_super_admin),
                               db: Session = Depends(get_db)):
    """Approve or decline a retention extension. Only an approval writes a new date, and it
    writes it in the same transaction as the decision."""
    extension = db.get(RetentionExtension, extension_id)
    if extension is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Retention extension not found")
    try:
        extension = media_retention.decide_extension(
            db, background, extension, approver=admin, approve=data.approve,
            granted_until=data.granted_until, note=data.note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    svc.create_audit_log(db, actor=admin, action=f"retention.extension_{extension.state}",
                         target_type="live_recording", target_id=extension.recording_id,
                         org_id=extension.org_id, meta={"state": extension.state})
    return {"id": str(extension.id), "state": extension.state,
            "granted_until": extension.granted_until}


# ── Governance records ────────────────────────────────────────────────────────
# The same `governance_records` table services/ops.py already reads for
# single_path_override and break_glass — this is the write side, plus the other kinds
# (dpia, legal_hold, privacy_request, access_review, exception, obligation) the Governance
# console needs. GOVERNANCE_KINDS is this API's own closed list (informational for
# clients), not a DB constraint.

@router.get("/governance-records", response_model=Page)
def list_governance_records(
    kind: str | None = None,
    status_: str | None = Query(None, alias="status"),
    org_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    items, total = crud.list_governance_records(db, kind=kind, status=status_, org_id=org_id, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/governance-records", response_model=GovernanceRecordOut, status_code=status.HTTP_201_CREATED)
def create_governance_record(data: GovernanceRecordCreate, request: Request,
                             db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    if data.org_id and db.get(Organization, data.org_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    record = crud.create_governance_record(db, data)
    _audit(db, admin, request, "governance_record.create", target_type="governance_record",
           target_id=record.id, org_id=data.org_id, meta={"kind": data.kind})
    return record


@router.patch("/governance-records/{record_id}", response_model=GovernanceRecordOut)
def update_governance_record(record_id: uuid.UUID, data: GovernanceRecordUpdate, request: Request,
                             db: Session = Depends(get_db), admin: User = Depends(require_super_admin)):
    record = crud.get_governance_record(db, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Governance record not found")
    org_id = record.org_id
    out = crud.update_governance_record(db, record, data)
    _audit(db, admin, request, "governance_record.update", target_type="governance_record",
           target_id=record_id, org_id=org_id, meta=data.model_dump(exclude_none=True))
    return out


# ── Developer / API keys ─────────────────────────────────────────────────────

@router.get("/organizations/{org_id}/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(org_id: uuid.UUID, db: Session = Depends(get_db),
                 ctx: SupportContext = Depends(support_context)):
    ctx.authorize(org_id, tenant_access.CAP_CREDENTIALS_READ,
                  resource="api_key", resource_id=org_id)
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return crud.list_api_keys(db, org)


@router.post("/organizations/{org_id}/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(org_id: uuid.UUID, data: ApiKeyCreate, request: Request,
                   background: BackgroundTasks,
                   db: Session = Depends(get_db),
                   ctx: SupportContext = Depends(support_context),
                   admin: User = Depends(require_super_admin)):
    ctx.authorize(org_id, tenant_access.CAP_CREDENTIALS_WRITE,
                  resource="api_key", resource_id=org_id)
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    created = crud.create_api_key(db, org, data.label, expires_in_days=data.expires_in_days)
    # ZST-EC-001 DEV-002. The customer is told whenever a credential is minted against
    # their Organization — including when platform staff do it under an approved support
    # session, which is precisely the case they most need to see.
    developer_comms.notify_credential_created(db, background, org=org, creator=admin,
                                              key_id=created.id)
    _audit(db, admin, request, "api_key.create", target_type="api_key",
           target_id=created.id, org_id=org_id,
           meta={"label": data.label, "prefix": created.prefix,
                 "expires_in_days": data.expires_in_days})
    return created


@router.delete("/organizations/{org_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(org_id: uuid.UUID, key_id: str, request: Request,
                   db: Session = Depends(get_db),
                     ctx: SupportContext = Depends(support_context), admin: User = Depends(require_super_admin)):
    ctx.authorize(org_id, tenant_access.CAP_CREDENTIALS_WRITE,
                  resource="api_key", resource_id=org_id)
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    if not crud.revoke_api_key(db, org, key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    _audit(db, admin, request, "api_key.revoke", target_type="api_key", target_id=key_id, org_id=org_id)


# ── Support case lifecycle (ZST-EC-001 SUP-002 / SUP-003 / SUP-004, staff side) ─────────
# These stay on the `/admin` router and therefore keep its router-level require_super_admin
# guard - Super Admin capability is unchanged by this family, only the CUSTOMER side was
# added (routers/organization.py). Each route commits state first and notifies after, so a
# Resend outage cannot roll back a case transition.

@router.post("/support-tickets/{ticket_id}/update", response_model=SupportTicketOut)
def post_support_case_update(ticket_id: uuid.UUID, data: SupportCaseUpdateIn,
                             request: Request, background: BackgroundTasks,
                             db: Session = Depends(get_db),
                             admin: User = Depends(require_super_admin)):
    """Post a CUSTOMER-VISIBLE update.

    Separate from PATCH /support-tickets/{id} on purpose: that route edits staff-side fields
    and stays silent, which is what SUP-002 requires. Only this one mails the customer, and
    only `customer_update` is rendered - `internal_notes` has no route into any template.
    """
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if not support_comms.post_update(db, ticket, customer_update=data.customer_update,
                                     status=data.status,
                                     next_update_at=data.next_update_at):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "customer_update must not be empty")
    _audit(db, admin, request, "support_ticket.customer_update",
           target_type="support_ticket", target_id=ticket_id, org_id=ticket.org_id)
    support_comms.notify_case_update(db, background, ticket)
    return ticket


@router.post("/support-tickets/{ticket_id}/request-action", response_model=SupportTicketOut)
def request_support_case_action(ticket_id: uuid.UUID, data: SupportActionRequestIn,
                                request: Request, background: BackgroundTasks,
                                db: Session = Depends(get_db),
                                admin: User = Depends(require_super_admin)):
    """Move a case to WAITING_FOR_CUSTOMER with a specific requested action."""
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if not support_comms.request_customer_action(db, ticket, action=data.action,
                                                 due_at=data.due_at):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "action must not be empty")
    _audit(db, admin, request, "support_ticket.request_action",
           target_type="support_ticket", target_id=ticket_id, org_id=ticket.org_id)
    support_comms.notify_action_required(db, background, ticket)
    return ticket


@router.post("/support-tickets/{ticket_id}/escalate", response_model=SupportTicketOut)
def escalate_support_case(ticket_id: uuid.UUID, data: SupportEscalateIn, request: Request,
                          background: BackgroundTasks, db: Session = Depends(get_db),
                          admin: User = Depends(require_super_admin)):
    """Record a real escalation.

    `next_update_at` is stored only when supplied. Absent means the message says "we will
    update the case when new information is available" rather than inventing an SLA.
    """
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    escalation = support_comms.escalate(
        db, ticket, level=data.level, reason_category=data.reason_category,
        owner_after=data.owner_after, next_update_at=data.next_update_at,
        escalated_by=admin.id)
    if escalation is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"level must be 1-3 and reason_category one of {list(ESCALATION_REASONS)}")
    _audit(db, admin, request, "support_ticket.escalate", target_type="support_ticket",
           target_id=ticket_id, org_id=ticket.org_id,
           meta={"level": data.level, "reason": data.reason_category})
    support_comms.notify_escalated(db, background, ticket, escalation)
    return ticket


@router.post("/support-tickets/{ticket_id}/owner", response_model=SupportTicketOut)
def change_support_case_owner(ticket_id: uuid.UUID, data: SupportOwnerChangeIn,
                              request: Request, background: BackgroundTasks,
                              db: Session = Depends(get_db),
                              admin: User = Depends(require_super_admin)):
    """Change the customer-facing owning TEAM.

    A reassignment to the same team is a no-op and sends nothing - which is how an internal
    queue move stays internal.
    """
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    changed, previous = support_comms.change_owner(db, ticket, owner_after=data.owner)
    if not changed:
        return ticket
    _audit(db, admin, request, "support_ticket.owner_change",
           target_type="support_ticket", target_id=ticket_id, org_id=ticket.org_id,
           meta={"previous": previous, "current": data.owner})
    support_comms.notify_owner_changed(db, background, ticket, previous=previous)
    return ticket


@router.post("/support-tickets/{ticket_id}/link-incident", response_model=SupportTicketOut)
def link_support_case_incident(ticket_id: uuid.UUID, data: SupportIncidentLinkIn,
                               request: Request, background: BackgroundTasks,
                               db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Link a case to a REAL platform incident.

    Reuses models/platform_ops.Incident rather than creating a second incident domain. Only
    the incident's customer-safe `ref`, its status and a generic impact line are ever mailed.
    """
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    incident = db.get(Incident, data.incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    if not support_comms.link_incident(db, ticket, incident):
        raise HTTPException(status.HTTP_409_CONFLICT, "That incident is already linked")
    _audit(db, admin, request, "support_ticket.link_incident",
           target_type="support_ticket", target_id=ticket_id, org_id=ticket.org_id,
           meta={"incident_ref": incident.ref})
    support_comms.notify_incident_linked(db, background, ticket)
    return ticket


@router.post("/support-tickets/{ticket_id}/resolve", response_model=SupportTicketOut)
def resolve_support_case(ticket_id: uuid.UUID, data: SupportResolveIn, request: Request,
                         background: BackgroundTasks, db: Session = Depends(get_db),
                         admin: User = Depends(require_super_admin)):
    """Resolve a case, then request feedback only if eligibility allows it."""
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if not support_comms.resolve(db, ticket, summary=data.summary,
                                 customer_action_remains=data.customer_action_remains):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A case that is {ticket.status} cannot be resolved")
    _audit(db, admin, request, "support_ticket.resolve", target_type="support_ticket",
           target_id=ticket_id, org_id=ticket.org_id)
    support_comms.notify_resolved(db, background, ticket)
    # Gated on the stored sensitivity classification, not on the wording of the case.
    if data.request_feedback:
        support_comms.notify_feedback_request(db, background, ticket)
    return ticket


@router.post("/support-tickets/{ticket_id}/close", response_model=SupportTicketOut)
def close_support_case(ticket_id: uuid.UUID, data: SupportCloseIn, request: Request,
                       background: BackgroundTasks, db: Session = Depends(get_db),
                       admin: User = Depends(require_super_admin)):
    """Close a case. A distinct state from resolved, with its own notice."""
    ticket = crud.get_support_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if not support_comms.close(db, ticket):
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is already closed")
    _audit(db, admin, request, "support_ticket.close", target_type="support_ticket",
           target_id=ticket_id, org_id=ticket.org_id)
    support_comms.notify_closed(db, background, ticket)
    if data.request_feedback:
        support_comms.notify_feedback_request(db, background, ticket)
    return ticket


# ── Trust Center: advisories, evidence, disclosure (ZST-EC-001 TRU-001 -> TRU-003) ───────
#
# The OPERATOR half. Everything here is behind require_super_admin (the whole router is),
# every state change is audited, and every publication commits before any fan-out.
#
# `approved_by=admin.id` is passed explicitly on each publish/approve path rather than
# defaulted, because the services REFUSE to publish an advisory or approve evidence access
# without a named approver - that is the mechanism that stops an approval being fabricated.


@router.post("/trust/advisories", status_code=status.HTTP_201_CREATED)
def draft_advisory(data: AdvisoryDraftIn, request: Request,
                   db: Session = Depends(get_db),
                   admin: User = Depends(require_super_admin)):
    """Draft an advisory. Sends nothing - a draft has no audience."""
    advisory = trust_center.create_advisory(
        db, title=data.title, severity=data.severity, summary=data.summary,
        affected_components=data.affected_components,
        customer_impact=data.customer_impact,
        immediate_mitigation=data.immediate_mitigation,
        affected_versions=data.affected_versions,
        affected_scope_note=data.affected_scope_note,
        cvss_vector=data.cvss_vector,
        workaround_available=data.workaround_available,
        workaround_summary=data.workaround_summary,
        internal_incident_id=data.internal_incident_id,
        vulnerability_report_id=data.vulnerability_report_id,
        created_by=admin.id)
    if advisory is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An advisory needs an approved severity, a summary, at least one known "
            "affected component, and a workaround summary if a workaround is claimed")
    _audit(db, admin, request, "trust.advisory.draft", target_type="security_advisory",
           target_id=advisory.id)
    return {"id": str(advisory.id), "reference": advisory.public_reference,
            "status": advisory.status}


@router.post("/trust/advisories/{advisory_id}/impacts")
def record_advisory_impact(advisory_id: uuid.UUID, data: AdvisoryImpactIn,
                           request: Request, db: Session = Depends(get_db),
                           admin: User = Depends(require_super_admin)):
    """Map one organization to an advisory, with the recorded basis for saying so.

    This is the ONLY thing that makes a tenant an "affected customer". Without a row here an
    advisory reaches subscribed verified security contacts and nobody is told the advisory
    applies to them specifically.
    """
    advisory = db.get(SecurityAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Advisory not found")
    impact = trust_center.record_impact(
        db, advisory, org_id=data.org_id, basis=data.basis,
        evidence_note=data.evidence_note, affected_versions=data.affected_versions,
        recorded_by=admin.id)
    if impact is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A known organization and a recorded impact basis are required")
    _audit(db, admin, request, "trust.advisory.impact", target_type="security_advisory",
           target_id=advisory_id, org_id=data.org_id, meta={"basis": data.basis})
    return {"advisory": advisory.public_reference, "org_id": str(data.org_id),
            "basis": impact.basis}


@router.post("/trust/advisories/{advisory_id}/publish")
def publish_advisory(advisory_id: uuid.UUID, request: Request,
                     background: BackgroundTasks, db: Session = Depends(get_db),
                     admin: User = Depends(require_super_admin)):
    """Publish a draft and notify. The publication commits first; mail is best-effort."""
    advisory = db.get(SecurityAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Advisory not found")
    if not trust_center.publish(db, advisory, approved_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only a draft advisory can be published")
    _audit(db, admin, request, "trust.advisory.publish", target_type="security_advisory",
           target_id=advisory_id)
    kind = trust_center.notify_advisory(db, background, advisory)
    return {"reference": advisory.public_reference, "status": advisory.status,
            "version": advisory.version, "notified": kind}


@router.post("/trust/advisories/{advisory_id}/update")
def update_advisory(advisory_id: uuid.UUID, data: AdvisoryUpdateIn, request: Request,
                    background: BackgroundTasks, db: Session = Depends(get_db),
                    admin: User = Depends(require_super_admin)):
    """Materially update a published advisory by APPENDING a version.

    Nothing is overwritten. A cosmetic edit that changes no material field is refused rather
    than mailed - `publish_update` returns None and the response says so.
    """
    advisory = db.get(SecurityAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Advisory not found")
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items()
               if k != "change_summary"}
    version = trust_center.publish_update(
        db, advisory, changes=changes, change_summary=data.change_summary,
        published_by=admin.id)
    if version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nothing material changed, or this advisory cannot be updated in its current "
            "state")
    _audit(db, admin, request, "trust.advisory.update", target_type="security_advisory",
           target_id=advisory_id, meta={"version": version.version,
                                        "changed": version.changed_fields})
    kind = trust_center.notify_advisory(db, background, advisory, version=version)
    return {"reference": advisory.public_reference, "version": version.version,
            "changed_fields": version.changed_fields, "notified": kind}


@router.post("/trust/advisories/{advisory_id}/remediation")
def advisory_remediation(advisory_id: uuid.UUID, data: AdvisoryRemediationIn,
                         request: Request, background: BackgroundTasks,
                         db: Session = Depends(get_db),
                         admin: User = Depends(require_super_admin)):
    """Record that remediation actually exists, and notify.

    `action_mandatory` is what unlocks imperative wording; a deadline is quoted only when a
    real one is supplied. Neither is inferred from severity.
    """
    advisory = db.get(SecurityAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Advisory not found")
    if not trust_center.mark_remediation_available(
            db, advisory, fixed_version=data.fixed_version,
            remediation_steps=data.remediation_steps,
            remediation_deadline=data.remediation_deadline,
            action_mandatory=data.action_mandatory, published_by=admin.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Remediation needs real steps and a published advisory to attach them to")
    _audit(db, admin, request, "trust.advisory.remediation",
           target_type="security_advisory", target_id=advisory_id,
           meta={"mandatory": data.action_mandatory})
    kind = trust_center.notify_advisory(db, background, advisory)
    return {"reference": advisory.public_reference, "status": advisory.status,
            "notified": kind}


@router.post("/trust/advisories/{advisory_id}/close")
def close_advisory(advisory_id: uuid.UUID, data: AdvisoryCloseIn, request: Request,
                   background: BackgroundTasks, db: Session = Depends(get_db),
                   admin: User = Depends(require_super_admin)):
    """Close an advisory. The notice says closure is about the ADVISORY, not the estate."""
    advisory = db.get(SecurityAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Advisory not found")
    if not trust_center.close_advisory(db, advisory, closure_note=data.closure_note,
                                       published_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This advisory cannot be closed in its current state")
    _audit(db, admin, request, "trust.advisory.close", target_type="security_advisory",
           target_id=advisory_id)
    kind = trust_center.notify_advisory(db, background, advisory)
    return {"reference": advisory.public_reference, "status": advisory.status,
            "notified": kind}


@router.post("/trust/documents", status_code=status.HTTP_201_CREATED)
def register_trust_document(data: TrustDocumentIn, request: Request,
                            db: Session = Depends(get_db),
                            admin: User = Depends(require_super_admin)):
    """Register an evidence document. Contents go to private storage, never a column."""
    doc = trust_center.create_document(
        db, title=data.title, document_type=data.document_type, version=data.version,
        classification=data.classification, allowed_purposes=data.allowed_purposes,
        allowed_scopes=data.allowed_scopes, content=data.content,
        content_type=data.content_type, expires_at=data.expires_at, created_by=admin.id)
    if doc is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A document needs a known type, a classification, and at least one allowed "
            "purpose and scope")
    _audit(db, admin, request, "trust.document.register",
           target_type="trust_document", target_id=doc.id,
           meta={"classification": doc.classification})
    return {"id": str(doc.id), "status": doc.status,
            "classification": doc.classification}


@router.get("/trust/evidence/requests")
def list_evidence_requests(db: Session = Depends(get_db)):
    rows = db.scalars(select(TrustEvidenceRequest).order_by(
        TrustEvidenceRequest.requested_at.desc()).limit(200)).all()
    return [{"id": str(r.id), "reference": r.reference,
             "requester_email": r.requester_email, "company_name": r.company_name,
             "document_id": str(r.document_id), "purpose": r.purpose, "scope": r.scope,
             "status": r.status, "qualification_basis": r.qualification_basis,
             # Says whether the claim was CHECKED against something real or merely asserted,
             # so a reviewer knows which requests need more diligence.
             "qualification_verified": r.qualification_verified,
             "requested_at": trust_center.as_utc(r.requested_at),
             "access_expires_at": trust_center.as_utc(r.access_expires_at),
             "access_count": r.access_count} for r in rows]


@router.post("/trust/evidence/requests/{request_id}/approve")
def approve_evidence_request(request_id: uuid.UUID, data: EvidenceDecisionIn,
                             request: Request, background: BackgroundTasks,
                             db: Session = Depends(get_db),
                             admin: User = Depends(require_super_admin)):
    """Approve access and email the bound, expiring link. The document is not attached."""
    evidence_request = db.get(TrustEvidenceRequest, request_id)
    if evidence_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    token = trust_center.approve_request(
        db, evidence_request, approved_by=admin.id,
        ttl_hours=data.ttl_hours or EVIDENCE_ACCESS_TTL_HOURS,
        decision_note=data.decision_note)
    if token is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This request cannot be approved - check its state and that the document still "
            "allows the requested purpose and scope")
    _audit(db, admin, request, "trust.evidence.approve",
           target_type="trust_evidence_request", target_id=request_id,
           meta={"purpose": evidence_request.purpose, "scope": evidence_request.scope})
    trust_center.notify_access_approved(db, background, evidence_request, token)
    return {"reference": evidence_request.reference, "status": evidence_request.status,
            "access_expires_at": trust_center.as_utc(evidence_request.access_expires_at)}


@router.post("/trust/evidence/requests/{request_id}/deny")
def deny_evidence_request(request_id: uuid.UUID, data: EvidenceDecisionIn,
                          request: Request, background: BackgroundTasks,
                          db: Session = Depends(get_db),
                          admin: User = Depends(require_super_admin)):
    evidence_request = db.get(TrustEvidenceRequest, request_id)
    if evidence_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if not data.decision_note:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A denial needs a customer-safe reason")
    if not trust_center.deny_request(db, evidence_request, denied_by=admin.id,
                                     decision_note=data.decision_note):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This request has already been decided")
    _audit(db, admin, request, "trust.evidence.deny",
           target_type="trust_evidence_request", target_id=request_id)
    trust_center.notify_request_denied(db, background, evidence_request)
    return {"reference": evidence_request.reference, "status": evidence_request.status}


@router.post("/trust/evidence/requests/{request_id}/revoke")
def revoke_evidence_access(request_id: uuid.UUID, data: EvidenceDecisionIn,
                           request: Request, background: BackgroundTasks,
                           db: Session = Depends(get_db),
                           admin: User = Depends(require_super_admin)):
    """Revoke approved access. The token hash is cleared, so the link dies immediately."""
    evidence_request = db.get(TrustEvidenceRequest, request_id)
    if evidence_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if not trust_center.revoke_access(db, evidence_request, revoked_by=admin.id,
                                      reason=data.decision_note):
        raise HTTPException(status.HTTP_409_CONFLICT, "This access is not active")
    _audit(db, admin, request, "trust.evidence.revoke",
           target_type="trust_evidence_request", target_id=request_id)
    trust_center.notify_access_ended(db, background, evidence_request)
    return {"reference": evidence_request.reference, "status": evidence_request.status}


@router.get("/trust/evidence/requests/{request_id}/access-log")
def read_evidence_access_log(request_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every authorization decision on this request, authorized and refused alike.

    Records who, what, when and the outcome. Never any document content.
    """
    evidence_request = db.get(TrustEvidenceRequest, request_id)
    if evidence_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    return [{"at": trust_center.as_utc(a.at), "requester_email": a.requester_email,
             "document_id": str(a.document_id), "purpose": a.purpose, "scope": a.scope,
             "outcome": a.outcome, "client": a.client, "ip": a.ip}
            for a in trust_center.access_log(db, evidence_request)]


@router.get("/trust/security/reports")
def list_vulnerability_reports(db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Triage queue.

    Reporter identity is included ONLY through `vuln_disclosure.reporter_identity`, which
    checks the role again rather than trusting this router's own dependency. An operator
    without a security role sees the safe projection and nothing else.
    """
    rows = db.scalars(select(VulnerabilityReport).order_by(
        VulnerabilityReport.received_at.desc()).limit(200)).all()
    out = []
    for report in rows:
        entry = vuln_disclosure.public_projection(report)
        identity = vuln_disclosure.reporter_identity(report, actor=admin)
        if identity is not None:
            entry["reporter"] = identity
        entry["id"] = str(report.id)
        out.append(entry)
    return out


@router.post("/trust/security/reports/{report_id}/acknowledge")
def acknowledge_vulnerability_report(report_id: uuid.UUID, data: VulnLifecycleIn,
                                     request: Request, background: BackgroundTasks,
                                     db: Session = Depends(get_db),
                                     admin: User = Depends(require_super_admin)):
    """Acknowledge receipt. Not a validity judgement, and the copy says so."""
    report = db.get(VulnerabilityReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not vuln_disclosure.acknowledge(db, report, note=data.safe_update):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This report has already been acknowledged")
    _audit(db, admin, request, "trust.vuln.acknowledge",
           target_type="vulnerability_report", target_id=report_id)
    history = vuln_disclosure.reporter_history(db, report)
    vuln_disclosure.notify_reporter(db, background, report, history[-1])
    return vuln_disclosure.public_projection(report)


@router.post("/trust/security/reports/{report_id}/triage")
def triage_vulnerability_report(report_id: uuid.UUID, request: Request,
                                db: Session = Depends(get_db),
                                admin: User = Depends(require_super_admin)):
    """Internal triage. Deliberately silent - triage is not a researcher-facing event."""
    report = db.get(VulnerabilityReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not vuln_disclosure.triage(db, report, triaged_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This report cannot be triaged in its current state")
    _audit(db, admin, request, "trust.vuln.triage",
           target_type="vulnerability_report", target_id=report_id)
    return vuln_disclosure.public_projection(report)


@router.post("/trust/security/reports/{report_id}/coordinate")
def coordinate_vulnerability_report(report_id: uuid.UUID, data: VulnCoordinateIn,
                                    request: Request, background: BackgroundTasks,
                                    db: Session = Depends(get_db),
                                    admin: User = Depends(require_super_admin)):
    """Begin coordinated remediation.

    Coordination fields are stored only if real ones are supplied. With no coordinated
    disclosure policy configured they stay null, and the researcher message then mentions
    no date, embargo or credit at all.
    """
    report = db.get(VulnerabilityReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not vuln_disclosure.start_coordination(
            db, report, safe_update=data.safe_update,
            disclosure_date=data.disclosure_date, embargo_until=data.embargo_until,
            remediation_target=data.remediation_target,
            public_credit_preference=data.public_credit_preference):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This report must be triaged before coordination")
    _audit(db, admin, request, "trust.vuln.coordinate",
           target_type="vulnerability_report", target_id=report_id)
    history = vuln_disclosure.reporter_history(db, report)
    vuln_disclosure.notify_reporter(db, background, report, history[-1])
    return vuln_disclosure.public_projection(report)


@router.post("/trust/security/reports/{report_id}/remediated")
def remediate_vulnerability_report(report_id: uuid.UUID, data: VulnLifecycleIn,
                                   request: Request, background: BackgroundTasks,
                                   db: Session = Depends(get_db),
                                   admin: User = Depends(require_super_admin)):
    """Record remediation. Refused from any state before coordination."""
    report = db.get(VulnerabilityReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not data.safe_update:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A remediation update needs a researcher-safe statement")
    if not vuln_disclosure.mark_remediated(db, report, safe_update=data.safe_update,
                                           advisory_id=data.advisory_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Remediation can only be recorded for a report under coordination")
    _audit(db, admin, request, "trust.vuln.remediated",
           target_type="vulnerability_report", target_id=report_id)
    history = vuln_disclosure.reporter_history(db, report)
    vuln_disclosure.notify_reporter(db, background, report, history[-1])
    return vuln_disclosure.public_projection(report)


@router.post("/trust/security/reports/{report_id}/close")
def close_vulnerability_report(report_id: uuid.UUID, data: VulnCloseIn, request: Request,
                               background: BackgroundTasks,
                               db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    report = db.get(VulnerabilityReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    if not vuln_disclosure.close_report(db, report, resolution=data.resolution,
                                        safe_update=data.safe_update):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A known resolution and a researcher-safe statement are needed")
    _audit(db, admin, request, "trust.vuln.close",
           target_type="vulnerability_report", target_id=report_id,
           meta={"resolution": data.resolution})
    history = vuln_disclosure.reporter_history(db, report)
    vuln_disclosure.notify_reporter(db, background, report, history[-1])
    return vuln_disclosure.public_projection(report)


# ── Marketing: digests, announcements, webinars (ZST-EC-001 MKT-001 -> MKT-004) ──────────
#
# Approval lives here and only here. Nothing in services/marketing.py can send a digest or
# an announcement that a named person did not approve, and every recipient still has to hold
# the matching consent - approval and consent are independent gates and both are required.


@router.post("/marketing/releases/{release_id}/approve")
def approve_release_for_customers(release_id: uuid.UUID, data: ReleaseApprovalIn,
                                  request: Request, db: Session = Depends(get_db),
                                  admin: User = Depends(require_super_admin)):
    """Make one release distributable, with a summary written for customers.

    `Release.notes` stays internal and is never what goes out.
    """
    release = db.get(Release, release_id)
    if release is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Release not found")
    if not marketing.approve_release(
            db, release, customer_summary=data.customer_summary, approved_by=admin.id,
            documentation_path=data.documentation_path,
            rollout_status=data.rollout_status):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A customer-facing summary is required")
    _audit(db, admin, request, "marketing.release.approve", target_type="release",
           target_id=release_id)
    return {"id": str(release.id), "customer_visible": release.customer_visible}


@router.post("/marketing/digests", status_code=status.HTTP_201_CREATED)
def draft_release_digest(data: DigestDraftIn, request: Request,
                         db: Session = Depends(get_db),
                         admin: User = Depends(require_super_admin)):
    """Draft a digest. Unapproved releases are silently excluded, and an empty one refused."""
    digest = marketing.create_digest(
        db, title=data.title, period_start=data.period_start,
        period_end=data.period_end, release_ids=data.release_ids, summary=data.summary)
    if digest is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A digest needs a valid period and at least one approved, customer-visible "
            "release")
    _audit(db, admin, request, "marketing.digest.draft", target_type="release_digest",
           target_id=digest.id)
    return {"id": str(digest.id), "status": digest.status,
            "release_ids": digest.release_ids}


@router.post("/marketing/digests/{digest_id}/approve")
def approve_release_digest(digest_id: uuid.UUID, request: Request,
                           db: Session = Depends(get_db),
                           admin: User = Depends(require_super_admin)):
    digest = db.get(ReleaseDigest, digest_id)
    if digest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Digest not found")
    if not marketing.approve_digest(db, digest, approved_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft digest can be approved")
    _audit(db, admin, request, "marketing.digest.approve", target_type="release_digest",
           target_id=digest_id)
    return {"id": str(digest.id), "status": digest.status}


@router.post("/marketing/digests/{digest_id}/publish")
def publish_release_digest(digest_id: uuid.UUID, request: Request,
                           background: BackgroundTasks, db: Session = Depends(get_db),
                           admin: User = Depends(require_super_admin)):
    """Publish an approved digest and fan out to RELEASE_NOTES subscribers only."""
    digest = db.get(ReleaseDigest, digest_id)
    if digest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Digest not found")
    if not marketing.publish_digest(db, digest, published_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Only an approved digest can be published")
    _audit(db, admin, request, "marketing.digest.publish", target_type="release_digest",
           target_id=digest_id)
    queued = marketing.notify_digest(db, background, digest)
    return {"id": str(digest.id), "status": digest.status, "queued": queued}


@router.post("/marketing/features")
def set_feature_availability(data: FeatureAvailabilityIn, request: Request,
                             db: Session = Depends(get_db),
                             admin: User = Depends(require_super_admin)):
    """Record authoritative rollout state. The announcement copy derives from this."""
    availability = marketing.set_availability(
        db, feature_key=data.feature_key, feature_name=data.feature_name,
        lifecycle=data.lifecycle, customer_summary=data.customer_summary,
        eligible_plans=data.eligible_plans, eligible_regions=data.eligible_regions,
        rollout_percentage=data.rollout_percentage,
        documentation_path=data.documentation_path, effective_at=data.effective_at)
    if availability is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "A known lifecycle value is required")
    _audit(db, admin, request, "marketing.feature.availability",
           target_type="feature_availability", target_id=availability.id,
           meta={"lifecycle": availability.lifecycle})
    return {"id": str(availability.id), "feature_key": availability.feature_key,
            "lifecycle": availability.lifecycle}


@router.post("/marketing/features/{availability_id}/grants")
def grant_feature_availability(availability_id: uuid.UUID, data: FeatureGrantIn,
                               request: Request, db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Record that one organization can actually use a targeted feature."""
    availability = db.get(FeatureAvailability, availability_id)
    if availability is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature not found")
    grant = marketing.grant_availability(db, availability, org_id=data.org_id,
                                         granted_by=admin.id)
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    _audit(db, admin, request, "marketing.feature.grant",
           target_type="feature_availability", target_id=availability_id,
           org_id=data.org_id)
    return {"feature_key": availability.feature_key, "org_id": str(data.org_id)}


@router.post("/marketing/features/{availability_id}/announcements",
             status_code=status.HTTP_201_CREATED)
def draft_feature_announcement(availability_id: uuid.UUID, data: AnnouncementDraftIn,
                               request: Request, db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Draft an announcement, freezing the lifecycle it describes."""
    availability = db.get(FeatureAvailability, availability_id)
    if availability is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feature not found")
    announcement = marketing.create_announcement(
        db, availability, body=data.body, headline=data.headline)
    if announcement is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A body is required")
    _audit(db, admin, request, "marketing.announcement.draft",
           target_type="feature_announcement", target_id=announcement.id)
    return {"id": str(announcement.id), "lifecycle": announcement.lifecycle,
            "status": announcement.status,
            "subject": marketing.announcement_subject(announcement, availability)}


@router.post("/marketing/announcements/{announcement_id}/send")
def send_feature_announcement(announcement_id: uuid.UUID, request: Request,
                              background: BackgroundTasks,
                              db: Session = Depends(get_db),
                              admin: User = Depends(require_super_admin)):
    """Approve and send. Recipients must be BOTH subscribed AND eligible."""
    announcement = db.get(FeatureAnnouncement, announcement_id)
    if announcement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Announcement not found")
    if announcement.status == "draft" and not marketing.approve_announcement(
            db, announcement, approved_by=admin.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "This announcement cannot be approved")
    _audit(db, admin, request, "marketing.announcement.send",
           target_type="feature_announcement", target_id=announcement_id,
           meta={"lifecycle": announcement.lifecycle})
    queued = marketing.notify_announcement(db, background, announcement)
    return {"id": str(announcement.id), "lifecycle": announcement.lifecycle,
            "queued": queued}


@router.post("/marketing/webinars", status_code=status.HTTP_201_CREATED)
def create_webinar(data: WebinarIn, request: Request, db: Session = Depends(get_db),
                   admin: User = Depends(require_super_admin)):
    webinar = marketing.schedule_webinar(
        db, title=data.title, starts_at_utc=data.starts_at_utc,
        description=data.description, duration_minutes=data.duration_minutes,
        join_path=data.join_path)
    if webinar is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A start time is required")
    _audit(db, admin, request, "marketing.webinar.create",
           target_type="marketing_webinar", target_id=webinar.id)
    return {"id": str(webinar.id), "reference": webinar.reference,
            "starts_at_utc": marketing.as_utc(webinar.starts_at_utc)}


@router.post("/marketing/webinars/{webinar_id}/reschedule")
def reschedule_webinar(webinar_id: uuid.UUID, data: WebinarRescheduleIn, request: Request,
                       background: BackgroundTasks, db: Session = Depends(get_db),
                       admin: User = Depends(require_super_admin)):
    """Move a session and tell everybody who registered. The old time stays visible."""
    webinar = db.get(MarketingWebinar, webinar_id)
    if webinar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    changed, previous = marketing.reschedule_webinar(
        db, webinar, starts_at_utc=data.starts_at_utc)
    if not changed:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "That is already the time, or this session has finished")
    _audit(db, admin, request, "marketing.webinar.reschedule",
           target_type="marketing_webinar", target_id=webinar_id)
    queued = marketing.notify_webinar(db, background, webinar, variant="rescheduled",
                                      previous_start=previous)
    return {"reference": webinar.reference,
            "starts_at_utc": marketing.as_utc(webinar.starts_at_utc), "queued": queued}


@router.post("/marketing/webinars/{webinar_id}/cancel")
def cancel_webinar(webinar_id: uuid.UUID, request: Request, background: BackgroundTasks,
                   db: Session = Depends(get_db),
                   admin: User = Depends(require_super_admin)):
    webinar = db.get(MarketingWebinar, webinar_id)
    if webinar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if not marketing.cancel_webinar(db, webinar):
        raise HTTPException(status.HTTP_409_CONFLICT, "This session is already finished")
    _audit(db, admin, request, "marketing.webinar.cancel",
           target_type="marketing_webinar", target_id=webinar_id)
    queued = marketing.notify_webinar(db, background, webinar, variant="cancelled")
    return {"reference": webinar.reference, "status": webinar.status, "queued": queued}


@router.post("/marketing/webinars/{webinar_id}/remind")
def remind_webinar_registrants(webinar_id: uuid.UUID, request: Request,
                               background: BackgroundTasks,
                               db: Session = Depends(get_db),
                               admin: User = Depends(require_super_admin)):
    """Send a reminder.

    Operator-triggered on purpose: no reminder-threshold policy is configured, and inventing
    one ("24 hours before") would be exactly the fabrication STS-005 already refuses.
    """
    webinar = db.get(MarketingWebinar, webinar_id)
    if webinar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    _audit(db, admin, request, "marketing.webinar.remind",
           target_type="marketing_webinar", target_id=webinar_id)
    queued = marketing.notify_webinar(db, background, webinar, variant="reminder")
    return {"reference": webinar.reference, "queued": queued}


@router.post("/marketing/webinars/{webinar_id}/complete")
def complete_webinar(webinar_id: uuid.UUID, data: WebinarCompleteIn, request: Request,
                     background: BackgroundTasks, db: Session = Depends(get_db),
                     admin: User = Depends(require_super_admin)):
    """Mark a session delivered and optionally send the APPROVED follow-up.

    The follow-up is marketing: it needs this approval AND each recipient's own
    LIVE_EVENT_EDUCATION consent. Attendance is not consent and is not consulted.
    """
    webinar = db.get(MarketingWebinar, webinar_id)
    if webinar is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if not marketing.complete_webinar(
            db, webinar, followup_body=data.followup_body,
            followup_approved_by=admin.id if data.followup_body else None):
        raise HTTPException(status.HTTP_409_CONFLICT, "This session is already finished")
    _audit(db, admin, request, "marketing.webinar.complete",
           target_type="marketing_webinar", target_id=webinar_id)
    queued = (marketing.notify_webinar_followup(db, background, webinar)
              if data.followup_body else 0)
    return {"reference": webinar.reference, "status": webinar.status,
            "followup_queued": queued}
