"""One-time reconciliation: fill `subscriptions.current_period_end` from Stripe.

WHY THIS EXISTS. `current_period_end` is the approved effective date for a scheduled plan
change, but nothing populated it for a Stripe-paid subscription until the webhook began syncing
it. Every subscription created before that carries NULL, and `request_plan_change` refuses with
`no_period_end` rather than inventing a date. This reads the authoritative value from Stripe for
those rows so they become changeable.

WHAT IT WILL NOT DO — each one a deliberate refusal, not an omission:

  * It never FABRICATES a date. There is no "now + 30 days" fallback anywhere. A subscription
    Stripe cannot answer for is reported and skipped.
  * It never MODIFIES Stripe. Only `subscriptions.retrieve` is called — a read. No price, no
    item, no proration, no cancellation.
  * It never changes a PLAN, a status, a cadence or a pending change. `current_period_end` is
    the only column written.
  * It touches only rows where the column is NULL. A value already present is left alone even
    if Stripe now reports a different one — a later period boundary is the webhook's business,
    not a backfill's, and overwriting one could move the effective date of a change a customer
    has already been promised.

Same shape as the other migrate_*.py scripts here: no Alembic, `--dry-run` supported,
idempotent (a second run finds nothing), and never auto-run by the application.

Reads `current_period_end` from the SUBSCRIPTION ITEM first. That is not a stylistic choice: on
the API version this account runs it is absent from the subscription object and present only on
`items.data[].current_period_end`, so a top-level-only read silently yields None. The same
helper the webhook path uses is reused here so the two cannot diverge.

Auditable: writes one `subscription.period_end_reconciled` audit row per subscription changed,
carrying the Stripe subscription id and the value adopted.

    python reconcile_subscription_periods.py --dry-run
    python reconcile_subscription_periods.py
"""

import sys

from sqlalchemy import select

from app.config import settings
from app.crud.admin import create_audit_log
from app.db import SessionLocal
from app.models import Subscription
from app.services.payments_stripe_events import _subscription_period_end


def reconcile_subscription_periods(*, dry_run: bool = False) -> dict:
    """Fill NULL current_period_end from Stripe. Returns a per-outcome summary."""
    if not settings.stripe_configured():
        print("STRIPE_SECRET_KEY is not configured — nothing can be reconciled.")
        return {"updated": 0, "skipped": 0, "unavailable": 0}

    import stripe

    client = stripe.StripeClient(settings.STRIPE_SECRET_KEY.strip())
    updated, skipped, unavailable = 0, 0, 0

    with SessionLocal() as db:
        candidates = db.scalars(
            select(Subscription).where(
                Subscription.stripe_subscription_id.isnot(None),
                Subscription.current_period_end.is_(None),
            )
        ).all()

        print(f"{len(candidates)} subscription(s) have a Stripe reference and no period end.")
        if not candidates:
            return {"updated": 0, "skipped": 0, "unavailable": 0}

        for sub in candidates:
            ref = sub.stripe_subscription_id
            try:
                remote = client.v1.subscriptions.retrieve(ref).to_dict()
            except Exception as exc:                  # noqa: BLE001 — reported, not swallowed
                unavailable += 1
                print(f"  {ref}: Stripe could not be read ({type(exc).__name__}) — skipped")
                continue

            period_end = _subscription_period_end(remote)
            if period_end is None:
                # Stripe knows the subscription but reports no boundary (an incomplete or
                # already-ended subscription). Nothing honest to write.
                skipped += 1
                print(f"  {ref}: Stripe reports no current_period_end — skipped, NOT invented")
                continue

            updated += 1
            if dry_run:
                # WRITE NOTHING in dry-run — not even to be rolled back later.
                # `crud.admin.create_audit_log` COMMITS its own session, so a trailing
                # db.rollback() cannot undo it: an earlier version of this script printed
                # "DRY RUN — rolled back" and had already persisted every change. Skipping the
                # write entirely is the only honest way to preview from this session.
                print(f"  {ref}: WOULD SET current_period_end <- {period_end.isoformat()}")
                continue

            sub.current_period_end = period_end
            create_audit_log(
                db, actor=None, action="subscription.period_end_reconciled",
                target_type="subscription", target_id=sub.id, org_id=sub.org_id,
                meta={"stripe_subscription_id": ref,
                      "current_period_end": period_end.isoformat(),
                      "source": "stripe.subscriptions.retrieve",
                      "note": "one-time backfill; no plan, status or cadence changed"},
            )
            print(f"  {ref}: current_period_end <- {period_end.isoformat()}")

        if dry_run:
            db.rollback()
            print(f"DRY RUN — nothing written ({updated} would change)")
        else:
            db.commit()

    return {"updated": updated, "skipped": skipped, "unavailable": unavailable}


if __name__ == "__main__":
    result = reconcile_subscription_periods(dry_run="--dry-run" in sys.argv)
    print(f"updated={result['updated']} skipped={result['skipped']} "
          f"unavailable={result['unavailable']}")
