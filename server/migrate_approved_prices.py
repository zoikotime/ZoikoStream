"""One-off DATA migration: publish the approved display prices onto existing plans.

Source of authority: "ZoikoStream / Commercial Estate / Engineering Handoff — Approved Price
Book & Stripe Billing Wireframe v1.0".

    Developer    $49 / month   ($490 / year)
    Business    $249 / month  ($2,490 / year)
    Enterprise   contract / custom pricing — no number, ever

Why this exists: ZST-COM-PLAN-001 Section 24 stated numeric prices were "intentionally not
supplied", so `plans.price_monthly` was seeded NULL and the Billing page rendered "Pricing not
currently published". The price book now supplies them, so the display price is publishable.

WHAT THIS IS NOT: it is not the charging authority. A Checkout Session is always priced from
the Stripe Price named in STRIPE_SUBSCRIPTION_PRICES — never from this column. This only stops
the console showing "not published" for a plan that has an approved, published price.

THE ANNUAL PRICES ARE DELIBERATELY NOT WRITTEN HERE. `plans` has a single `price_monthly`
column and no annual column; inventing one, or stuffing $490 into a monthly field, would
misstate the price. Annual is carried by its own Stripe Price and surfaced through
`STRIPE_SUBSCRIPTION_PRICES` (see `developer:annual=price_...`), which does not exist yet.

Idempotent: re-running changes nothing once applied. Only writes a plan whose price differs,
and never overwrites Enterprise with a number.
"""

from sqlalchemy import text

from app.db import engine

# slug -> (price_monthly, currency). Enterprise is absent on purpose: contract-priced.
APPROVED_MONTHLY = {
    "developer": (49, "USD"),
    "business": (249, "USD"),
}


def migrate_approved_prices(*, dry_run: bool = False) -> int:
    changed = 0
    with engine.connect() as conn:
        rows = {slug: (price, cur) for slug, price, cur in conn.execute(text(
            "SELECT slug, price_monthly, currency FROM plans")).all()}
        if not rows:
            print("plans table is empty — nothing to migrate")
            return 0

        for slug, (price, currency) in APPROVED_MONTHLY.items():
            if slug not in rows:
                print(f"  {slug}: plan absent from this catalog, skipping")
                continue
            current_price, current_cur = rows[slug]
            if current_price is not None and int(current_price) == price and current_cur == currency:
                print(f"  {slug}: already ${price} {currency}, unchanged")
                continue
            conn.execute(
                text("UPDATE plans SET price_monthly = :p, currency = :c WHERE slug = :s"),
                {"p": price, "c": currency, "s": slug},
            )
            changed += 1
            print(f"  {slug}: price_monthly {current_price!r} -> {price} {currency}")

        # Enterprise must present as contract pricing, never as a number or as free.
        if "enterprise" in rows:
            conn.execute(text(
                "UPDATE plans SET custom_pricing = TRUE, price_monthly = NULL "
                "WHERE slug = 'enterprise' "
                "AND (custom_pricing IS NOT TRUE OR price_monthly IS NOT NULL)"))
            print("  enterprise: pinned to custom/contract pricing (no number)")

        if dry_run:
            conn.rollback()
            print(f"DRY RUN — rolled back ({changed} plan(s) would change)")
            return changed
        conn.commit()
    return changed


if __name__ == "__main__":
    import sys
    migrate_approved_prices(dry_run="--dry-run" in sys.argv)
