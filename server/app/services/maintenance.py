"""Scheduled commercial maintenance (ZST-LE-COM-001 Sections 7/20/29).

There is no in-process job runner in this codebase and this module does not add one. A
background thread in a Cloud Run container is the wrong place for financial housekeeping: it
runs N times for N instances, dies mid-sweep on a scale-down, and cannot be observed. Instead
every job is a plain function plus one authenticated endpoint
(`POST /api/commercial/maintenance/run`) for an external scheduler — Cloud Scheduler, a cron
container, or an operator — to drive. Idempotent by construction, so a double-fire is harmless.

Five jobs:

  expire_capacity_holds      lapsed soft holds -> EXPIRED, inventory returned
  release_stale_reservations hard reservations whose window has passed on an event that never
                             delivered -> RELEASED, inventory returned
  expire_replay_entitlements published replays past their retention window -> EXPIRED (state
                             only; the media object is never deleted by a sweep)
  report_stale_payments      payment attempts stuck mid-flight, REPORTED not deleted
  report_unmatched_settlements  open unattributed money, aged and alerted

What none of them do is delete or rewrite a financial record. "Failed payment cleanup" is
implemented as reporting, deliberately: a payment row is the evidence that an attempt happened,
and a sweep that removed them would destroy exactly what a reconciliation needs. Money records
are additive (doc T4) — the job surfaces them for a human, it does not tidy them away.

Thresholds come from the platform settings registry, not from constants here. An unconfigured
threshold means the job reports `skipped` rather than assuming a window (doc Section 26 "no
hard-coded fallback").
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..crud import commercial as crud
from ..models import CapacityReservation, Event, Payment, User
from . import platform_settings

log = logging.getLogger(__name__)

# Payment states that mean "an attempt is in flight but no money has settled". A row sitting
# here long after it was created is an abandoned checkout or a provider event we never
# received — worth a human's attention, never worth deleting.
_IN_FLIGHT_PAYMENT_STATES = ("requires_action", "pending")


def expire_capacity_holds(db: Session, *, actor: User | None = None) -> dict:
    """Sweep soft holds whose governed period has lapsed (doc C2).

    Also runs opportunistically inside soft_hold_capacity, so a busy pool self-heals. This is
    the path that matters on a QUIET pool: with no new hold attempts, a lapsed hold would
    otherwise keep occupying inventory indefinitely.
    """
    expired = crud.expire_stale_soft_holds(db, actor=actor)
    return {"job": "expire_capacity_holds", "expired": expired}


def release_stale_reservations(db: Session, *, actor: User | None = None) -> dict:
    """Release hard reservations whose window has passed on an event that never delivered.

    A hard reservation does not expire — that is correct, it is a commitment. But once its
    window has passed and the event never went live, the commitment is spent and the inventory
    is being held against nothing. Left alone it silently shrinks a pool forever.

    Deliberately narrow. Only releases a reservation whose window END is in the past AND whose
    event never reached a delivering/delivered state. A CONSUMED reservation is left alone (it
    records real usage), and a reservation on a live or completed event is left alone (it is
    the evidence of what was committed to a delivery that happened).
    """
    now = datetime.now(timezone.utc)
    # Events that did deliver, or are delivering — their reservations are historical evidence.
    delivered_states = crud.PRODUCTION_EVENT_STATES + crud._EVENT_STATES_COMPLETED
    candidates = db.scalars(
        select(CapacityReservation).where(
            CapacityReservation.state == "hard_reserved",
            CapacityReservation.window_end.is_not(None),
            CapacityReservation.window_end < now,
        )
    ).all()
    released = 0
    for reservation in candidates:
        event = db.get(Event, reservation.event_id)
        if event is not None and event.status in delivered_states:
            continue
        crud.release_capacity(
            db, reservation, "stale_reservation_window_passed", actor=actor)
        released += 1
    return {"job": "release_stale_reservations", "released": released,
            "examined": len(candidates)}


def expire_replay_entitlements(db: Session, *, actor: User | None = None) -> dict:
    """Mark published replays past their retention window as EXPIRED (doc Section 14/J).

    Needs no configured threshold: the window is per-entitlement (`ReplayEntitlement.
    expires_at`), set when the entitlement was created. An entitlement with no expiry has no
    retention limit and is left alone.

    State only — the media object is not deleted. See crud.expire_lapsed_replay_entitlements.
    """
    expired = crud.expire_lapsed_replay_entitlements(db, actor=actor)
    return {"job": "expire_replay_entitlements", "expired": expired}


def report_stale_payments(db: Session, *, actor: User | None = None) -> dict:
    """Surface payment attempts stuck in flight past the configured window.

    NOT a cleanup in the deleting sense — see the module docstring. Each stale attempt is
    audited so it lands in the same trail Finance already reads, and returned for the caller
    to alert on. The Payment row itself is untouched: only a provider event may move a
    payment's state, and a maintenance job is not a provider.
    """
    hours = platform_settings.stale_payment_window_hours(db)
    if hours is None:
        return {"job": "report_stale_payments", "skipped": "no stale-payment window configured"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stale = db.scalars(
        select(Payment).where(
            Payment.state.in_(_IN_FLIGHT_PAYMENT_STATES),
            Payment.created_at < cutoff,
        )
    ).all()
    for payment in stale:
        crud.audit(db, actor=actor, action="commercial.maintenance.stale_payment",
                   target_type="payment", target_id=payment.id,
                   org_id=crud._order_org_id(db, payment.event_order_id),
                   reason=f"payment has been '{payment.state}' for more than {hours}h",
                   state=payment.state, amount=str(payment.amount),
                   currency=payment.currency, provider=payment.provider,
                   checkout_session_ref=payment.checkout_session_ref)
    if stale:
        db.commit()
    return {"job": "report_stale_payments", "window_hours": hours, "stale": len(stale),
            "payment_ids": [str(p.id) for p in stale]}


def report_unmatched_settlements(db: Session, *, actor: User | None = None) -> dict:
    """Alert on provider money still unattributed past the configured window (doc P5).

    The settlements are already retained and already visible on the reconciliation report; what
    was missing is the passage of time becoming a signal. Unattributed money that is hours old
    is a queue item; unattributed money that is days old is a problem.
    """
    hours = platform_settings.unmatched_settlement_alert_hours(db)
    open_settlements = crud.list_open_unmatched_settlements(db)
    if hours is None:
        return {"job": "report_unmatched_settlements", "open": len(open_settlements),
                "skipped": "no alert window configured"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    aged = [s for s in open_settlements if s.received_at and s.received_at < cutoff]
    for settlement in aged:
        crud.audit(db, actor=actor, action="commercial.maintenance.unmatched_settlement_aged",
                   target_type="unmatched_settlement", target_id=settlement.id,
                   correlation_id=settlement.correlation_id,
                   reason=f"unattributed for more than {hours}h",
                   provider=settlement.provider,
                   provider_payment_ref=settlement.provider_payment_ref,
                   amount=None if settlement.amount is None else str(settlement.amount),
                   currency=settlement.currency)
    if aged:
        db.commit()
    return {"job": "report_unmatched_settlements", "window_hours": hours,
            "open": len(open_settlements), "aged": len(aged),
            "settlement_ids": [str(s.id) for s in aged]}


def expire_commercial_overrides(db: Session, *, actor: User | None = None) -> dict:
    """Mark approved commercial overrides whose expiry has passed as `expired`.

    ZST-COM-PLAN-001 Section 19 requires manual overrides be "time-bound ... [with] automatic
    expiry". Access control already refuses a lapsed override at read time
    (crud.admin.active_overrides filters on expires_at), so this job is about the STORED state
    agreeing with reality — a console listing must not show a lapsed grant as still approved.
    Correctness never depends on this job having run.

    Idempotent: a second run finds nothing, because the first moved the rows out of `approved`.
    """
    from ..crud import admin as admin_crud

    expired = admin_crud.expire_lapsed_overrides(db)
    db.commit()
    return {"job": "expire_commercial_overrides", "expired": expired}


def apply_due_plan_changes(db: Session, *, actor: User | None = None) -> dict:
    """Put scheduled subscription plan changes into effect once their date has arrived.

    This is the clock behind Section 12's `PLAN_CHANGE_SCHEDULED -> ACTIVE`. The approved
    effective date is the subscription's own `current_period_end`, so a change becomes due
    exactly when the period the customer already paid for has ended — no proration arises
    because no mid-cycle boundary is ever crossed.

    Order matters and is deliberate: OUR record moves first, then Stripe is asked to swap the
    price. If Stripe fails, the local change stands and is reported — the tenant is on the plan
    they asked for from the date they were promised, and the provider is reconciled on retry or
    by the next `customer.subscription.updated` webhook. The alternative (Stripe first) would
    let a provider timeout leave a customer billed for a plan our record denies them.

    Idempotent twice over: `apply_plan_change` clears `pending_*` as part of applying, so a
    second sweep finds nothing; and the Stripe call carries a per-subscription idempotency key,
    so a retry cannot swap twice or invoice twice.
    """
    from ..config import settings
    from ..crud import admin as admin_crud
    from . import payments as payment_svc

    applied, failed, provider_failed, contended = [], [], [], 0
    # Candidate ids first, then an ATOMIC per-row claim. The batch query's lock is dropped as
    # soon as the first subscription is applied (create_audit_log commits its own session), so
    # re-claiming each row individually is what actually prevents two concurrent runners — a
    # scheduler double-fire, a retry, or an operator running this by hand — from applying the
    # same change twice and writing two `plan_change_applied` rows.
    candidate_ids = [s.id for s in admin_crud.due_plan_changes(db)]
    db.rollback()                     # release the batch lock; each row is re-claimed below

    for sub_id in candidate_ids:
        sub = admin_crud.claim_due_plan_change(db, sub_id)
        if sub is None:
            # Held by another worker, or already applied by one. Either way not ours.
            contended += 1
            db.rollback()
            continue
        target = sub.pending_plan_id
        ok, error = admin_crud.apply_plan_change(db, sub, actor=actor)
        if not ok:
            db.commit()                       # keep the rejection audit row
            if error:
                failed.append({"subscription_id": str(sub.id), "error": error})
            continue
        db.commit()
        applied.append({"subscription_id": str(sub.id), "plan_id": str(sub.plan_id),
                        "billing_interval": sub.billing_interval})

        # Reconcile the provider. Skipped when there is nothing to reconcile against.
        if not (sub.stripe_subscription_id and settings.stripe_configured()):
            continue
        try:
            plan = db.get(admin_crud.Plan, sub.plan_id)
            price_id = admin_crud.resolve_subscription_price_id(plan, sub.billing_interval)
            provider = payment_svc.get_provider("stripe")
            provider.change_subscription_price(
                sub.stripe_subscription_id, price_id=price_id,
                # Stable per subscription AND per target, so a retry of THIS change collapses
                # onto one Stripe operation while a genuinely later change gets its own key.
                idempotency_key=f"{sub.id}:{target}:{sub.billing_interval}",
            )
            admin_crud.create_audit_log(
                db, actor=actor, action="subscription.plan_change_provider_synced",
                target_type="subscription", target_id=sub.id, org_id=sub.org_id,
                meta={"stripe_subscription_id": sub.stripe_subscription_id,
                      "price_id": price_id, "proration": "none"},
            )
            db.commit()
        except Exception as exc:              # noqa: BLE001 — recorded, never swallowed
            db.rollback()
            admin_crud.create_audit_log(
                db, actor=actor, action="subscription.plan_change_provider_failed",
                target_type="subscription", target_id=sub.id, org_id=sub.org_id,
                meta={"stripe_subscription_id": sub.stripe_subscription_id,
                      "error": f"{type(exc).__name__}: {exc}"},
            )
            db.commit()
            provider_failed.append({"subscription_id": str(sub.id),
                                    "error": type(exc).__name__})
            log.exception("plan change applied locally but Stripe sync failed for %s", sub.id)

    return {"job": "apply_due_plan_changes", "applied": len(applied),
            "rejected": len(failed), "provider_failed": len(provider_failed),
            # Rows another concurrent runner held or had already applied. Reported rather than
            # hidden so a scheduler double-fire is visible in the job result instead of looking
            # like a run that found nothing to do.
            "contended": contended,
            "details": {"applied": applied, "rejected": failed,
                        "provider_failed": provider_failed}}


JOBS = (
    expire_capacity_holds,
    release_stale_reservations,
    expire_replay_entitlements,
    report_stale_payments,
    report_unmatched_settlements,
    expire_commercial_overrides,
    apply_due_plan_changes,
)


def run_all(db: Session, *, actor: User | None = None) -> dict:
    """Run every maintenance job, collecting results.

    One job failing does not stop the others: these are independent housekeeping tasks and a
    capacity sweep should not be skipped because a settlement query failed. Each failure is
    captured in the result and logged, so a scheduler sees a 200 with the failure named rather
    than an opaque 500 that hides the three jobs that did work.
    """
    results = []
    for job in JOBS:
        try:
            results.append(job(db, actor=actor))
        except Exception as exc:                      # noqa: BLE001 — reported, not swallowed
            db.rollback()
            log.exception("maintenance job %s failed", job.__name__)
            results.append({"job": job.__name__, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "jobs": results,
        "failed": [r["job"] for r in results if "error" in r],
    }
