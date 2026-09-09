"""Org self-service API — the backend for the Organization Profile & Settings pages.

Isolation: the target org is ALWAYS the caller's `user.org_id` (from the JWT); org_id is
never read from the request body. Super admin isn't blocked — it operates on its own org
here and uses /admin/* for cross-org management. Reads: any member. Writes + sensitive
reads (security, developer): org admin (require_org_admin already admits super_admin)."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import (Header, APIRouter, BackgroundTasks, Depends, HTTPException,
                     Query, Request, Response, status)
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..config import settings
from ..crud import admin as admin_crud
from ..crud import delivery as delivery_crud
from ..crud import event as event_crud
from ..crud import organization as crud
from ..db import get_db
from ..email import UnsafeLinkError
from ..services import account_lifecycle as lifecycle
from ..services import payments as payment_svc
from ..models.plan import Plan
from ..models.subscription import normalize_subscription_state
from ..models import (
    ABUSE_CATEGORIES,
    ABUSE_SUBJECT_TYPES,
    CASE_CATEGORIES,
    CASE_CATEGORY_LABELS,
    CASE_SENSITIVITIES,
    EXPORT_TYPES,
    SCOPE_ORGANIZATION,
    DeveloperDataExport,
    AbuseReport,
    OrganizationSecurityContact,
    SupportTicket,
    WEBHOOK_VERIFIED,
    STEP_UP_HIGH_RISK_ROLE_GRANT,
    STEP_UP_OWNERSHIP_TRANSFER,
    AccessReview,
    AccessReviewAssignment,
    Event,
    LiveIngressEndpoint,
    Organization,
    NotificationPreferenceEvent,
    OwnershipTransfer,
    SupportAccessRequest,
    User,
    WEBHOOK_EVENTS,
)
from ..schemas.admin import (
    AdminUserOut,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    Page,
    PlanOut,
    UserUpdate,
)
from ..schemas.auth import TokenOut, UserOut
from ..schemas.organization import (
    AbuseReportCreate,
    AccessReviewAssignmentOut,
    SecurityContactCreate,
    SecurityContactVerify,
    SupportCaseCreate,
    SupportParticipantAdd,
    SupportReopen,
    AccessReviewCreate,
    AccessReviewDecisionIn,
    AccessReviewOut,
    OwnershipTransferCreate,
    OwnershipTransferOut,
    NotificationCatalogItem,
    SubscriptionCheckoutCreate,
    SupportAccessCustomerOut,
    SupportAccessDecision,
    InvitationAccept,
    InvitationAction,
    InvitationCreate,
    DeliveryCreate,
    DeliveryCreated,
    DeliveryOut,
    EventReportOut,
    InvitationOut,
    InvitationPreview,
    InvitationReject,
    LiveInputCreate,
    LiveInputKeyOut,
    LiveInputOut,
    OrgBrandingOut,
    OrgBrandingUpdate,
    OrgDeveloperOut,
    OrgDomainOut,
    OrgDomainUpdate,
    OrgMeOut,
    OrgNotifications,
    OrgProfileOut,
    OrgProfileUpdate,
    OrgSecurity,
    RecordingExportEligibility,
    RecordingOut,
    WebhookDeliveryOut,
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointOut,
    WebhookEndpointUpdate,
    WebhookVerificationOut,
    DeveloperExportCreate,
    RetentionExtensionCreate,
    RetentionStateOut,
    WebhookVerifyIn,
    WebhookSecretOut,
)
from ..security import create_access_token, get_current_user, hash_password, require_org_admin
from ..services import delivery as delivery_svc
from ..services import livekit, org as org_svc
from ..services import credential_lifecycle
from ..services import developer_comms
from ..services import developer_export
from ..services import signing_rotation
from ..services import media_comms
from ..services import media_retention
from ..services import webhook_lifecycle
from ..services import webhook_security
from ..services import notifications as notif_svc
from ..services import org_policy
from ..services import stepup as stepup_svc
from ..services import commerce_comms
from ..services import security_comms
from ..services import support_comms
from ..services import org_comms
from ..services import org_governance as governance
from ..services import support_access as support_svc
from ..services import report as report_svc

# Roles an org admin may assign/invite. Excludes super_admin (platform-only, never via this API).
ORG_ASSIGNABLE_ROLES = ("org_admin", "host", "speaker", "viewer")

log = logging.getLogger(__name__)

router = APIRouter(prefix="/organization", tags=["organization"])


def get_my_org(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Organization:
    """The caller's own organization, resolved from the token. The single point where
    org_id is bound — nothing downstream accepts it from the client."""
    org = db.get(Organization, user.org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return org


def get_my_org_admin(
    _: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org),
) -> Organization:
    """get_my_org gated to org admin (and above) for mutations/sensitive reads. Reuses the
    same lookup — require_org_admin just adds the 403 gate (super admin passes)."""
    return org


# ── Overview (dashboard) ──────────────────────────────────────────────────────

@router.get("/overview")
def overview(
    range_: str = Query("24h", alias="range", pattern="^(1h|24h|7d|30d)$"),
    workspace: str | None = Query(None, description="workspace slug; only 'production' exists"),
    include_test: bool = Query(True),
    org: Organization = Depends(get_my_org),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Whole-page payload for /organization/dashboard, scoped to the caller's own org.

    Readable by any member: it reports on the organization the caller already belongs to and
    carries no cross-tenant or platform-wide figures. `workspace` is accepted so the console's
    switcher round-trips, but this platform has one implicit workspace per org — an unknown
    slug is a 404 rather than silently returning the default.
    """
    if workspace and workspace not in {w["slug"] for w in org_svc.workspaces(org)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown workspace '{workspace}'")
    return org_svc.overview(db, org, user, range_=range_, include_test=include_test)


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    """Pricing tiers for the Billing page's plan comparison, each carrying whether it can be
    purchased self-service.

    `self_service` is computed SERVER-SIDE from whether an approved Stripe price is configured
    for the plan (settings.subscription_price_map). The browser never decides this, and never
    sees the Price ID itself — it sends a plan slug back and the server re-resolves. That keeps
    ZST-COM-PLAN-001 Section 03's CTA split (Developer/Business "Start building" vs Enterprise
    "Talk to an expert") derived from configuration rather than hardcoded in the UI."""
    out = []
    for plan in admin_crud.list_plans(db):
        if not plan.is_active:
            continue
        item = PlanOut.model_validate(plan).model_dump()
        # Which CADENCES this plan can actually be bought on, straight from the approved price
        # configuration. The page renders a monthly/annual choice only where both exist, so a
        # cadence Finance has not published is never offered — the same fail-closed rule that
        # decides self_service, applied one level down.
        intervals = settings.purchasable_intervals(plan.slug)
        item["billing_intervals"] = intervals
        item["self_service"] = bool(intervals)
        out.append(item)
    return out


# ── Ledger 1 subscription checkout (ZST-COM-PLAN-001 Section 13/18, Stripe-hosted) ────────

@router.post("/billing/checkout-session")
def create_subscription_checkout(data: SubscriptionCheckoutCreate,
                                  org: Organization = Depends(get_my_org_admin),
                                  user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)):
    """Start a Stripe-hosted subscription checkout for THIS organization's chosen plan.

    Security posture, each point a requirement rather than a precaution:

    * The browser sends a PLAN SLUG only. It never sends an amount, currency, interval or
      Stripe Price ID — the price is resolved server-side by
      admin_crud.resolve_subscription_price_id from operator configuration, so a tampered
      request can at worst name a different plan, never a different price.
    * The organization comes from `get_my_org_admin` (the caller's own session), never from the
      request body, so one tenant cannot open checkout against another's subscription.
    * No state changes. Section 18: "No feature may be unlocked because a card authorization
      succeeded if the subscription/order activation did not complete." Only the verified
      webhook may advance the Section 12 state; this records the session reference for
      correlation and nothing else.
    * Returns a URL for the browser to follow. Card entry happens on Stripe's page — no payment
      credential ever reaches this origin, and the secret key never leaves the server.
    """
    if not settings.stripe_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Payments are not configured")

    plan = admin_crud.get_plan_by_slug(db, data.plan_slug)
    if plan is None or not plan.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")

    try:
        price_id = admin_crud.resolve_subscription_price_id(plan, data.billing_interval)
    except ValueError as e:
        # An unpriced plan is a commercial state, not a server fault: it means Finance has not
        # published a price for it yet. 409 so the UI can route the customer to the inquiry
        # path rather than showing a broken checkout.
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

    sub = admin_crud._current_subs(db, [org.id]).get(org.id)
    if sub is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization has no subscription record to upgrade")

    # A tenant that ALREADY has a live Stripe subscription must not be sent through checkout
    # again. This endpoint creates a NEW Stripe subscription every time it succeeds, so an
    # already-paying organization that clicked a different plan card ended up with TWO live
    # subscriptions on the same customer and was billed for both — the plan never changed, and
    # the second charge was silent. That is a money bug, not a missing feature, so it is
    # refused here rather than left to be noticed on an invoice.
    #
    # Changing the plan of an already-paid subscription is Section 12's PLAN_CHANGE_SCHEDULED
    # path, which is NOT implemented: the effective-date and proration rules it needs are
    # undefined by ZST-COM-PLAN-001 and by the Approved Price Book, so there is no correct
    # amount to charge and no defensible date to charge it on. Until Product/Finance supply
    # those rules this stays a sales conversation, which is what the Billing page already
    # tells the customer — this makes the backend agree with it instead of quietly
    # double-billing.
    if sub.stripe_subscription_id and normalize_subscription_state(sub.status) not in (
            "canceled", "closed", "trial_expired"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This organization already has an active subscription. Changing the plan of a "
            "paid subscription is not self-service yet — please contact sales so the change "
            "can be scheduled without double-billing you.",
        )

    base = settings.APP_URL.rstrip("/")
    # EXPLICITLY "stripe". get_provider() defaults to the deterministic SIMULATOR, so calling it
    # bare handed this route a MockPaymentProvider — which has no subscription-checkout method at
    # all, so the endpoint 500'd instead of ever reaching Stripe. Every Ledger 2 call site
    # already names its provider; this was the one that did not.
    #
    # Naming it is also the safer shape: get_provider("stripe") FAILS CLOSED with
    # ProviderNotConfigured when there is no secret key, where the bare call would silently
    # return a simulator. The stripe_configured() guard above turns that into a 503 first, so
    # this route can never produce a fabricated checkout.
    provider = payment_svc.get_provider("stripe")

    success_url = f"{base}/organization/billing?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base}/organization/billing?checkout=cancelled"
    metadata = {"org_id": str(org.id), "plan_slug": plan.slug, "subscription_id": str(sub.id)}

    # Idempotency key = org + plan + a FINGERPRINT OF THE REQUEST ITSELF.
    #
    # It was `{org.id}:{plan.slug}` alone, which is stable forever — and Stripe rejects a reused
    # key whose parameters have changed ("Keys for idempotent requests can only be used with the
    # same parameters they were first used with"). So the first time anything in the session
    # changed, that organization could never check out for that plan again: every attempt
    # returned IdempotencyError -> 502, permanently. A changed APP_URL after a deploy, or a user
    # changing their email address, was enough to poison it for good.
    #
    # Hashing the parameters makes the collision impossible by construction while keeping the
    # property that mattered: an identical retry (the payer double-clicks, or the browser
    # re-sends) still produces the SAME key and therefore the same Stripe session rather than a
    # second subscription. A genuinely different request simply gets a different key, which is
    # what Stripe's contract asks for.
    fingerprint = hashlib.sha256(json.dumps(
        {"price_id": price_id, "success_url": success_url, "cancel_url": cancel_url,
         "metadata": metadata, "customer_email": user.email},
        sort_keys=True,
    ).encode()).hexdigest()[:16]

    try:
        result = provider.create_subscription_checkout_session(
            price_id=price_id,
            idempotency_key=f"{org.id}:{plan.slug}:{fingerprint}",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata=metadata,
            customer_email=user.email,
        )
    except payment_svc.PaymentProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Payment provider error: {e}")

    admin_crud.record_subscription_checkout_started(
        db, sub, checkout_session_ref=result.checkout_session_ref,
        billing_interval=data.billing_interval, actor=user)
    db.commit()
    # The Price ID is deliberately NOT returned: the browser has no use for it and echoing it
    # would invite a client that tries to send one back.
    return {"checkout_url": result.checkout_url,
            "checkout_session_ref": result.checkout_session_ref}


