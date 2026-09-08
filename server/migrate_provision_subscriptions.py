"""One-time backfill: give eligible existing organizations their initial Developer trial.

WHY THIS EXISTS. Self-registration (`routers/auth.py`) never created a subscription row, and
`crud.admin.create_organization` created one only when an admin explicitly named a plan. Almost
every organization was created by the former, so 2149 of 2150 have no subscription at all —
which means they can neither be metered against a plan nor buy one
(`create_subscription_checkout` returns 409 "no subscription record to upgrade"). The code path
is fixed; this backfills the organizations created before the fix.

APPROVED PRODUCT DECISIONS implemented here, none of them invented by this script:
  * Developer plan, `trialing` state, 14 days, no card, no charge, no Stripe subscription.
  * Eligibility: an organization with NO subscription and at least ONE VERIFIED user.
    Suspended organizations ARE included when they meet that same criterion — billing state and
    operational state are orthogonal, and all 16 have verified users and real events.
    Organizations with no user, and organizations whose users are all unverified, are EXCLUDED:
    provisioning a trial for an abandoned registration starts a clock nobody asked for.

THE TRIAL START, and why it is not fabricated. Decision 4 states the trial "starts when the
organization is successfully created/registered AND its initial subscription is provisioned".
For an organization that already exists, the second condition completes now — at migration time.
So the trial starts NOW, and `started_at` records that.

`organizations.created_at` was considered and REJECTED as the origin. It is present for every
row, so it is available; but measured against production it would provision 1947 of 2048
organizations as ALREADY EXPIRED (only 101 would still be inside a trial), handing almost every
real customer a trial that ended before they knew they had one. That is a worse outcome than
either option's ambiguity, and it is not what decision 4 says.

NO STRIPE. This script imports nothing from the Stripe adapter and makes no network call. It
creates no customer, no subscription and no charge.

Idempotent: `provision_initial_subscription` returns None without writing when an organization
already has any subscription row, so a second run — or a resumed batch — changes nothing.

    python migrate_provision_subscriptions.py --dry-run
    python migrate_provision_subscriptions.py --limit 100        # first 100 eligible only
    python migrate_provision_subscriptions.py --batch-size 200
"""

import argparse
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.crud.admin import INITIAL_PLAN_SLUG, get_plan_by_slug, provision_initial_subscription
from app.db import SessionLocal
from app.models import Organization, Subscription, User


def eligible_org_ids(db, *, limit: int | None = None) -> list:
    """Organizations that qualify, deterministically ordered.

    Three predicates, matching the approved rules exactly:
      * NO subscription row of any kind  -> excludes the already-provisioned
      * at least one user with email_verified IS TRUE -> excludes user-less and unverified
      * every organization status is accepted -> suspended organizations are included

    Ordered by `created_at, id` so batching is stable and resumable: a run that stops halfway
    leaves the remainder in the same order for the next run.
    """
    verified_user = (
        select(func.count(User.id))
        .where(User.org_id == Organization.id, User.email_verified.is_(True))
        .correlate(Organization)
        .scalar_subquery()
    )
    has_subscription = (
        select(func.count(Subscription.id))
        .where(Subscription.org_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    stmt = (
        select(Organization.id)
        .where(has_subscription == 0, verified_user > 0)
        .order_by(Organization.created_at, Organization.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def report_legacy_subscriptions(db) -> list:
    """Subscriptions whose trial has no end date — REPORTED, never touched.

    Decision 8: the legacy row with NULL `trial_ends_at` must not be silently modified or
    deleted. There is no deterministic rule in the specification or the data for what its trial
    end should have been, so this script names it for manual review and changes nothing about
    it. Note such a row is also, by definition, excluded from provisioning: it already HAS a
    subscription.
    """
    rows = db.execute(
        select(Subscription.id, Subscription.org_id, Subscription.status,
               Subscription.trial_ends_at, Organization.name)
        .join(Organization, Organization.id == Subscription.org_id)
        .where(Subscription.trial_ends_at.is_(None),
               Subscription.status.in_(("trialing", "trial")))
    ).all()
    return list(rows)


def migrate_provision_subscriptions(*, dry_run: bool = False, batch_size: int = 200,
                                     limit: int | None = None) -> dict:
    provisioned = skipped = 0

    with SessionLocal() as db:
        plan = get_plan_by_slug(db, INITIAL_PLAN_SLUG)
        if plan is None or not plan.is_active:
            print(f"REFUSING: the '{INITIAL_PLAN_SLUG}' plan does not exist or is inactive.")
            print("          Run migrate_plan_names.py first, then re-run this script.")
            return {"provisioned": 0, "skipped": 0, "eligible": 0, "refused": True}

        legacy = report_legacy_subscriptions(db)
        if legacy:
            print(f"\nMANUAL REVIEW — {len(legacy)} subscription(s) have a trial with no end "
                  "date. NOT modified by this script (decision 8):")
            for sub_id, org_id, status, _ends, org_name in legacy:
                print(f"   subscription={sub_id} org={org_id} status={status!r} org_name={org_name!r}")
            print()

        org_ids = eligible_org_ids(db, limit=limit)
        total = len(org_ids)
        print(f"{total} organization(s) eligible: no subscription, at least one verified user.")
        print(f"Provisioning: plan={plan.slug!r} status='trialing' trial=14 days from now. "
              "No card, no charge, no Stripe.")
        if not total:
            return {"provisioned": 0, "skipped": 0, "eligible": 0, "refused": False}

        for start in range(0, total, batch_size):
            batch = org_ids[start:start + batch_size]
            # One timestamp per batch so every organization in it gets an identical, auditable
            # trial window rather than timestamps smeared across the run.
            now = datetime.now(timezone.utc)
            for org_id in batch:
                org = db.get(Organization, org_id)
                if org is None:                       # deleted between query and write
                    skipped += 1
                    continue
                created = provision_initial_subscription(db, org, now=now)
                if created is None:
                    # Raced with another writer, or became ineligible. Never an overwrite.
                    skipped += 1
                else:
                    provisioned += 1

            if dry_run:
                db.rollback()
            else:
                db.commit()
            done = min(start + batch_size, total)
            print(f"  batch {start // batch_size + 1}: {done}/{total} "
                  f"(provisioned={provisioned} skipped={skipped})"
                  f"{' [DRY RUN - rolled back]' if dry_run else ''}")

        if dry_run:
            # Nothing was committed; each batch was rolled back above.
            print(f"\nDRY RUN — nothing written. {provisioned} would be provisioned, "
                  f"{skipped} skipped.")

    return {"provisioned": provisioned, "skipped": skipped, "eligible": total,
            "refused": False}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    ap.add_argument("--batch-size", type=int, default=200,
                    help="organizations committed per transaction (default 200)")
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N eligible organizations (for a staged rollout)")
    args = ap.parse_args()
    result = migrate_provision_subscriptions(
        dry_run=args.dry_run, batch_size=args.batch_size, limit=args.limit)
    print(f"\nprovisioned={result['provisioned']} skipped={result['skipped']} "
          f"eligible={result['eligible']}")
