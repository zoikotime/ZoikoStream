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
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.crud.admin import INITIAL_PLAN_SLUG, get_plan_by_slug, provision_initial_subscription
from app.db import SessionLocal
from app.models import Organization, Subscription, User


class ExclusionInputError(ValueError):
    """Raised when exclusion input files or IDs are malformed or unreadable."""
    pass


def load_exclusions(*, paths: list[str] | None = None, org_ids: list[str] | None = None) -> set[uuid.UUID]:
    """Parse exclusion UUIDs from file paths and command-line arguments.

    Comments (`#`) and empty lines in files are ignored. Malformed entries fail closed:
    every invalid entry is collected and reported in one ExclusionInputError.
    """
    result: set[uuid.UUID] = set()
    errors: list[str] = []

    if paths:
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                raise ExclusionInputError(f"cannot read exclusion file: {p} ({e})") from e

            for lineno, raw_line in enumerate(lines, start=1):
                line = raw_line.strip()
                if not line or line[0] == "#":
                    continue
                token = line.split("#", 1)[0].strip()
                if not token:
                    continue
                try:
                    result.add(uuid.UUID(token))
                except ValueError:
                    errors.append(f"{p}:{lineno}: '{token}' is not a valid organization UUID")

    if org_ids:
        for token in org_ids:
            t = str(token).strip()
            if not t:
                continue
            try:
                result.add(uuid.UUID(t))
            except ValueError:
                errors.append(f"'{t}' is not a valid organization UUID")

    if errors:
        msg = f"{len(errors)} malformed exclusion entry(ies): " + "; ".join(errors)
        raise ExclusionInputError(msg)

    return result


