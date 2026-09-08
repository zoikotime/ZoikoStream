"""One-off DATA migration: rename the plan catalog to the ZST-COM-PLAN-001 Section 03 tiers.

Section 03 names the tiers Developer, Business and Enterprise. This deployment's catalog was
seeded before that document existed, under an earlier Starter/Pro/Enterprise naming, so
`STRIPE_SUBSCRIPTION_PRICES` (keyed by the Section 03 slugs) matched no plan and every tier
rendered as inquiry-only.

Same pattern as the other migrate_*.py scripts here: this project has no Alembic, schema and
catalog evolve through small idempotent scripts run once against DATABASE_URL.

WHAT THIS CHANGES — and, more importantly, what it does NOT:

    starter  ->  developer      (Starter  -> Developer)
    pro      ->  business       (Pro      -> Business)
    enterprise                  unchanged, the name already matches Section 03

Only `slug` and `name` are written. Every entitlement value (max_users, max_storage_gb,
max_streaming_hours, features), `price_monthly`, `currency` and the row's `id` are left exactly
as they are — so no subscription is re-pointed, no FK moves, and no quota changes. Nothing
commercial is invented here: Section 24 withholds numeric prices, and `price_monthly` is NULL
across this catalog both before and after. The amount, currency and billing interval live on
the Stripe Price, which this script does not touch.

THE ONE JUDGEMENT THIS SCRIPT ENCODES: that the existing Starter tier IS the Section 03
Developer tier and Pro IS Business — i.e. that the catalog is being RENAMED rather than
replaced. That is an ordering-preserving read of a three-tier catalog onto a three-tier
document (entry / mid / unlimited), and it is the only mapping under which no entitlement value
has to be invented. It is a commercial assertion nonetheless, and it is recorded here rather
than buried in a console session.

Idempotent: re-running after a successful migration is a no-op. Refuses rather than guesses if
the catalog is not in a shape this mapping describes.

Reversal is the same script with RENAMES inverted; no data is destroyed either way.
"""

from sqlalchemy import text

from app.db import engine

# old_slug -> (new_slug, new_name). Enterprise is deliberately absent: it already matches.
RENAMES = {
    "starter": ("developer", "Developer"),
    "pro": ("business", "Business"),
}


def migrate_plan_names(*, dry_run: bool = False) -> int:
    """Apply the Section 03 renames. Returns the number of rows changed."""
    changed = 0
    with engine.connect() as conn:
        existing = {slug: name for slug, name in
                    conn.execute(text("SELECT slug, name FROM plans")).all()}
        if not existing:
            print("plans table is empty — nothing to migrate")
            return 0

        print(f"catalog before: {sorted(existing)}")

        for old_slug, (new_slug, new_name) in RENAMES.items():
            if old_slug not in existing:
                if new_slug in existing:
                    print(f"  {old_slug} -> {new_slug}: already migrated, skipping")
                else:
                    print(f"  {old_slug} -> {new_slug}: source plan absent, skipping")
                continue
            if new_slug in existing:
                # Both spellings present. Merging them would mean choosing which row's
                # entitlements survive and re-pointing subscriptions — a data decision this
                # script has no authority to make silently.
                raise SystemExit(
                    f"REFUSING: both '{old_slug}' and '{new_slug}' exist. Merging them would "
                    f"re-point subscriptions and discard one row's entitlements. Resolve by "
                    f"hand, then re-run."
                )
            conn.execute(
                text("UPDATE plans SET slug = :new_slug, name = :new_name WHERE slug = :old_slug"),
                {"new_slug": new_slug, "new_name": new_name, "old_slug": old_slug},
            )
            changed += 1
            print(f"  {old_slug} -> {new_slug}  (name: {existing[old_slug]!r} -> {new_name!r})")

        if dry_run:
            conn.rollback()
            print(f"DRY RUN — rolled back ({changed} row(s) would change)")
            return changed

        conn.commit()
        after = sorted(slug for (slug,) in conn.execute(text("SELECT slug FROM plans")).all())
        print(f"catalog after:  {after}")
    return changed


if __name__ == "__main__":
    import sys
    migrate_plan_names(dry_run="--dry-run" in sys.argv)
