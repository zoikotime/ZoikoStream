"""The customer-facing plan catalog — GET /api/organization/plans.

ZST-COM-PLAN-001 Section 03 names three tiers: Developer, Business, Enterprise. `starter` and
`pro` are the pre-Section-03 spellings of the first two (migrate_plan_names.py renames
starter -> developer and pro -> business), so a deployment that has not run that migration
still holds five rows and used to serve all five to the Billing page.

THE DISTINCTION THESE PIN IS RETIRED vs DELETED. The rows stay, because `subscriptions.plan_id`
and `pending_plan_id` point at them and because deleting them would rewrite what a customer was
actually sold. They keep entitling whoever is on them and stay visible to the Super Admin
console. They simply stop being OFFERED.

Structural, not database-backed: what matters is WHERE the filter lives and what it is derived
from, and both are properties of the source.
"""
from _testsupport import code_only

from app.models.plan import RETIRED_PLAN_SLUGS


def test_the_retired_set_is_exactly_the_two_legacy_spellings():
    assert set(RETIRED_PLAN_SLUGS) == {"starter", "pro"}
    # The canonical three must never appear here — retiring one would silently stop the
    # product being sellable.
    for canonical in ("developer", "business", "enterprise"):
        assert canonical not in RETIRED_PLAN_SLUGS


def test_the_customer_catalog_excludes_the_retired_tiers():
    from app.routers import organization
    src = code_only(organization.list_plans)
    assert "RETIRED_PLAN_SLUGS" in src, \
        "the customer-facing catalog must exclude retired tiers"
    # Derived from the shared constant, never a literal spelled again at the call site.
    assert '"pro"' not in src and "'pro'" not in src
    assert '"starter"' not in src and "'starter'" not in src


def test_the_admin_console_still_sees_every_plan():
    """The filter belongs to the customer endpoint alone. Both routes read the same
    crud.list_plans, and an administrator has to be able to see — and fix — a legacy row."""
    from app.crud import admin as admin_crud
    from app.routers import admin as admin_router
    assert "RETIRED_PLAN_SLUGS" not in code_only(admin_crud.list_plans), \
        "filtering in the CRUD would hide legacy plans from the Super Admin console too"
    assert "RETIRED_PLAN_SLUGS" not in code_only(admin_router.list_plans)


def test_no_plan_row_is_deleted_or_deactivated_to_achieve_this():
    """Retirement is a read-side decision. A write here would break the foreign keys that
    existing subscriptions hold, and would change what a customer was sold."""
    from app.routers import organization
    src = code_only(organization.list_plans)
    for write in ("db.delete", "db.add", "db.commit", "is_active =", "update("):
        assert write not in src, f"{write} must not appear in a catalog read"


def test_entitlement_does_not_consult_the_offered_catalog():
    """A tenant ON a retired plan keeps its limits. Entitlements resolve the plan from the
    subscription row, never from the list of what is currently for sale."""
    from app.services import org as org_svc
    for fn in (org_svc.entitlements, org_svc.enforcement_plan):
        assert "RETIRED_PLAN_SLUGS" not in code_only(fn)
        assert "list_plans" not in code_only(fn)


def test_checkout_resolves_a_plan_by_slug_not_from_the_catalog():
    """So excluding a tier from the catalog cannot change how any plan is purchased. A retired
    slug posted directly still fails closed at the price lookup — it has no approved Stripe
    price — rather than being newly reachable or newly broken."""
    from app.routers import organization
    src = code_only(organization.create_subscription_checkout)
    assert "get_plan_by_slug" in src
    assert "list_plans" not in src
    assert "RETIRED_PLAN_SLUGS" not in src


def test_the_frontend_and_backend_retired_sets_agree():
    """Two copies of the rule exist on purpose — the page filters again so the rendered data is
    right against an older server — so they have to be checked against each other."""
    import re
    from pathlib import Path
    # The page filters through planPresentation.visiblePlans, which is where the frontend copy
    # of the rule lives (Billing.jsx imports it rather than re-spelling the slugs).
    jsx = Path(
        "../client/src/pages/organization/planPresentation.js"
    ).read_text(encoding="utf-8")
    m = re.search(r"RETIRED_PLAN_SLUGS = \[([^\]]*)\]", jsx)
    assert m, "planPresentation.js must declare RETIRED_PLAN_SLUGS"
    frontend = {s.strip().strip('"').strip("'") for s in m.group(1).split(",") if s.strip()}
    assert frontend == set(RETIRED_PLAN_SLUGS), (frontend, set(RETIRED_PLAN_SLUGS))