_PLAN_CHANGE_STATUS = {
    "invalid_interval": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "invalid_plan": status.HTTP_404_NOT_FOUND,
    "unpriced": status.HTTP_409_CONFLICT,
    "not_active": status.HTTP_409_CONFLICT,
    "change_already_scheduled": status.HTTP_409_CONFLICT,
    "no_change": status.HTTP_409_CONFLICT,
    "no_period_end": status.HTTP_409_CONFLICT,
    "illegal_transition": status.HTTP_409_CONFLICT,
    "no_change_scheduled": status.HTTP_409_CONFLICT,
}


@router.post("/billing/plan-change")
def schedule_plan_change(data: SubscriptionCheckoutCreate,
                          org: Organization = Depends(get_my_org_admin),
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Schedule a paid subscription's plan/cadence change for the end of the current period.

    This is the Section 12 `active -> plan_change_scheduled` path, and it is the ONLY
    self-service way to change an already-paid plan. It is separate from checkout on purpose:
    checkout CREATES a subscription (and creating a second one for an existing customer is
    double-billing), whereas this MOVES the one they already have.

    Approved rules enforced by crud.admin.request_plan_change, not here:
      * no proration — nothing is charged or credited at request time;
      * effective at the subscription's own `current_period_end`;
      * entitlements untouched until that date.

    Security posture is identical to checkout: the browser sends a plan slug and a cadence from
    a closed vocabulary, the organization comes from the caller's session (never the body), and
    the Stripe Price is resolved server-side. No amount, price or Stripe id is accepted.
    """
    plan = admin_crud.get_plan_by_slug(db, data.plan_slug)
    if plan is None or not plan.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")

    sub = admin_crud._current_subs(db, [org.id]).get(org.id)
    if sub is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization has no subscription to change")

    try:
        admin_crud.request_plan_change(
            db, sub, plan=plan, billing_interval=data.billing_interval, actor=user)
    except admin_crud.PlanChangeError as e:
        db.rollback()
        raise HTTPException(_PLAN_CHANGE_STATUS.get(e.code, status.HTTP_409_CONFLICT), str(e))
    db.commit()
    db.refresh(sub)
    return _scheduled_change_payload(db, sub)


@router.delete("/billing/plan-change")
def cancel_scheduled_plan_change(org: Organization = Depends(get_my_org_admin),
                                  user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)):
    """Abandon a scheduled change — Section 12's `-> ACTIVE(old version)` outcome.

    Nothing is refunded or reversed because nothing was charged: the current plan was billed
    and entitled throughout, so cancelling simply drops the pending intention.
    """
    sub = admin_crud._current_subs(db, [org.id]).get(org.id)
    if sub is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This organization has no subscription")
    try:
        admin_crud.cancel_plan_change(db, sub, actor=user)
    except admin_crud.PlanChangeError as e:
        db.rollback()
        raise HTTPException(_PLAN_CHANGE_STATUS.get(e.code, status.HTTP_409_CONFLICT), str(e))
    db.commit()
    db.refresh(sub)
    return _scheduled_change_payload(db, sub)


def _scheduled_change_payload(db: Session, sub) -> dict:
    """The pending change as the Billing page needs to render it, or nulls when none.

    Read from OUR record, and only the fields the page displays — no Stripe ids, no price.
    """
    pending = db.get(Plan, sub.pending_plan_id) if sub.pending_plan_id else None
    return {
        "status": sub.status,
        "plan_slug": sub.plan.slug if sub.plan else None,
        "billing_interval": sub.billing_interval,
        "current_period_end": sub.current_period_end,
        "pending_plan_slug": pending.slug if pending else None,
        "pending_plan_name": pending.name if pending else None,
        "pending_billing_interval": sub.pending_billing_interval,
        "plan_change_effective_at": sub.plan_change_effective_at,
    }


@router.get("/billing/checkout-status")
def subscription_checkout_status(session_id: str = Query(..., max_length=120),
                                  org: Organization = Depends(get_my_org_admin),
                                  db: Session = Depends(get_db)):
    """Authoritative state for a returning payer — read from OUR database, never from the URL.

    Stripe's success redirect proves only that the browser came back; it is trivially forgeable
    and arrives before the webhook may have landed. So this reports the Section 12 state the
    backend actually holds, and says `pending` while confirmation is outstanding rather than
    claiming a success that has not been verified.
    """
    sub = admin_crud.subscription_by_provider_ref(db, checkout_session_ref=session_id)
    # Tenant check: a session reference belonging to another organization reveals nothing.
    if sub is None or sub.org_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkout session not found")
    confirmed = sub.stripe_subscription_id is not None
    return {
        "status": sub.status,
        "plan": sub.plan.name if sub.plan else None,
        # False until a signature-verified webhook has bound the Stripe subscription.
        "payment_confirmed": confirmed,
        "state": "confirmed" if confirmed else "pending",
    }


@router.get("/analytics")
def analytics(
    range_: str = Query("30d", alias="range", pattern="^(7d|30d|90d|12m)$"),
    org: Organization = Depends(get_my_org),
    db: Session = Depends(get_db),
):
    """Historical analytics for /organization/analytics, scoped to the caller's own org.
    Readable by any member — same posture as /overview."""
    return org_svc.analytics(db, org, range_key=range_)


@router.get("/audience-attendance")
def audience_attendance(
    range_: str = Query("30d", alias="range", pattern="^(7d|30d|90d|12m)$"),
    org: Organization = Depends(get_my_org),
    db: Session = Depends(get_db),
):
    """Attendance aggregate for the Audience & Access page's Attendance panel — see
    org_svc.audience_attendance's docstring for exactly which figures are real counts vs.
    labeled estimates. Readable by any member, same posture as /analytics."""
    return org_svc.audience_attendance(db, org, range_key=range_)


@router.get("/recordings", response_model=list[RecordingOut])
def list_recordings(
    org: Organization = Depends(get_my_org),
    db: Session = Depends(get_db),
):
    """The org-wide recordings library (/organization/recordings). Only rows that actually
    captured something (status=stopped, enforced=True) — a failed/unenforced attempt has no
    file behind it and would be a dead "Watch Replay" link. Any member may read this, same
    as the rest of the read surface here."""
    recordings = event_crud.list_org_recordings(db, org.id)
    held_event_ids = admin_crud.legal_hold_event_ids(db, {ev.id for _, ev in recordings})
    out = []
    for rec, ev in recordings:
        duration = None
        if rec.started_at and rec.stopped_at:
            duration = int((rec.stopped_at - rec.started_at).total_seconds() - rec.paused_ms / 1000)
        out.append(RecordingOut(
            id=rec.id, event_id=ev.id, title=ev.title, category=ev.category,
            started_at=rec.started_at, duration_seconds=duration, size_bytes=rec.size_bytes,
            url=livekit.signed_url(rec.file_url),
            legal_hold=rec.legal_hold or ev.id in held_event_ids,
            validation_status=rec.validation_status,
        ))
    return out


@router.delete("/recordings/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(
    recording_id: uuid.UUID,
    background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org),
    db: Session = Depends(get_db),
):
    """Governed deletion of a recording (ZST-EC-001 MED-011).

    Org-admin only: this is a destructive, unrecoverable action on org data. Every gate lives
    in services/media_retention.delete_asset, so the same rules apply to any future caller:

        legal hold -> pending retention extension -> retention window -> storage result

    Legal hold was already enforced here and still is, against BOTH the recording's own
    `legal_hold` column and an open legal-hold governance record on its event; only a
    super_admin can place or release one, so an org admin can never delete their way around
    a hold they didn't set and can't lift.

    Two things are new. A recording inside its committed retention window is refused — the
    retention date is a keep-guarantee, not a suggestion, and an extension request that is
    still pending blocks deletion too. And the storage deletion's RESULT is now read: the
    previous version called livekit.delete_object(), discarded the boolean it returns, and
    deleted the row regardless, which left an orphaned object in the bucket with nothing
    recording that it should have been removed.

    Recordings with no retention date — every row captured before MED-011, and every row
    still being validated — behave exactly as they did before.
    """
    rec = event_crud.get_org_recording(db, admin.org_id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    deleted, reason = media_retention.delete_asset(db, background, rec, actor=admin)
    if not deleted:
        raise HTTPException(status.HTTP_409_CONFLICT, reason)


# ── Retention extension (ZST-EC-001 MED-011) ──────────────────────────────────
# Maker-checker on purpose. An Organization can ASK to keep a recording longer; only Zoiko
# Steam platform governance can grant it (routers/admin.py). Letting the asset owner extend
# their own retention unilaterally would make the retention policy advisory.

@router.get("/recordings/{recording_id}/retention", response_model=RetentionStateOut)
def recording_retention(recording_id: uuid.UUID,
                        org: Organization = Depends(get_my_org),
                        db: Session = Depends(get_db)):
    """This asset's committed retention state and whether it can be deleted right now."""
    rec = event_crud.get_org_recording(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    eligible, reason = media_retention.deletion_eligible(db, rec)
    policy = media_retention.policy(db)
    return RetentionStateOut(
        recording_id=rec.id,
        retention_policy_version=rec.retention_policy_version or str(policy["version"]),
        retention_expires_at=rec.retention_expires_at,
        legal_hold=bool(rec.legal_hold) or admin_crud.event_under_legal_hold(db, rec.event_id),
        hold_reference=rec.hold_reference,
        hold_category=rec.hold_category,
        deletion_eligible=eligible,
        deletion_blocked_reason=reason,
    )


@router.post("/recordings/{recording_id}/retention-extensions",
             status_code=status.HTTP_202_ACCEPTED)
def request_retention_extension(recording_id: uuid.UUID, data: RetentionExtensionCreate,
                                background: BackgroundTasks,
                                admin: User = Depends(require_org_admin),
                                org: Organization = Depends(get_my_org_admin),
                                db: Session = Depends(get_db)):
    """Ask platform governance to extend a recording's retention. Grants nothing by itself."""
    rec = event_crud.get_org_recording(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    try:
        extension = media_retention.request_extension(
            db, background, rec, requester=admin,
            reason_category=data.reason_category, requested_until=data.requested_until)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    admin_crud.create_audit_log(db, actor=admin, action="retention.extension_request",
                                target_type="live_recording", target_id=rec.id,
                                org_id=org.id,
                                meta={"reason_category": data.reason_category})
    return {"id": str(extension.id), "state": extension.state,
            "requested_until": extension.requested_until}


# ── Controlled customer export (BRD LE-AC-18) ─────────────────────────────────
# services/delivery.py owns eligibility + the audit trail; this layer only shapes the
# HTTP surface and builds the /deliveries/{token} URL (services never construct URLs,
# same separation crud/organization.py's invitation links already use).

def _delivery_url(token: str) -> str:
    return f"{settings.APP_URL.rstrip('/')}/deliveries/{token}"


@router.get("/recordings/{recording_id}/export-eligibility", response_model=RecordingExportEligibility)
def recording_export_eligibility(
    recording_id: uuid.UUID, org: Organization = Depends(get_my_org), db: Session = Depends(get_db),
):
    rec = event_crud.get_org_recording(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    held = admin_crud.event_under_legal_hold(db, rec.event_id)
    eligible, reason = delivery_svc.export_eligibility(rec, held)
    return RecordingExportEligibility(eligible=eligible, reason=reason, validation_status=rec.validation_status)


@router.get("/recordings/{recording_id}/exports", response_model=list[DeliveryOut])
def list_recording_exports(
    recording_id: uuid.UUID, org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    rec = event_crud.get_org_recording(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    return delivery_crud.list_deliveries(db, recording_id=rec.id)


@router.post("/recordings/{recording_id}/exports", response_model=DeliveryCreated, status_code=status.HTTP_201_CREATED)
def create_recording_export(
    recording_id: uuid.UUID, data: DeliveryCreate, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    rec = event_crud.get_org_recording(db, org.id, recording_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    ev = db.get(Event, rec.event_id)
    try:
        delivery, raw = delivery_svc.create_export(
            db, rec, ev, recipient_name=data.recipient_name, recipient_email=data.recipient_email,
            expires_in_days=data.expires_in_days, actor=admin,
        )
    except delivery_svc.ExportNotEligible as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    url = _delivery_url(raw)
    delivery_svc.deliver_export_email(db, delivery, ev.title or "your event", url)
    return DeliveryCreated(**DeliveryOut.model_validate(delivery).model_dump(), delivery_url=url)


@router.delete("/recordings/{recording_id}/exports/{delivery_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_recording_export(
    recording_id: uuid.UUID, delivery_id: uuid.UUID, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    delivery = delivery_crud.get_delivery(db, org.id, delivery_id)
    if delivery is None or delivery.recording_id != recording_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")
    delivery_crud.revoke_delivery(db, delivery)
    admin_crud.create_audit_log(db, actor=admin, action="export.revoke", target_type="customer_delivery",
                                target_id=delivery.id, org_id=org.id)


# ── Live Inputs (LiveKit Ingress) ─────────────────────────────────────────────
# RTMP/WHIP ingest endpoints for on-site encoders — see models/live.py's
# LiveIngressEndpoint and services/livekit.py's create_ingress/ingress_credentials/
# delete_ingress. Event-scoped like everything else in this app's live domain: an input
# targets one event's room (f"event_{event_id}", same convention as every other LiveKit
# room reference), not a standalone persistent channel.

@router.get("/live-inputs", response_model=list[LiveInputOut])
def list_live_inputs(org: Organization = Depends(get_my_org), db: Session = Depends(get_db)):
    return [
        LiveInputOut(
            id=i.id, event_id=ev.id, event_title=ev.title, title=i.title,
            description=i.description, input_type=i.input_type, state=i.state,
            enforced=i.enforced, error=i.error, created_at=i.created_at,
        )
        for i, ev in event_crud.list_org_ingress_endpoints(db, org.id)
    ]


@router.post("/live-inputs", response_model=LiveInputOut, status_code=status.HTTP_201_CREATED)
async def create_live_input(
    data: LiveInputCreate, background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    ev = db.get(Event, data.event_id)
    if ev is None or ev.org_id != org.id or ev.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    row = LiveIngressEndpoint(
        event_id=ev.id, org_id=org.id, title=data.title, description=data.description,
        input_type=data.input_type, created_by=admin.id,
    )
    # Identity mirrors the on-site-encoder naming other live-domain identities use
    # (moderation.Ctx.identity is str(user.id) for a logged-in participant) — here there's
    # no user behind the connection, so the row's own id stands in.
    db.add(row)
    db.flush()
    identity = f"ingress-{row.id}"
    info, error = await livekit.create_ingress(livekit.room_for_event(ev.id), data.input_type, data.title, identity)
    row.participant_identity = identity
    row.enforced = info is not None
    row.error = error
    if info is not None:
        row.ingress_id = info.ingress_id
    db.commit()

    # ZST-EC-001 MED-001. The row is committed above, so the notice reports a
    # resource that already exists. `enforced` decides how readiness is described —
    # an unprovisioned input is recorded intent, not a working ingest.
    media_comms.notify_input_created(db, background, row)
    db.refresh(row)
    return LiveInputOut(
        id=row.id, event_id=ev.id, event_title=ev.title, title=row.title,
        description=row.description, input_type=row.input_type, state=row.state,
        enforced=row.enforced, error=row.error, created_at=row.created_at,
    )


@router.get("/live-inputs/{endpoint_id}/key", response_model=LiveInputKeyOut)
async def reveal_live_input_key(
    endpoint_id: uuid.UUID, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    row = event_crud.get_org_ingress_endpoint(db, org.id, endpoint_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Live input not found")
    if not row.ingress_id:
        return LiveInputKeyOut(ingest_url=None, stream_key=None)
    url, key = await livekit.ingress_credentials(row.ingress_id)
    return LiveInputKeyOut(ingest_url=url, stream_key=key)


# ── Post-event reports (BRD §18.2) ─────────────────────────────────────────────
# Generate/list are read/write on the report snapshot itself; release hands it to
# services/report.py, which reuses the same CustomerDelivery mechanism as export.

def _report_event(db, org_id, event_id) -> Event:
    ev = db.get(Event, event_id)
    if ev is None or ev.org_id != org_id or ev.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return ev


@router.get("/events/{event_id}/reports", response_model=list[EventReportOut])
def list_event_reports(
    event_id: uuid.UUID, org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    _report_event(db, org.id, event_id)
    return delivery_crud.list_reports(db, event_id)


@router.post("/events/{event_id}/reports", response_model=EventReportOut, status_code=status.HTTP_201_CREATED)
def generate_event_report(
    event_id: uuid.UUID, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    ev = _report_event(db, org.id, event_id)
    return report_svc.generate_event_report(db, ev, actor=admin)


@router.post("/reports/{report_id}/release", response_model=DeliveryCreated, status_code=status.HTTP_201_CREATED)
def release_event_report(
    report_id: uuid.UUID, data: DeliveryCreate, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    report = delivery_crud.get_report(db, org.id, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    ev = db.get(Event, report.event_id)
    delivery, raw = report_svc.release_report(
        db, report, ev, recipient_name=data.recipient_name, recipient_email=data.recipient_email,
        expires_in_days=data.expires_in_days, actor=admin,
    )
    url = _delivery_url(raw)
    report_svc.deliver_report_email(db, delivery, ev.title or "your event", url)
    return DeliveryCreated(**DeliveryOut.model_validate(delivery).model_dump(), delivery_url=url)


@router.delete("/live-inputs/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_input(
    endpoint_id: uuid.UUID, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    row = event_crud.get_org_ingress_endpoint(db, org.id, endpoint_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Live input not found")
    if row.ingress_id:
        await livekit.delete_ingress(row.ingress_id)
    db.delete(row)
    db.commit()


@router.get("/console-state")
def console_state(
    org: Organization = Depends(get_my_org),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Small payload the org shell polls on every page: identity, workspace, nav badges and
    the service-health verdict for the services this org actually uses."""
    return org_svc.console_state(db, org, user)


# ── Identity / Profile ────────────────────────────────────────────────────────

@router.get("/me", response_model=OrgMeOut)
def get_me(org: Organization = Depends(get_my_org)):
    return org


@router.get("/profile", response_model=OrgProfileOut)
def get_profile(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/profile", response_model=OrgProfileOut)
def update_profile(
    data: OrgProfileUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    if data.slug is not None and crud.slug_taken(db, data.slug, org.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Slug already taken")
    return crud.apply_fields(db, org, data)


# ── Branding ──────────────────────────────────────────────────────────────────

@router.get("/branding", response_model=OrgBrandingOut)
def get_branding(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/branding", response_model=OrgBrandingOut)
def update_branding(
    data: OrgBrandingUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    return crud.apply_fields(db, org, data)


# ── Developer ───────────────────────────────────────────────────────────────
# Key records are shaped through ApiKeyOut rather than returned raw: the stored record also
# holds `key_hash`, which is credential material and has no business reaching a browser.

@router.get("/developer", response_model=OrgDeveloperOut)
def get_developer(org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db)):
    return OrgDeveloperOut(
        api_keys=org.api_keys or [],
        webhooks=crud.list_webhook_endpoints(db, org.id),
    )


# Settings.jsx's Developer tab talks to these directly (its own list/create/revoke flow),
# separate from the /developer/api-keys pair below that Credentials.jsx uses via
# GET /developer's raw api_keys field — both read/write the same org.api_keys column
# through the same crud.admin mint/hash logic, just via two different pages' routes.
@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_my_api_keys(org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db)):
    return admin_crud.list_api_keys(db, org)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_my_api_key(
    data: ApiKeyCreate,
    background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    """Mint a key for the caller's OWN org. The raw key is in this response and nowhere
    else — only its sha256 is stored, so it can never be re-shown."""
    created = admin_crud.create_api_key(db, org, data.label,
                                        expires_in_days=data.expires_in_days)
    # ZST-EC-001 DEV-002. The credential is committed before this line, so the notice
    # reports a credential that already exists; a delivery failure cannot unmake it. The
    # claim inside notify_credential_created keys off the credential id, so a retried
    # request cannot produce a second security email for one key.
    admin_crud.create_audit_log(db, actor=admin, action="api_key.create",
                                target_type="api_key", target_id=created.id, org_id=org.id,
                                meta={"label": data.label,
                                      "fingerprint": created.fingerprint})
    developer_comms.notify_credential_created(db, background, org=org, creator=admin,
                                              key_id=created.id)
    return created


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_my_api_key(
    key_id: str,
    background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    # Scoped to the caller's own org, so a key id from another org is simply not found.
    if not admin_crud.revoke_api_key(db, org, key_id, actor_id=admin.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    credential_lifecycle.notify_revoked(db, background, org=org, key_id=key_id, actor=admin)


@router.post("/developer/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_developer_api_key(
    data: ApiKeyCreate, background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    """Org-scoped minting — same mint/hash logic as the super-admin path
    (crud.admin.create_api_key), just gated to this org's own admin instead of a platform
    operator. The raw key is returned once, here, and never again.

    Previously depended on get_current_user, which let ANY authenticated member of the org
    mint a credential — the org-admin dependency below is what the sibling /api-keys route
    already required, and minting is not a member-level action.
    """
    created = admin_crud.create_api_key(db, org, data.label, data.expires_in_days)
    admin_crud.create_audit_log(db, actor=admin, action="api_key.create", target_type="api_key",
                                target_id=created.id, org_id=org.id,
                                meta={"label": data.label, "fingerprint": created.fingerprint})
    # ZST-EC-001 DEV-002 — after the credential is committed.
    developer_comms.notify_credential_created(db, background, org=org, creator=admin,
                                              key_id=created.id)
    return created


@router.delete("/developer/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_developer_api_key(
    key_id: str, background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    if not admin_crud.revoke_api_key(db, org, key_id, actor_id=admin.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    admin_crud.create_audit_log(db, actor=admin, action="api_key.revoke", target_type="api_key",
                                target_id=key_id, org_id=org.id)
    # ZST-EC-001 DEV-004 — after the revocation is committed.
    credential_lifecycle.notify_revoked(db, background, org=org, key_id=key_id, actor=admin)


# ── Developer / Webhook verification + credential rotation (ZST-EC-001 DEV-004/006) ──

@router.post("/developer/webhooks/{endpoint_id}/verification",
             response_model=WebhookVerificationOut)
def reset_webhook_verification(endpoint_id: uuid.UUID, background: BackgroundTasks,
                               admin: User = Depends(require_org_admin),
                               org: Organization = Depends(get_my_org_admin),
                               db: Session = Depends(get_db)):
    """Issue (or re-issue) the verification challenge for an endpoint.

    Re-issuing returns the endpoint to PENDING_VERIFICATION, which withholds production
    events until it is verified again — that is the point of a reset, not a side effect.
    """
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    was_verified = ep.status == WEBHOOK_VERIFIED
    raw = webhook_lifecycle.reset_verification(db, ep)
    admin_crud.create_audit_log(db, actor=admin, action="webhook.verification_reset",
                                target_type="webhook_endpoint", target_id=ep.id,
                                org_id=org.id, meta={"url": ep.url})
    if was_verified:
        webhook_lifecycle.notify_verification_reset(db, background, ep)
    else:
        webhook_lifecycle.notify_verification_required(db, background, ep)
    return WebhookVerificationOut(
        endpoint_id=ep.id, status=ep.status, challenge=raw,
        challenge_header=webhook_security.CHALLENGE_HEADER,
        expires_at=ep.verification_expires_at)


@router.post("/developer/webhooks/{endpoint_id}/verify", response_model=WebhookEndpointOut)
def verify_webhook_endpoint(endpoint_id: uuid.UUID, data: WebhookVerifyIn,
                            admin: User = Depends(require_org_admin),
                            org: Organization = Depends(get_my_org_admin),
                            db: Session = Depends(get_db)):
    """Redeem the challenge. Only this makes an endpoint production-eligible."""
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    outcome = webhook_lifecycle.verify(db, ep, data.challenge)
    if outcome == webhook_lifecycle.OK:
        admin_crud.create_audit_log(db, actor=admin, action="webhook.verified",
                                    target_type="webhook_endpoint", target_id=ep.id,
                                    org_id=org.id, meta={"url": ep.url})
        return ep
    detail = {
        webhook_lifecycle.NOT_PENDING: "This endpoint is already verified.",
        webhook_lifecycle.NO_CHALLENGE: "No verification challenge is outstanding.",
        webhook_lifecycle.EXPIRED: "The verification challenge expired. Reset it and retry.",
        webhook_lifecycle.TOO_MANY_ATTEMPTS: "Too many attempts. Reset verification.",
    }.get(outcome, "The verification challenge did not match.")
    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)


@router.post("/developer/api-keys/{key_id}/rotate", response_model=ApiKeyCreated,
             status_code=status.HTTP_201_CREATED)
def rotate_developer_api_key(key_id: str, background: BackgroundTasks,
                             admin: User = Depends(require_org_admin),
                             org: Organization = Depends(get_my_org_admin),
                             db: Session = Depends(get_db)):
    """Issue a replacement credential and retire the original.

    The replacement secret is in this response and nowhere else. There is deliberately no
    overlap window: an overlap is a promise about what an authentication path will accept,
    and no Zoiko Steam endpoint authenticates an API key yet.
    """
    retired, replacement = admin_crud.rotate_api_key(db, org, key_id, actor_id=admin.id)
    if replacement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "API key not found, or already revoked")
    admin_crud.create_audit_log(db, actor=admin, action="api_key.rotate",
                                target_type="api_key", target_id=replacement.id,
                                org_id=org.id,
                                meta={"rotated_from": key_id,
                                      "old_fingerprint": admin_crud.key_fingerprint(retired),
                                      "new_fingerprint": replacement.fingerprint})
    credential_lifecycle.notify_rotated(db, background, org=org, old_record=retired,
                                        new_key_id=replacement.id, actor=admin)
    return replacement


# ── Developer / signing rotation + data export (ZST-EC-001 DEV-008 / DEV-012) ────────

@router.post("/developer/webhooks/{endpoint_id}/rotate-secret")
def rotate_webhook_signing_secret(endpoint_id: uuid.UUID, background: BackgroundTasks,
                                  admin: User = Depends(require_org_admin),
                                  org: Organization = Depends(get_my_org_admin),
                                  db: Session = Depends(get_db)):
    """Issue a replacement signing secret and open the overlap window.

    Both secrets sign every delivery until the window closes, so a subscriber can migrate
    without dropping events. The raw secret is in this response and in the reveal route;
    it is never emailed.
    """
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    raw, fingerprint = signing_rotation.start_rotation(db, ep, actor_id=admin.id)
    admin_crud.create_audit_log(db, actor=admin, action="webhook.secret_rotate",
                                target_type="webhook_endpoint", target_id=ep.id,
                                org_id=org.id, meta={"fingerprint": fingerprint})
    signing_rotation.notify_started(db, background, ep)
    return {"endpoint_id": str(ep.id), "secret": raw, "fingerprint": fingerprint,
            "secret_version": ep.secret_version,
            "overlap_ends_at": ep.rotation_ends_at}


@router.post("/developer/exports", status_code=status.HTTP_202_ACCEPTED)
def request_developer_export(data: DeveloperExportCreate, background: BackgroundTasks,
                             admin: User = Depends(require_org_admin),
                             org: Organization = Depends(get_my_org_admin),
                             db: Session = Depends(get_db)):
    """Request a governed server-side export of developer metadata.

    Org-admin only: credential and webhook metadata is security-sensitive, and an ordinary
    member has no business bulk-exporting it. Generation happens after the response so a
    large export never blocks the request.
    """
    if data.export_type not in EXPORT_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unsupported export type. Expected one of {list(EXPORT_TYPES)}")
    export = developer_export.request_export(db, org=org, requester=admin,
                                             export_type=data.export_type)
    admin_crud.create_audit_log(db, actor=admin, action="developer_export.request",
                                target_type="developer_export", target_id=export.id,
                                org_id=org.id, meta={"export_type": data.export_type})
    background.add_task(_run_export, export.id)
    return {"id": str(export.id), "status": export.status,
            "export_type": export.export_type}


def _run_export(export_id) -> None:
    """Generate, store and announce one export, off the request path."""
    from ..db import SessionLocal

    db = SessionLocal()
    try:
        export = db.get(DeveloperDataExport, export_id)
        if export is None:
            return
        token = developer_export.process(db, export)
        if token:
            developer_export.notify_ready(db, developer_export._Bg(), export, token)
        else:
            developer_export.notify_failed(db, developer_export._Bg(), export)
    finally:
        db.close()


@router.get("/developer/exports/{export_id}/download")
def download_developer_export(export_id: uuid.UUID, token: str, request: Request,
                              admin: User = Depends(require_org_admin),
                              org: Organization = Depends(get_my_org_admin),
                              db: Session = Depends(get_db)):
    """Serve an export against a single-use, short-lived, purpose-bound authorization.

    Authenticated AND token-bound: the link alone is not sufficient, so a forwarded email
    does not hand the export to whoever received it.
    """
    export = db.get(DeveloperDataExport, export_id)
    if export is None or export.org_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export not found")

    client_hint = request.headers.get("user-agent", "")[:120]
    if not developer_export.authorize_download(db, export, token, user=admin,
                                               client=client_hint):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This download link is not valid or has expired.")
    content = developer_export.load(export)
    if content is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The export object is no longer stored")
    return Response(content=content, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="{export.export_type}.csv"'})


# ── Developer / Webhooks ──────────────────────────────────────────────────────
# See models/webhook.py + services/webhooks.py for the delivery system itself
# (signing, retries, the ticker). This is just the registration CRUD + delivery log read.

def _validate_events(events: list[str]) -> None:
    bad = [e for e in events if e not in WEBHOOK_EVENTS]
    if bad:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown event type(s): {', '.join(bad)}")


@router.post("/developer/webhooks", response_model=WebhookEndpointCreated, status_code=status.HTTP_201_CREATED)
def create_developer_webhook(
    data: WebhookEndpointCreate, background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    _validate_events(data.events)
    # ZST-EC-001 DEV-006. Validated before anything is stored: the URL column had no
    # constraint at all, so an internal or metadata address could be registered and the
    # platform would sign and POST to it.
    try:
        webhook_security.validate_webhook_url(data.url)
    except webhook_security.UnsafeWebhookUrl as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    ep = crud.create_webhook_endpoint(db, org.id, data.url, data.label, data.events, admin.id)
    # The endpoint starts PENDING_VERIFICATION and receives no production events until it
    # proves control of the URL.
    webhook_lifecycle.issue_challenge(db, ep)
    webhook_lifecycle.notify_verification_required(db, background, ep)
    admin_crud.create_audit_log(db, actor=admin, action="webhook.create", target_type="webhook_endpoint",
                                target_id=ep.id, org_id=org.id, meta={"url": data.url})
    return ep


@router.patch("/developer/webhooks/{endpoint_id}", response_model=WebhookEndpointOut)
def update_developer_webhook(
    endpoint_id: uuid.UUID, data: WebhookEndpointUpdate, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    if data.events is not None:
        _validate_events(data.events)
    url_changed = bool(data.url and data.url.strip() != ep.url)
    if url_changed:
        try:
            webhook_security.validate_webhook_url(data.url)
        except webhook_security.UnsafeWebhookUrl as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    ep = crud.update_webhook_endpoint(db, ep, url=data.url, label=data.label,
                                      events=data.events, enabled=data.enabled)
    admin_crud.create_audit_log(db, actor=admin, action="webhook.update", target_type="webhook_endpoint",
                                target_id=ep.id, org_id=org.id)
    return ep


@router.delete("/developer/webhooks/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_developer_webhook(
    endpoint_id: uuid.UUID, admin: User = Depends(get_current_user),
    org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    crud.delete_webhook_endpoint(db, ep)
    admin_crud.create_audit_log(db, actor=admin, action="webhook.delete", target_type="webhook_endpoint",
                                target_id=endpoint_id, org_id=org.id)


@router.get("/developer/webhooks/{endpoint_id}/secret", response_model=WebhookSecretOut)
def reveal_developer_webhook_secret(
    endpoint_id: uuid.UUID, org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    """Re-viewable, not reveal-once — see models/webhook.py's docstring for why this
    secret is a shared verification secret rather than a bearer credential."""
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    return WebhookSecretOut(secret=ep.secret)


@router.get("/developer/webhooks/{endpoint_id}/deliveries", response_model=list[WebhookDeliveryOut])
def list_developer_webhook_deliveries(
    endpoint_id: uuid.UUID, org: Organization = Depends(get_my_org_admin), db: Session = Depends(get_db),
):
    ep = crud.get_webhook_endpoint(db, org.id, endpoint_id)
    if ep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook endpoint not found")
    return crud.list_webhook_deliveries(db, ep.id)


# ── Notifications ─────────────────────────────────────────────────────────────

@router.get("/notifications", response_model=OrgNotifications)
def get_notifications(org: Organization = Depends(get_my_org)):
    # Normalized, not raw. A legacy `security_alerts: false` sitting in the JSON column
    # must never be reported back as though it were in effect.
    return OrgNotifications(**notif_svc.effective(org))


@router.get("/notifications/catalog", response_model=list[NotificationCatalogItem])
def notification_catalog(org: Organization = Depends(get_my_org)):
    """What each preference actually controls.

    The settings page renders from this rather than from a hardcoded list, so a switch can
    never outlive the email it claims to govern: `mandatory` marks the families no
    preference may suppress, and `available` marks the ones with no send path at all.
    """
    current = notif_svc.effective(org)
    return [
        NotificationCatalogItem(**entry, value=bool(current.get(entry["key"], False)))
        for entry in notif_svc.CATALOG
    ]


@router.patch("/notifications", response_model=OrgNotifications)
def update_notifications(
    data: OrgNotifications,
    background: BackgroundTasks,
    admin: User = Depends(require_org_admin),
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    """Save operational preferences, then confirm the change (ZST-EC-001 ORG-012).

    Order is fixed: normalize -> compare -> commit -> record the snapshot event -> notify.
    Nothing is emailed unless something actually moved, and the email describes state that
    is already durable.
    """
    previous = notif_svc.effective(org)
    incoming = data.model_dump(exclude_unset=True)

    # Normalize BEFORE persisting. A client may still send `security_alerts: false` for
    # backward compatibility; it is pinned back to True here rather than rejected, so old
    # clients keep working and the mandatory guarantee still holds in the database.
    proposed = notif_svc.normalize({**previous, **incoming})

    if proposed == previous:
        # A no-op is not a change: no event, no email.
        return OrgNotifications(**previous)

    merged = crud.merge_json(db, org, "notifications", proposed)
    current = notif_svc.normalize(merged)

    effective_at = datetime.now(timezone.utc)
    event = NotificationPreferenceEvent(
        org_id=org.id,
        preference_owner_id=admin.id, preference_owner_email=admin.email,
        actor_id=admin.id, actor_email=admin.email,
        previous_preferences=previous, current_preferences=current,
        scope=SCOPE_ORGANIZATION, effective_at=effective_at,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    admin_crud.create_audit_log(db, actor=admin, action="notification_preferences.update",
                                target_type="organization", target_id=org.id, org_id=org.id,
                                meta={"changed": notif_svc.summarize_change(previous, current)})
    org_comms_notify_preferences(db, background, org, event, admin)
    return OrgNotifications(**current)


def org_comms_notify_preferences(db, background, org, event, actor) -> None:
    """Queue the single summarized ORG-012 message for one committed change.

    One notification per event, claimed by conditional UPDATE, so a double-submit or a
    retried request cannot confirm the same change twice.

    Recipients are the preference owner plus — only when organization-wide routing moved —
    the recorded Organization owner. Every administrator is deliberately NOT mailed: a
    routine settings edit is not an organization-wide security event.
    """
    updated = db.execute(
        update(NotificationPreferenceEvent)
        .where(NotificationPreferenceEvent.id == event.id,
               NotificationPreferenceEvent.notified_at.is_(None))
        .values(notified_at=datetime.now(timezone.utc))
    ).rowcount
    db.commit()
    if not updated:
        return

    summary = notif_svc.summarize_change(event.previous_preferences or {},
                                         event.current_preferences or {})
    owner = db.get(User, org.owner_user_id) if org.owner_user_id else None
    addresses = org_comms.recipients(actor, owner)
    if not addresses:
        return
    try:
        for address in addresses:
            background.add_task(
                email_mod.send_notification_preferences_email, address,
                change_summary=summary,
                effective_at=org_comms.org_timestamp(org, event.effective_at),
                scope=f"{org.name} (organization-wide)",
                actor=actor.email if actor else None,
            )
    except UnsafeLinkError:
        log.exception("ORG-012 not queued: APP_URL unsafe for this environment")


# ── Security ──────────────────────────────────────────────────────────────────

@router.get("/security", response_model=OrgSecurity)
def get_security(org: Organization = Depends(get_my_org_admin)):
    return OrgSecurity(**(org.security or {}))


@router.patch("/security", response_model=OrgSecurity)
def update_security(
    data: OrgSecurity,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    merged = crud.merge_json(db, org, "security", data.model_dump(exclude_unset=True))
    return OrgSecurity(**merged)


# ── Domain ────────────────────────────────────────────────────────────────────

@router.get("/domain", response_model=OrgDomainOut)
def get_domain(org: Organization = Depends(get_my_org)):
    return org


@router.patch("/domain", response_model=OrgDomainOut)
def update_domain(
    data: OrgDomainUpdate,
    org: Organization = Depends(get_my_org_admin),
    db: Session = Depends(get_db),
):
    # Changing the domain drops it back to unverified — a real DNS-verification flow
    # (later phase) is the only thing that sets domain_verified = True. Only react to a
    # domain the client actually sent (exclude_unset), not the None default of an empty body.
    fields = data.model_dump(exclude_unset=True)
    if "domain" in fields and fields["domain"] != org.domain:
        org.domain_verified = False
    return crud.apply_fields(db, org, data)


# ── Members (users) ────────────────────────────────────────────────────────────
# Org-admin only: this is the member-management surface. Every query is scoped to the
# admin's own org_id (from the JWT); org_id is never taken from the client.

def _user_out(u: User, org_name: str | None) -> AdminUserOut:
    out = AdminUserOut.model_validate(u)
    out.organization_name = org_name
    return out


@router.get("/users", response_model=Page)
def list_org_users(
    q: str | None = None,
    role: str | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    sort_by: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    users, total = crud.list_org_users(db, admin.org_id, q=q, role=role, status=status_,
                                       sort_by=sort_by, order=order, page=page, page_size=page_size)
    org_name = admin.organization.name if admin.organization else None
    return Page(items=[_user_out(u, org_name) for u in users], total=total, page=page, page_size=page_size)


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_org_user(user_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _user_out(u, admin.organization.name if admin.organization else None)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def update_org_user(user_id: uuid.UUID, data: UserUpdate, background: BackgroundTasks,
                    step_up: str | None = Header(None, alias="X-Step-Up"),
                    admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.role == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot modify a super admin")
    if data.role is not None and data.role not in ORG_ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role")
    # Self-lockout guard: an admin can't demote or deactivate their own account.
    if u.id == admin.id and (data.is_active is False or (data.role and data.role != admin.role)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot demote or deactivate yourself")
    was_active = u.is_active
    # ORG-003 high-risk control. Handing someone administrative control of the Organization
    # requires proof the acting admin is present RIGHT NOW, not that they signed in earlier.
    # Ordinary role changes are deliberately left alone — requiring re-auth to make somebody
    # a Viewer would train people to type their password without reading the prompt.
    if org_comms.is_high_risk_grant(u.role, data.role):
        outcome = stepup_svc.consume(
            db, admin, reference=step_up, purpose=STEP_UP_HIGH_RISK_ROLE_GRANT,
            spent_on=f"grant {data.role} to {u.id}")
        if outcome != stepup_svc.OK:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {"code": "STEP_UP_REQUIRED",
                 "purpose": STEP_UP_HIGH_RISK_ROLE_GRANT,
                 "message": stepup_svc.describe(outcome)})

    # ORG-003 needs the BEFORE picture, and it has to be taken while the old values are
    # still on the row. Normalized to the same display form as the after-picture so the two
    # are comparable and an unrelated edit produces two identical strings.
    previous_access = org_comms.describe_access(role=u.role, org=admin.organization,
                                                active=u.is_active)
    crud.update_org_user(db, u, data)
    current_access = org_comms.describe_access(role=u.role, org=admin.organization,
                                               active=u.is_active)

    # ORG-003 - committed access-policy change. announce_access_changed no-ops when the two
    # snapshots match, so renaming a member sends nothing.
    org_comms.announce_access_changed(db, background, user=u, org_id=u.org_id,
                                      previous_access=previous_access,
                                      current_access=current_access)

    # IDN-008 only on a real active-state flip, after commit. Kept separate from ORG-003 on
    # purpose: a deactivation restricts the Zoiko identity, which is a different claim from
    # an Organization access change and is owned by a different family.
    if data.is_active is not None and data.is_active != was_active:
        lifecycle.announce(
            db, background, user=u, email=u.email, org_id=u.org_id,
            state=lifecycle.STATE_RESTRICTED if not u.is_active else lifecycle.STATE_REACTIVATED,
            reason_category="organization_administrative_action",
        )
    return _user_out(u, admin.organization.name if admin.organization else None)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org_user(user_id: uuid.UUID, background: BackgroundTasks,
                    admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    u = crud.get_org_user(db, admin.org_id, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if u.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    if u.role == "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cannot delete a super admin")
    previous_access = org_comms.describe_access(role=u.role, org=admin.organization,
                                                active=u.is_active)
    crud.soft_delete_user(db, u)  # retained in DB, hidden from listings, sessions killed

    # ORG-004 - the membership in THIS Organization ended. identity_also_restricted records
    # that soft_delete_user additionally deactivated the account, for the audit trail; it
    # never changes the copy, because ORG-004 must not claim anything about the identity.
    org_comms.announce_membership_removed(db, background, user=u, email=u.email,
                                          org_id=u.org_id, previous_access=previous_access,
                                          identity_also_restricted=True)

    # IDN-008 "Deletion completed". Truthful for a soft delete: active access really is
    # removed, and the record really is retained — which is exactly what the residual-records
    # wording says, rather than claiming erasure. Both families fire because both statements
    # are true: one Organization membership ended AND the identity was deleted here.
    lifecycle.announce(db, background, user=u, email=u.email, org_id=u.org_id,
                       state=lifecycle.STATE_DELETION_COMPLETED,
                       reason_category="organization_administrative_action")


# ══ ORG-009 Authorized support access — customer surface ════════════════════════════════
# The approval gate, from the tenant's side. These are the only routes that can turn a
# support request into something startable, and they are org-scoped by the caller's own
# membership: an approver can only ever decide on requests against their OWN Organization.


def _support_customer_out(req) -> SupportAccessCustomerOut:
    return SupportAccessCustomerOut(
        id=req.id, case_reference=req.case_reference,
        engineer_display=req.engineer_display, requested_scope=req.requested_scope,
        allowed_actions=support_svc.actions_list(req.allowed_actions),
        requested_minutes=req.requested_minutes, status=req.status,
        emergency=req.emergency, requested_at=req.requested_at,
        approved_at=req.approved_at, approved_by_email=req.approved_by_email,
        starts_at=req.starts_at, expires_at=req.expires_at, ended_at=req.ended_at,
    )


def _my_support_request(db: Session, admin: User, req_id: uuid.UUID) -> SupportAccessRequest:
    req = db.get(SupportAccessRequest, req_id)
    # Scoped to the caller's organization. A 404 rather than a 403 so this cannot be used to
    # discover that a support request exists against some other tenant.
    if req is None or req.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support request not found")
    return req


@router.get("/support-access", response_model=list[SupportAccessCustomerOut])
def list_support_access(admin: User = Depends(require_org_admin),
                        db: Session = Depends(get_db)):
    """Every support-access request raised against this Organization, newest first.

    This is the "action history" surface the ORG-009 ended notice points at, and it is a
    tenant route — no customer email ever links to a Super Admin console.
    """
    rows = db.scalars(
        select(SupportAccessRequest)
        .where(SupportAccessRequest.org_id == admin.org_id)
        .order_by(SupportAccessRequest.requested_at.desc())
    ).all()
    return [_support_customer_out(r) for r in rows]


@router.post("/support-access/{req_id}/decision", response_model=SupportAccessCustomerOut)
def decide_support_access(req_id: uuid.UUID, data: SupportAccessDecision,
                          background: BackgroundTasks,
                          admin: User = Depends(require_org_admin),
                          db: Session = Depends(get_db)):
    """Approve or deny scoped support access to this Organization.

    Approval binds to the exact terms on the record right now (services/support_access.py
    fingerprints them). If staff later widen the scope or extend the duration, this approval
    stops applying and a fresh decision is required.
    """
    req = _my_support_request(db, admin, req_id)
    if req.status != "requested":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This request is already {req.status}")

    outcome = (support_svc.approve(db, req, admin) if data.approve
               else support_svc.deny(db, req, admin))
    if outcome != support_svc.OK:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request is already {outcome}")

    admin_crud.create_audit_log(
        db, actor=admin,
        action="support_access.approve" if data.approve else "support_access.deny",
        target_type="support_access", target_id=req.id, org_id=req.org_id,
        meta={"case": req.case_reference, "engineer": req.engineer_display,
              "scope": req.requested_scope, "minutes": req.requested_minutes},
    )
    return _support_customer_out(req)


# ══ ORG-008 Organization ownership transfer ═════════════════════════════════════════════
# Ownership moves only after BOTH parties confirm from an authenticated session, before the
# request expires. An email link click is never sufficient — the emails carry no token at all.


@router.get("/ownership-transfer", response_model=OwnershipTransferOut | None)
def get_ownership_transfer(admin: User = Depends(require_org_admin),
                           db: Session = Depends(get_db)):
    return db.scalar(
        select(OwnershipTransfer)
        .where(OwnershipTransfer.org_id == admin.org_id,
               OwnershipTransfer.status.notin_(("completed", "canceled", "expired")))
        .order_by(OwnershipTransfer.initiated_at.desc())
    )


@router.post("/ownership-transfer", response_model=OwnershipTransferOut,
             status_code=status.HTTP_201_CREATED)
def start_ownership_transfer(data: OwnershipTransferCreate, background: BackgroundTasks,
                             admin: User = Depends(require_org_admin),
                             org: Organization = Depends(get_my_org),
                             db: Session = Depends(get_db)):
    """Propose a new owner. Ownership does NOT change here."""
    proposed = db.scalar(
        select(User).where(User.org_id == admin.org_id,
                           func.lower(User.email) == data.proposed_owner_email.lower(),
                           User.is_active.is_(True), User.deleted_at.is_(None))
    )
    if proposed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "The proposed owner must be an active member of this Organization")
    if org.owner_user_id == proposed.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "That member already owns this Organization")
    existing = db.scalar(
        select(OwnershipTransfer).where(
            OwnershipTransfer.org_id == org.id,
            OwnershipTransfer.status.notin_(("completed", "canceled", "expired")))
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An ownership transfer is already in progress")

    current_owner = db.get(User, org.owner_user_id) if org.owner_user_id else admin
    transfer = governance.initiate_transfer(db, org=org, current_owner=current_owner,
                                            proposed_owner=proposed, initiated_by=admin)
    admin_crud.create_audit_log(db, actor=admin, action="ownership_transfer.initiate",
                                target_type="ownership_transfer", target_id=transfer.id,
                                org_id=org.id,
                                meta={"proposed_owner": proposed.email})
    governance.notify_transfer_initiated(db, background, transfer)
    return transfer


@router.post("/ownership-transfer/{transfer_id}/confirm", response_model=OwnershipTransferOut,
             responses={409: {"description": "Transfer expired or already resolved"}})
def confirm_ownership_transfer(transfer_id: uuid.UUID, background: BackgroundTasks,
                               step_up: str | None = Header(None, alias="X-Step-Up"),
                               user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    """Record this party's durable confirmation, and complete once both exist.

    Deliberately depends on get_current_user rather than require_org_admin: the proposed
    owner may not be an administrator yet, and requiring one would make the second
    confirmation impossible for exactly the person it is meant to come from.

    ZST-EC-001 ORG-008: BOTH confirmations require a fresh step-up. Ownership of a tenant is
    the highest-value grant in the product, and an unattended session is not evidence that
    the owner is the one confirming. The email carries no token precisely so that the only
    way to confirm is here, signed in, having just re-entered a password.
    """
    transfer = db.get(OwnershipTransfer, transfer_id)
    if transfer is None or transfer.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer not found")

    outcome = stepup_svc.consume(db, user, reference=step_up,
                                 purpose=STEP_UP_OWNERSHIP_TRANSFER,
                                 spent_on=f"confirm transfer {transfer_id}")
    if outcome != stepup_svc.OK:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "STEP_UP_REQUIRED", "purpose": STEP_UP_OWNERSHIP_TRANSFER,
             "message": stepup_svc.describe(outcome)})

    # Expiry is evaluated before anything else, so a retry cannot resurrect a dead request.
    if governance.transfer_is_expired(transfer):
        if governance.expire_transfer(db, transfer):
            governance.notify_transfer_expired(db, background, transfer)
        # Returned, not raised. BackgroundTasks ride on the response object, and raising
        # builds a NEW response that discards everything queued on the abandoned one - so
        # raising here would record the expiry and silently drop the notice telling both
        # parties that ownership did not change.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "This transfer request expired; ownership did not change"},
            background=background,
        )

    outcome = governance.confirm_transfer(db, transfer, user)
    if outcome == "not_a_party":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the current or proposed owner can confirm this transfer")
    admin_crud.create_audit_log(db, actor=user, action="ownership_transfer.confirm",
                                target_type="ownership_transfer", target_id=transfer.id,
                                org_id=transfer.org_id, meta={"status": transfer.status})

    if transfer.status == "ready" and governance.complete_transfer(db, transfer):
        admin_crud.create_audit_log(db, actor=user, action="ownership_transfer.complete",
                                    target_type="ownership_transfer", target_id=transfer.id,
                                    org_id=transfer.org_id,
                                    meta={"new_owner": transfer.proposed_owner_email})
        governance.notify_transfer_completed(db, background, transfer)
    return transfer


@router.delete("/ownership-transfer/{transfer_id}", response_model=OwnershipTransferOut)
def cancel_ownership_transfer(transfer_id: uuid.UUID, background: BackgroundTasks,
                              admin: User = Depends(require_org_admin),
                              db: Session = Depends(get_db)):
    transfer = db.get(OwnershipTransfer, transfer_id)
    if transfer is None or transfer.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transfer not found")
    if not governance.cancel_transfer(db, transfer, admin):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This transfer is already {transfer.status}")
    admin_crud.create_audit_log(db, actor=admin, action="ownership_transfer.cancel",
                                target_type="ownership_transfer", target_id=transfer.id,
                                org_id=transfer.org_id, meta={})
    governance.notify_transfer_canceled(db, background, transfer)
    return transfer


# ══ ORG-007 Access review ═══════════════════════════════════════════════════════════════
# A reviewer's silence leaves a decision PENDING. There is no path here that turns silence
# into an approval, and completion is refused while anything is still pending.


@router.post("/access-reviews", response_model=AccessReviewOut,
             status_code=status.HTTP_201_CREATED)
def open_access_review(data: AccessReviewCreate, background: BackgroundTasks,
                       admin: User = Depends(require_org_admin),
                       db: Session = Depends(get_db)):
    review = governance.open_review(
        db, org_id=admin.org_id,
        due_at=datetime.now(timezone.utc) + timedelta(days=data.due_in_days),
        created_by=admin, review_period=data.review_period,
    )
    admin_crud.create_audit_log(db, actor=admin, action="access_review.open",
                                target_type="access_review", target_id=review.id,
                                org_id=admin.org_id,
                                meta={"due_at": review.due_at.isoformat()})
    governance.notify_review_opened(db, background, review)
    out = AccessReviewOut.model_validate(review)
    out.outstanding = governance.outstanding(db, review)
    return out


@router.get("/access-reviews/{review_id}/assignments",
            response_model=list[AccessReviewAssignmentOut])
def list_review_assignments(review_id: uuid.UUID, admin: User = Depends(require_org_admin),
                            db: Session = Depends(get_db)):
    review = db.get(AccessReview, review_id)
    if review is None or review.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access review not found")
    return db.scalars(
        select(AccessReviewAssignment)
        .where(AccessReviewAssignment.review_id == review.id)
        .order_by(AccessReviewAssignment.member_email)
    ).all()


@router.post("/access-reviews/{review_id}/assignments/{assignment_id}",
             response_model=AccessReviewAssignmentOut)
def decide_review_assignment(review_id: uuid.UUID, assignment_id: uuid.UUID,
                             data: AccessReviewDecisionIn,
                             admin: User = Depends(require_org_admin),
                             db: Session = Depends(get_db)):
    """Record one decision. An exception needs a reason and an accountable owner."""
    review = db.get(AccessReview, review_id)
    if review is None or review.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access review not found")
    if review.status == "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This review is already complete")
    assignment = db.get(AccessReviewAssignment, assignment_id)
    if assignment is None or assignment.review_id != review.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")

    if data.decision not in ("approved", "change_required", "remove", "exception"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown decision")
    if data.decision == "exception" and not (data.reason or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "An exception requires a recorded reason")

    governance.record_decision(db, assignment, decision=data.decision, decided_by=admin,
                               reason=data.reason,
                               exception_owner_email=(data.exception_owner_email
                                                      or None))
    admin_crud.create_audit_log(db, actor=admin, action="access_review.decide",
                                target_type="access_review_assignment",
                                target_id=assignment.id, org_id=review.org_id,
                                meta={"decision": data.decision,
                                      "member": assignment.member_email,
                                      "high_risk": assignment.high_risk})
    return assignment


@router.post("/access-reviews/{review_id}/complete", response_model=AccessReviewOut)
def complete_access_review(review_id: uuid.UUID, background: BackgroundTasks,
                           admin: User = Depends(require_org_admin),
                           db: Session = Depends(get_db)):
    review = db.get(AccessReview, review_id)
    if review is None or review.org_id != admin.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access review not found")
    if not governance.complete(db, review):
        pending = governance.outstanding(db, review)
        # Inaction is not approval: the review stays open until every member has a decision.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{pending} member(s) still have no recorded decision; a review cannot be "
            f"completed by leaving them pending",
        )
    admin_crud.create_audit_log(db, actor=admin, action="access_review.complete",
                                target_type="access_review", target_id=review.id,
                                org_id=review.org_id, meta=governance.tally(db, review))
    governance.notify_review_completed(db, background, review)
    out = AccessReviewOut.model_validate(review)
    out.outstanding = 0
    return out


# ── Invitations (admin management) ───────────────────────────────────────────────

def _invite_url(token: str) -> str:
    base = settings.APP_URL.rstrip("/")
    return f"{base}/accept-invite?token={token}"


def _inv_out(inv, token: str | None = None, url: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=inv.id, email=inv.email, role=inv.role, status=inv.status,
        invited_by=inv.inviter.email if inv.inviter else None,
        expires_at=inv.expires_at, accepted_at=inv.accepted_at, created_at=inv.created_at,
        invite_token=token, invite_url=url,
    )


@router.get("/invitations", response_model=Page)
def list_invitations(
    status_: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    items, total = crud.list_invitations(db, admin.org_id, status=status_, page=page, page_size=page_size)
    return Page(items=[_inv_out(i) for i in items], total=total, page=page, page_size=page_size)


@router.post("/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def create_invitation(data: InvitationCreate, background: BackgroundTasks,
                      admin: User = Depends(require_org_admin), org: Organization = Depends(get_my_org),
                      db: Session = Depends(get_db)):
    email = data.email.lower()
    if data.role not in ORG_ASSIGNABLE_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid role")
    # ZST-EC-001 Phase 10. organizations.security.allowed_domains was inert; it now gates
    # who may be invited. Deliberately not applied to sign-in - locking out an existing
    # member whose address predates the policy is a support incident, not a security win.
    domain_issue = org_policy.domain_violation(org, email)
    if domain_issue:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, domain_issue)
    if crud.user_email_taken(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")
    if crud.pending_invite_exists(db, admin.org_id, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "A pending invitation for that email already exists")
    # ENFORCEMENT reads the plan directly, NOT the overview payload. It used to pull the
    # "Members" bar out of `entitlements()`, which is a REPORTING API whose `limit: None` means
    # "no entitled plan, so render no denominator". Enforcement read that same None as "no
    # ceiling", so a trial_expired / canceled / closed / suspended organization could invite
    # UNLIMITED members — more than it could while its subscription was live.
    #
    # `enforcement_plan` answers the ceiling question directly, so the display convention can no
    # longer leak into a permission decision. `None` still means "no ceiling", but now it can
    # only arise from a plan that genuinely has no seat limit (Enterprise) or from an
    # organization with no subscription row at all — never from a lapsed subscription.
    seat_plan = org_svc.enforcement_plan(db, org.id)
    seat_limit = seat_plan.max_users if seat_plan else None
    if seat_limit is not None:
        members_used = db.scalar(
            select(func.count(User.id)).where(User.org_id == org.id, User.deleted_at.is_(None))
        ) or 0
        if members_used >= seat_limit:
            # ZST-EC-001 COM-008. This 409 used to be the customer's ONLY signal: the person
            # clicking Invite saw an error and nobody responsible for the plan was told.
            #
            # The DECISION above is the authoritative one and is deliberately unchanged by
            # this notice — evaluate_entitlements re-reads the same authority and records the
            # UNDER_LIMIT -> LIMIT_REACHED crossing, so the mail fires once per crossing
            # rather than on every blocked attempt. No limit is computed here, and none is
            # passed to the email layer.
            commerce_comms.evaluate_entitlements(db, org)
            commerce_comms.notify_limit_reached(
                db, background, org, "Members",
                blocked_action="New member invitations are blocked")
            # JSONResponse rather than `raise`: BackgroundTasks are attached to the RESPONSE,
            # and raising discards them — the notice would be queued and never sent.
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": "Member seat limit reached for your plan — upgrade to "
                                   "invite more people"},
                background=background)
    inv, raw = crud.create_invitation(db, admin.org_id, email, data.role, admin.id)
    url = _invite_url(raw)
    # ORG-001 base variant. The invitation row is already committed, so the notice reports
    # state rather than creating it, and the claim inside announce_invited means a retried
    # request cannot mail the invitee twice.
    org_comms.announce_invited(db, background, inv, raw)
    return _inv_out(inv, token=raw, url=url)  # raw token returned once, for delivery


@router.patch("/invitations/{invitation_id}", response_model=InvitationOut)
def update_invitation(invitation_id: uuid.UUID, data: InvitationAction, background: BackgroundTasks,
                      admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if inv.status == "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation already accepted")
    if data.action == "resend":
        # resend_invitation re-issues the offer (new token, new deadline) and clears the
        # ORG-001 markers, so the new offer is announced exactly once. It grants nothing on
        # its own: the invitee still has to accept, and the role is unchanged.
        inv, raw = crud.resend_invitation(db, inv)
        url = _invite_url(raw)
        org_comms.announce_invited(db, background, inv, raw)
        return _inv_out(inv, token=raw, url=url)

    # Revoked and Expired are distinct ORG-001 variants with distinct copy, so the notice is
    # chosen from the state actually written rather than from one shared "cancelled" branch.
    revoked = data.action == "cancel"
    crud.set_invitation_status(db, inv, "cancelled" if revoked else "expired")
    if revoked:
        org_comms.announce_revoked(db, background, inv)
    else:
        org_comms.announce_expired(db, background, inv)
    return _inv_out(inv)


@router.delete("/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invitation(invitation_id: uuid.UUID, admin: User = Depends(require_org_admin), db: Session = Depends(get_db)):
    inv = crud.get_invitation(db, admin.org_id, invitation_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    crud.delete_invitation(db, inv)


def _expired_response(db, background, inv) -> JSONResponse:
    """ORG-001 Expired, fired where a lapsed invitation is actually observed and recorded.

    These are unauthenticated routes, so this is the only place in the request path where
    expiry becomes authoritative state. The claim inside announce_expired means repeatedly
    opening a dead link mails the invitee once, not once per refresh.

    Returns the 400 explicitly instead of raising it. BackgroundTasks ride on the response
    object, and raising builds a NEW response - which discards every task queued on the
    abandoned one. Raising here would have meant the Expired notice was queued and then
    silently dropped on both public paths, in production, with nothing to show for it.
    """
    org_comms.announce_expired(db, background, inv)
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST,
                        content={"detail": "Invitation has expired"},
                        background=background)


# ── Invitation accept / reject (public — the invitee holds a token, not a session) ──

@router.get("/invitations/preview", response_model=InvitationPreview,
            responses={400: {"description": "Invalid or expired invitation"}})
def preview_invitation(token: str, background: BackgroundTasks, db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    if inv.expires_at < datetime.now(timezone.utc):
        crud.set_invitation_status(db, inv, "expired")
        return _expired_response(db, background, inv)
    return InvitationPreview(
        email=inv.email, role=inv.role,
        organization_name=inv.organization.name if inv.organization else "your organization",
    )


@router.post("/invitations/accept", response_model=TokenOut,
             responses={400: {"description": "Invalid or expired invitation"}})
def accept_invitation(data: InvitationAccept, background: BackgroundTasks,
                      db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, data.token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    if inv.expires_at < datetime.now(timezone.utc):
        crud.set_invitation_status(db, inv, "expired")
        return _expired_response(db, background, inv)
    if crud.user_email_taken(db, inv.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
    if data.username and crud.username_taken(db, data.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    violation = org_policy.password_violation(inv.organization, data.password)
    if violation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, violation)
    username = (data.username or crud.unique_username(db, inv.email.split("@")[0])).lower()
    user = crud.accept_invitation(db, inv, data.full_name, username, hash_password(data.password))
    # ORG-002. accept_invitation committed both the membership and the accepted status, so
    # the notice describes state that already exists. Recipients are the inviter plus the
    # organization's administrators, deduplicated, with the new member excluded.
    org_comms.announce_member_joined(db, background, inv, user)
    out = UserOut.model_validate(user)
    out.organization_name = user.organization.name if user.organization else None
    return TokenOut(access_token=create_access_token(user, remember=False), user=out)


@router.post("/invitations/reject")
def reject_invitation(data: InvitationReject, db: Session = Depends(get_db)):
    inv = crud.find_invitation_by_token(db, data.token)
    if inv is None or inv.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or already-used invitation")
    crud.set_invitation_status(db, inv, "rejected")
    return {"message": "Invitation declined."}


# ── Support cases (ZST-EC-001 SUP-001 .. SUP-004, customer side) ────────────────────────
# THE authorization fix. Support cases used to exist only on the `/admin` router, which is
# gated at router level by require_super_admin - so a customer could not open or even read
# their own case. These routes put case creation and the customer's own half of the
# lifecycle on the ORGANIZATION router, using the same get_my_org / require_org_admin
# pattern as every other tenant route.
#
# Tenant isolation is unchanged, and deliberately stronger than the old admin route: the
# organization comes from the caller's own token via get_my_org, never from the request
# body, so there is no org_id field a customer could point at somebody else's tenant.

@router.post("/support-cases", status_code=status.HTTP_201_CREATED)
def open_support_case(data: SupportCaseCreate, background: BackgroundTasks,
                      admin: User = Depends(require_org_admin),
                      org: Organization = Depends(get_my_org),
                      db: Session = Depends(get_db)):
    """Open a support case for the caller's OWN organization.

    Gated on org_admin, the same authority that manages members and billing. Priority is
    taken as submitted by that authorized caller and defaults to normal - it is never
    inferred from how urgent the description sounds.
    """
    ticket = support_comms.open_case(
        db, org=org, requester=admin, subject=data.subject, description=data.description,
        category=data.category, priority=data.priority, sensitivity=data.sensitivity)
    if ticket is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"category must be one of {list(CASE_CATEGORIES)}, sensitivity one of "
            f"{list(CASE_SENSITIVITIES)}, and priority a valid support priority")
    admin_crud.create_audit_log(db, actor=admin, action="support_case.open",
                                target_type="support_ticket", target_id=ticket.id,
                                org_id=org.id,
                                meta={"case_reference": ticket.case_reference,
                                      "category": ticket.category})
    support_comms.notify_case_opened(db, background, ticket)
    return _support_case_out(db, ticket)


@router.get("/support-cases")
def list_support_cases(user: User = Depends(get_current_user),
                       org: Organization = Depends(get_my_org),
                       db: Session = Depends(get_db)):
    """The caller's own organization's cases. Org-scoped, so no cross-tenant read."""
    rows = db.scalars(
        select(SupportTicket).where(SupportTicket.org_id == org.id)
        .order_by(SupportTicket.created_at.desc())).all()
    return [_support_case_out(db, t) for t in rows]


def _get_own_case(db, org, ticket_id) -> SupportTicket:
    ticket = db.get(SupportTicket, ticket_id)
    # 404 rather than 403 for another tenant's case: a customer must not be able to probe
    # whether a case reference exists in somebody else's organization.
    if ticket is None or ticket.org_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support case not found")
    return ticket


def _support_case_out(db, ticket: SupportTicket) -> dict:
    """Customer-safe projection. internal_notes is deliberately absent."""
    return {
        "id": str(ticket.id),
        "case_reference": ticket.case_reference,
        "subject": ticket.subject,
        "description": ticket.message,
        "category": ticket.category,
        "category_label": CASE_CATEGORY_LABELS.get(ticket.category or "other", "Other"),
        "priority": ticket.priority,
        "status": ticket.status,
        "owner": ticket.assigned_owner,
        "next_update_at": ticket.next_update_at,
        "customer_update": ticket.customer_update,
        "pending_action": ticket.pending_action,
        "pending_action_due_at": ticket.pending_action_due_at,
        "resolution_summary": ticket.resolution_summary,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
        "reopened_at": ticket.reopened_at,
        "created_at": ticket.created_at,
        "participants": [{"email": p.email, "role": p.role}
                         for p in support_comms.participants(db, ticket)],
    }


@router.get("/support-cases/{ticket_id}")
def get_support_case(ticket_id: uuid.UUID, user: User = Depends(get_current_user),
                     org: Organization = Depends(get_my_org),
                     db: Session = Depends(get_db)):
    return _support_case_out(db, _get_own_case(db, org, ticket_id))


@router.post("/support-cases/{ticket_id}/participants",
             status_code=status.HTTP_201_CREATED)
def add_support_case_participant(ticket_id: uuid.UUID, data: SupportParticipantAdd,
                                 admin: User = Depends(require_org_admin),
                                 org: Organization = Depends(get_my_org),
                                 db: Session = Depends(get_db)):
    """Add somebody explicitly to one case.

    Case membership is the ONLY thing that puts an address on the recipient list, which is
    what stops a case notice reaching every organization member.
    """
    ticket = _get_own_case(db, org, ticket_id)
    row = support_comms.add_participant(db, ticket, email=data.email,
                                        display_name=data.display_name,
                                        added_by=admin.id)
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid email is required")
    return {"email": row.email, "role": row.role}


@router.post("/support-cases/{ticket_id}/action-complete")
def complete_support_case_action(ticket_id: uuid.UUID, background: BackgroundTasks,
                                 admin: User = Depends(require_org_admin),
                                 org: Organization = Depends(get_my_org),
                                 db: Session = Depends(get_db)):
    """The customer supplies what was asked for, which clears the pending action.

    Clearing it is what stops the reminder: reminder_due() reads the same committed state.
    """
    ticket = _get_own_case(db, org, ticket_id)
    if not support_comms.complete_customer_action(db, ticket):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This case is {ticket.status}, not waiting for you")
    return {"status": ticket.status}


@router.post("/support-cases/{ticket_id}/reopen")
def reopen_support_case(ticket_id: uuid.UUID, data: SupportReopen,
                        background: BackgroundTasks,
                        admin: User = Depends(require_org_admin),
                        org: Organization = Depends(get_my_org),
                        db: Session = Depends(get_db)):
    """Customers may reopen their own resolved or closed case.

    A reopen starts a new lifecycle cycle, so the case can legitimately be resolved and
    announced again later.
    """
    ticket = _get_own_case(db, org, ticket_id)
    if not support_comms.reopen(db, ticket, reason=data.reason):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A case that is {ticket.status} cannot be reopened")
    admin_crud.create_audit_log(db, actor=admin, action="support_case.reopen",
                                target_type="support_ticket", target_id=ticket.id,
                                org_id=org.id,
                                meta={"case_reference": ticket.case_reference})
    support_comms.notify_reopened(db, background, ticket)
    return {"status": ticket.status, "reopened_at": ticket.reopened_at}


# ── Security contacts and abuse reporting (ZST-EC-001 SEC-006 / SEC-004) ────────────────
# Customer-side. State commits before any notice, so a Resend outage cannot roll back a
# nomination, a verification, or a filed report.

@router.post("/security-contacts", status_code=status.HTTP_201_CREATED)
def nominate_security_contact(data: SecurityContactCreate, background: BackgroundTasks,
                              admin: User = Depends(require_org_admin),
                              org: Organization = Depends(get_my_org),
                              db: Session = Depends(get_db)):
    """Nominate an address to receive this organization's security mail.

    The address is PENDING until it proves control of the inbox. Only VERIFIED contacts
    receive SEC-001 and SEC-003, which is why this is the foundation the other families
    depend on. Re-nominating supersedes any outstanding token.
    """
    contact, raw = security_comms.nominate_contact(
        db, org, email=data.email, display_name=data.display_name, created_by=admin.id)
    if contact is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid email is required")
    admin_crud.create_audit_log(db, actor=admin, action="security_contact.nominate",
                                target_type="security_contact", target_id=contact.id,
                                org_id=org.id, meta={"email": contact.email})
    security_comms.notify_contact_verification(db, background, contact, raw)
    return {"id": str(contact.id), "email": contact.email, "status": contact.status,
            "verification_expires_at": contact.verification_expires_at}


@router.get("/security-contacts")
def list_security_contacts(user: User = Depends(get_current_user),
                           org: Organization = Depends(get_my_org),
                           db: Session = Depends(get_db)):
    rows = db.scalars(
        select(OrganizationSecurityContact).where(
            OrganizationSecurityContact.org_id == org.id)
        .order_by(OrganizationSecurityContact.created_at)).all()
    # The token hash is never projected, not even to an admin - it has no legitimate reader
    # outside verify_contact().
    return [{"id": str(c.id), "email": c.email, "display_name": c.display_name,
             "status": c.status, "verified_at": c.verified_at,
             "revoked_at": c.revoked_at} for c in rows]


@router.post("/security-contacts/verify")
def verify_security_contact(data: SecurityContactVerify, db: Session = Depends(get_db)):
    """Redeem a verification token.

    Deliberately UNAUTHENTICATED: a security contact is frequently a shared mailbox with no
    platform account, so requiring a session would make the whole concept unusable. The
    token is the credential - purpose-bound, hashed at rest, single-use and expiring.
    """
    contact, outcome = security_comms.verify_contact(db, token=data.token)
    if contact is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            {"invalid": "This verification link is not valid.",
                             "already_used": "This link has already been used.",
                             "superseded": "A newer verification link was issued.",
                             "expired": "This verification link has expired."}[outcome])
    return {"status": contact.status, "verified_at": contact.verified_at}


@router.delete("/security-contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_security_contact(contact_id: uuid.UUID,
                            admin: User = Depends(require_org_admin),
                            org: Organization = Depends(get_my_org),
                            db: Session = Depends(get_db)):
    """Revoke a contact. A revoked address stops receiving security mail immediately and
    its outstanding verification token stops being redeemable."""
    contact = db.get(OrganizationSecurityContact, contact_id)
    if contact is None or contact.org_id != org.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Security contact not found")
    if not security_comms.revoke_contact(db, contact):
        raise HTTPException(status.HTTP_409_CONFLICT, "This contact is already revoked")
    admin_crud.create_audit_log(db, actor=admin, action="security_contact.revoke",
                                target_type="security_contact", target_id=contact.id,
                                org_id=org.id)


@router.post("/abuse-reports", status_code=status.HTTP_201_CREATED)
def file_abuse_report(data: AbuseReportCreate, background: BackgroundTasks,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """File an abuse report.

    The reporter's identity is stored on the report but is NEVER projected into any read
    available to the reported party - see security_comms.reported_party_view(). The
    acknowledgement promises a review and nothing more.
    """
    report = security_comms.file_report(
        db, category=data.category, subject_type=data.subject_type,
        subject_id=data.subject_id, description=data.description,
        reporter_id=user.id, reporter_contact=user.email,
        reporter_name=user.full_name, org_id=data.org_id)
    if report is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"category must be one of {list(ABUSE_CATEGORIES)} and subject_type one of "
            f"{list(ABUSE_SUBJECT_TYPES)}")
    security_comms.notify_report_received(db, background, report)
    return {"reference": report.reference, "status": report.status}


@router.get("/abuse-reports/about-us")
def reports_about_this_organization(user: User = Depends(get_current_user),
                                   org: Organization = Depends(get_my_org),
                                   db: Session = Depends(get_db)):
    """What THIS organization may see about reports filed against it.

    Every field comes from reported_party_view(), which omits the reporter's id, contact,
    name and the free-text description. There is no query parameter, header or role that
    widens this projection - reporter identity is simply not in the response shape.
    """
    rows = db.scalars(
        select(AbuseReport).where(AbuseReport.org_id == org.id)
        .order_by(AbuseReport.created_at.desc())).all()
    return [security_comms.reported_party_view(r) for r in rows]