def _eligibility_predicates():
    """Core eligibility rules: no subscription row, and at least one verified user.

    Orthogonal to organization status: whether suspended orgs are in scope is decided
    by Product via explicit exclusions, never inferred here.
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
    return (has_subscription == 0, verified_user > 0)


def eligible_org_ids(db, *, limit: int | None = None, exclude: set | None = None) -> list:
    """Organizations that qualify, deterministically ordered.

    Three predicates, matching the approved rules exactly:
      * NO subscription row of any kind  -> excludes the already-provisioned
      * at least one user with email_verified IS TRUE -> excludes user-less and unverified
      * every organization status is accepted -> suspended organizations are included

    Ordered by `created_at, id` so batching is stable and resumable. Exclusions are applied
    before the limit.
    """
    has_subscription_zero, verified_user_gt_zero = _eligibility_predicates()
    stmt = (
        select(Organization.id)
        .where(has_subscription_zero, verified_user_gt_zero)
    )
    if exclude:
        stmt = stmt.where(Organization.id.notin_(list(exclude)))
    stmt = stmt.order_by(Organization.created_at, Organization.id)
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def provisioning_scope(db, *, exclude: set | None = None, limit: int | None = None) -> dict:
    """Audit the database population against the eligibility rules and supplied exclusions."""
    exclude_set = set(exclude or ())
    exclusions_supplied = len(exclude_set)

    excluded_from_eligible = []
    excluded_already_provisioned = []
    excluded_not_eligible = []
    excluded_unknown_org_ids = []

    if exclude_set:
        org_rows = db.execute(
            select(
                Organization.id,
                select(func.count(Subscription.id)).where(Subscription.org_id == Organization.id).correlate(Organization).scalar_subquery().label("sub_count"),
                select(func.count(User.id)).where(User.org_id == Organization.id, User.email_verified.is_(True)).correlate(Organization).scalar_subquery().label("verified_count"),
            ).where(Organization.id.in_(list(exclude_set)))
        ).all()
        found_map = {row.id: (row.sub_count, row.verified_count) for row in org_rows}

        for x in sorted(exclude_set, key=lambda u: str(u)):
            if x not in found_map:
                excluded_unknown_org_ids.append(str(x))
            else:
                sub_count, verified_count = found_map[x]
                if sub_count > 0:
                    excluded_already_provisioned.append(str(x))
                elif verified_count > 0:
                    excluded_from_eligible.append(str(x))
                else:
                    excluded_not_eligible.append(str(x))

    sub_count_subq = (
        select(func.count(Subscription.id))
        .where(Subscription.org_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    user_count_subq = (
        select(func.count(User.id))
        .where(User.org_id == Organization.id)
        .correlate(Organization)
        .scalar_subquery()
    )
    verified_user_subq = (
        select(func.count(User.id))
        .where(User.org_id == Organization.id, User.email_verified.is_(True))
        .correlate(Organization)
        .scalar_subquery()
    )

    total_orgs = db.scalar(select(func.count(Organization.id))) or 0
    already_provisioned = db.scalar(select(func.count(Organization.id)).where(sub_count_subq > 0)) or 0
    ineligible_no_users = db.scalar(select(func.count(Organization.id)).where(sub_count_subq == 0, user_count_subq == 0)) or 0
    ineligible_users_none_verified = db.scalar(select(func.count(Organization.id)).where(sub_count_subq == 0, user_count_subq > 0, verified_user_subq == 0)) or 0

    all_eligible_ids = eligible_org_ids(db)
    eligible_before_exclusions = len(all_eligible_ids)

    final_ids = eligible_org_ids(db, limit=limit, exclude=exclude_set)
    final_provisioning_count = len(final_ids)

    final_active = 0
    final_suspended = 0
    if final_ids:
        status_rows = db.execute(
            select(Organization.status, func.count(Organization.id))
            .where(Organization.id.in_(final_ids))
            .group_by(Organization.status)
        ).all()
        for st, count in status_rows:
            if st == "active":
                final_active = count
            elif st == "suspended":
                final_suspended = count

    return {
        "total_organizations": total_orgs,
        "eligible_before_exclusions": eligible_before_exclusions,
        "exclusions_supplied": exclusions_supplied,
        "excluded_from_eligible": excluded_from_eligible,
        "excluded_already_provisioned": excluded_already_provisioned,
        "excluded_not_eligible": excluded_not_eligible,
        "excluded_unknown_org_ids": excluded_unknown_org_ids,
        "ineligible_no_users": ineligible_no_users,
        "ineligible_users_none_verified": ineligible_users_none_verified,
        "already_provisioned": already_provisioned,
        "limit_applied": limit,
        "final_provisioning_count": final_provisioning_count,
        "final_active": final_active,
        "final_suspended": final_suspended,
        "final_ids": final_ids,
    }


def print_scope(scope: dict) -> None:
    """Print the provisioning scope accounting."""
    print(f"Total organizations considered: {scope['total_organizations']}")
    print(f"ELIGIBLE before exclusions: {scope['eligible_before_exclusions']}")
    print(f"Exclusions supplied by Product: {scope['exclusions_supplied']}")
    for cat in ("excluded_from_eligible", "excluded_already_provisioned",
                "excluded_not_eligible", "excluded_unknown_org_ids"):
        ids = scope.get(cat, [])
        if ids:
            print(f"  {cat} ({len(ids)}):")
            for oid in ids:
                print(f"    {oid}")
    print(f"FINAL provisioning count: {scope['final_provisioning_count']}")
    print(f"  active: {scope['final_active']}")
    print(f"  suspended: {scope['final_suspended']}")


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
                                     limit: int | None = None,
                                     exclude: set | None = None) -> dict:
    provisioned = skipped = 0

    with SessionLocal() as db:
        scope = provisioning_scope(db, exclude=exclude, limit=limit)
        if scope["excluded_unknown_org_ids"]:
            print(f"REFUSING: unknown exclusion org id(s): {scope['excluded_unknown_org_ids']}")
            return {"provisioned": 0, "skipped": 0, "eligible": 0, "refused": True, "scope": scope}

        plan = get_plan_by_slug(db, INITIAL_PLAN_SLUG)
        if plan is None or not plan.is_active:
            print(f"REFUSING: the '{INITIAL_PLAN_SLUG}' plan does not exist or is inactive.")
            print("          Run migrate_plan_names.py first, then re-run this script.")
            return {"provisioned": 0, "skipped": 0, "eligible": 0, "refused": True, "scope": scope}

        legacy = report_legacy_subscriptions(db)
        if legacy:
            print(f"\nMANUAL REVIEW — {len(legacy)} subscription(s) have a trial with no end "
                  "date. NOT modified by this script (decision 8):")
            for sub_id, org_id, status, _ends, org_name in legacy:
                print(f"   subscription={sub_id} org={org_id} status={status!r} org_name={org_name!r}")
            print()

        org_ids = scope["final_ids"]
        total = len(org_ids)
        print(f"{total} organization(s) eligible: no subscription, at least one verified user.")
        print(f"Provisioning: plan={plan.slug!r} status='trialing' trial=14 days from now. "
              "No card, no charge, no Stripe.")
        print_scope(scope)

        if dry_run:
            print(f"\nDRY RUN — nothing written. {total} would be provisioned, 0 skipped.")
            return {"provisioned": total, "skipped": 0, "eligible": total, "refused": False, "scope": scope}

        if not total:
            return {"provisioned": 0, "skipped": 0, "eligible": 0, "refused": False, "scope": scope}

        for start in range(0, total, batch_size):
            batch = org_ids[start:start + batch_size]
            now = datetime.now(timezone.utc)
            for org_id in batch:
                org = db.get(Organization, org_id)
                if org is None:
                    skipped += 1
                    continue
                created = provision_initial_subscription(db, org, now=now)
                if created is None:
                    skipped += 1
                else:
                    provisioned += 1

            db.commit()
            done = min(start + batch_size, total)
            print(f"  batch {start // batch_size + 1}: {done}/{total} "
                  f"(provisioned={provisioned} skipped={skipped})")

    return {"provisioned": provisioned, "skipped": skipped, "eligible": total,
            "refused": False, "scope": scope}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change and write nothing")
    ap.add_argument("--batch-size", type=int, default=200,
                    help="organizations committed per transaction (default 200)")
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N eligible organizations (for a staged rollout)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="organization ID to exclude (repeatable)")
    ap.add_argument("--exclude-file", action="append", default=[],
                    help="path to file of organization IDs to exclude (repeatable)")
    args = ap.parse_args()

    exclude_set = load_exclusions(paths=args.exclude_file, org_ids=args.exclude)
    result = migrate_provision_subscriptions(
        dry_run=args.dry_run, batch_size=args.batch_size, limit=args.limit, exclude=exclude_set)
    print(f"\nprovisioned={result['provisioned']} skipped={result['skipped']} "
          f"eligible={result['eligible']}")

