"""Phase 2 commercial-foundation tests (ZST-LE-COM-001).

Covers ONLY what this phase built: pricing provenance, capacity inventory with
oversubscription safety, atomic invoice numbering, the seller legal entity registry, and
the tax NOT_DETERMINED vs explicit-ZERO distinction.

Split by what each check actually needs:
  * pure-logic / stub-session tests run everywhere (no DB, no network)
  * tests marked `needs_db` exercise real Postgres semantics — row locks, ON CONFLICT
    allocation, unique constraints — which cannot be faked. They skip when DATABASE_URL is
    unreachable rather than failing, so the suite stays runnable offline.

Run: `python -m pytest test_commercial_foundation.py -q`
"""
import ast
import concurrent.futures
import inspect
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import commercial as crud
from _testsupport import code_only
from app.db import engine
from app.models import (
    CapacityPool, CatalogLine, CatalogVersion, CommercialAccount, Event, EventOrder,
    Organization, Plan, SellerLegalEntity, User,
)
from app.schemas.admin import PlanOut


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════════════
# A. PRICING — provenance and the three published states
# ══════════════════════════════════════════════════════════════════════════════════════

def _plan(**over):
    fields = dict(id=uuid.uuid4(), name="Pro", slug="pro", price_monthly=None,
                  custom_pricing=False, currency="USD", is_active=True, features=[])
    fields.update(over)
    return PlanOut.model_validate(SimpleNamespace(**fields))


def test_unpublished_plan_price_is_not_published_not_zero():
    plan = _plan(price_monthly=None)
    assert plan.pricing_state == "NOT_PUBLISHED"
    assert plan.price_monthly is None  # never coerced to 0


def test_custom_pricing_is_distinct_from_unpublished():
    assert _plan(price_monthly=None, custom_pricing=True).pricing_state == "CUSTOM"


def test_approved_price_is_published():
    plan = _plan(price_monthly=149.0)
    assert plan.pricing_state == "PUBLISHED" and plan.price_monthly == 149.0


def test_an_explicit_zero_platform_price_is_still_published():
    """0.00 as a DELIBERATE price (a genuine free tier) must stay distinguishable from an
    absent price — the whole reason price_monthly became nullable."""
    assert _plan(price_monthly=0.0).pricing_state == "PUBLISHED"


def test_platform_plan_price_cannot_become_a_live_event_price():
    """Ledger separation (doc Section 3). The Live Event pricing path reads CatalogLine only;
    no commercial code may reach for Plan. Asserted structurally rather than by behaviour,
    because the guarantee IS the absence of a reference."""
    from app.crud import commercial as commercial_crud
    from app.routers import commercial as commercial_router

    for module in (commercial_crud, commercial_router):
        src = code_only(module)
        assert "Plan" not in src.replace("PlanOut", ""), f"{module.__name__} references Plan"
        assert "price_monthly" not in src, f"{module.__name__} references price_monthly"
        assert "Subscription" not in src, f"{module.__name__} references Subscription"


def test_order_line_price_is_traceable_to_its_catalog_version():
    """A line must carry the full commercial basis: service_code + price + unit_basis +
    tax_treatment, under an order that names the catalog version (doc B2)."""
    order = SimpleNamespace(
        id=uuid.uuid4(), status="draft", currency="USD",
        catalog_version_id=uuid.uuid4(), subtotal=Decimal(0), total_amount=Decimal(0),
        tax_amount=None,
    )
    line = CatalogLine(
        id=uuid.uuid4(), catalog_version_id=order.catalog_version_id, service_code="MEM-MANAGED",
        name="Managed memorial broadcast", unit_price=Decimal("1200.00"), currency="USD",
        unit_basis="per_event", tax_treatment="standard_rate",
    )
    captured = {}

    class _S:
        def add(self, obj):
            captured["line"] = obj
        def commit(self):
            pass
        def refresh(self, obj):
            pass

    crud.add_order_line(_S(), order, line, quantity=Decimal(1))
    saved = captured["line"]
    assert saved.service_code == "MEM-MANAGED"
    assert saved.unit_price == Decimal("1200.00")
    assert saved.unit_basis == "per_event"          # frozen, not looked up later
    assert saved.tax_treatment == "standard_rate"
    assert saved.catalog_line_id == line.id


def test_order_line_from_a_foreign_catalog_version_is_rejected():
    order = SimpleNamespace(id=uuid.uuid4(), status="draft", currency="USD",
                            catalog_version_id=uuid.uuid4(), subtotal=Decimal(0),
                            total_amount=Decimal(0), tax_amount=None)
    foreign = CatalogLine(catalog_version_id=uuid.uuid4(), service_code="X", name="X",
                          unit_price=Decimal("10.00"), currency="USD", unit_basis="per_event")
    with pytest.raises(ValueError, match="different catalog version"):
        crud.add_order_line(SimpleNamespace(), order, foreign)


def test_order_line_currency_must_match_the_order():
    """Doc L2: one currency per legal financial document."""
    cv = uuid.uuid4()
    order = SimpleNamespace(id=uuid.uuid4(), status="draft", currency="USD", catalog_version_id=cv,
                            subtotal=Decimal(0), total_amount=Decimal(0), tax_amount=None)
    gbp = CatalogLine(catalog_version_id=cv, service_code="X", name="X",
                      unit_price=Decimal("10.00"), currency="GBP", unit_basis="per_event")
    with pytest.raises(ValueError, match="one currency per order"):
        crud.add_order_line(SimpleNamespace(), order, gbp)


def test_unpriced_catalog_line_cannot_be_ordered():
    cv = uuid.uuid4()
    order = SimpleNamespace(id=uuid.uuid4(), status="draft", currency="USD", catalog_version_id=cv,
                            subtotal=Decimal(0), total_amount=Decimal(0), tax_amount=None)
    unpriced = CatalogLine(catalog_version_id=cv, service_code="X", name="X",
                           unit_price=None, currency=None, unit_basis="per_event")
    with pytest.raises(ValueError, match="no price/currency"):
        crud.add_order_line(SimpleNamespace(), order, unpriced)


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_order_cannot_be_priced_from_an_unpublished_catalog(status):
    cv = CatalogVersion(id=uuid.uuid4(), version_label="v1", vertical="memorials", status=status)
    with pytest.raises(ValueError, match="not\n?\\s*published|published"):
        crud.create_order(
            SimpleNamespace(), SimpleNamespace(id=uuid.uuid4()),
            commercial_account=SimpleNamespace(id=uuid.uuid4()), catalog_version=cv,
            purchaser_type="organization", purchaser_id=None, service_profile=None,
            cancellation_policy=None, currency="USD", idempotency_key="k1",
        )


@pytest.mark.parametrize("status", ["draft", "retired"])
def test_quote_cannot_be_priced_from_an_unpublished_catalog(status):
    cv = CatalogVersion(id=uuid.uuid4(), version_label="v1", vertical="memorials", status=status)
    with pytest.raises(ValueError, match="published"):
        crud.create_quote(SimpleNamespace(), SimpleNamespace(id=uuid.uuid4()), catalog_version=cv,
                          amount=Decimal("10.00"), tax_amount=None, currency="USD",
                          created_by=SimpleNamespace(id=uuid.uuid4()), valid_until=None)


# ══════════════════════════════════════════════════════════════════════════════════════
# B. CAPACITY — inventory, time-awareness, oversubscription, expiry
# ══════════════════════════════════════════════════════════════════════════════════════

def test_capacity_requires_an_explicit_window():
    """Live event capacity is time-specific (doc C1/C5) — an open-ended hold cannot be
    checked against a time-bounded pool."""
    with pytest.raises(ValueError, match="explicit window"):
        crud.soft_hold_capacity(SimpleNamespace(), SimpleNamespace(id=uuid.uuid4()),
                                resource_type="production_operator", quantity=1)


def test_no_capacity_number_is_assumed_anywhere():
    """Guards the Phase 1 removal: neither the old envelope constant nor any replacement
    default may reappear as CODE (prose recording its removal is fine)."""
    src = code_only(crud)
    assert "DEFAULT_CAPACITY_ENVELOPE" not in src
    assert not hasattr(crud, "DEFAULT_CAPACITY")
    # No module-level int constant that could act as a capacity fallback.
    tree = ast.parse(textwrap.dedent(inspect.getsource(crud)))
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                pytest.fail(f"module-level numeric constant(s) {names} could act as a capacity default")


@needs_db
class TestCapacityInventory:
    """Real Postgres: row locking and concurrent claims cannot be simulated with stubs."""

    @pytest.fixture
    def ctx(self):
        """A throwaway org + event + active pool. Torn down in reverse FK order."""
        with Session(engine) as db:
            org = Organization(name=f"cap-test-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Cap Test", email=f"cap-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"cap{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Cap test event")
            db.add(event)
            db.flush()
            pool = CapacityPool(
                resource_type=f"operator_{uuid.uuid4().hex[:6]}",
                window_start=NOW - timedelta(hours=2), window_end=NOW + timedelta(hours=6),
                total_capacity=2, status="active",
            )
            db.add(pool)
            db.commit()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, event_id=event.id,
                                  pool_id=pool.id, resource_type=pool.resource_type)
        yield ids
        with Session(engine) as db:
            db.execute(text("DELETE FROM capacity_reservations WHERE event_id = :e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM capacity_pools WHERE id = :p"), {"p": ids.pool_id})
            db.execute(text("DELETE FROM events WHERE id = :e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids.user_id})
            db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": ids.org_id})
            db.commit()

    def _hold(self, db, ctx, quantity):
        return crud.soft_hold_capacity(
            db, db.get(Event, ctx.event_id), resource_type=ctx.resource_type,
            window_start=NOW, window_end=NOW + timedelta(hours=2), quantity=quantity,
            actor=db.get(User, ctx.user_id),
        )

    def test_reservation_succeeds_when_capacity_exists(self, ctx):
        with Session(engine) as db:
            r = self._hold(db, ctx, 1)
            assert r.state == "soft_held"
            assert r.capacity_pool_id == ctx.pool_id      # bound to real inventory
            assert r.requested_quantity == 1
            util = crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))
            assert util["reserved_capacity"] == 1 and util["available_capacity"] == 1

    def test_reservation_fails_when_capacity_is_exhausted(self, ctx):
        with Session(engine) as db:
            self._hold(db, ctx, 2)  # pool total is 2
            with pytest.raises(ValueError, match="Insufficient capacity"):
                self._hold(db, ctx, 1)

    def test_reservation_fails_when_no_pool_covers_the_window(self, ctx):
        """Time-aware: the pool ends at NOW+6h, so a window past that is uncovered even
        though the resource type exists and has headroom."""
        with Session(engine) as db:
            with pytest.raises(ValueError, match="No active capacity pool"):
                crud.soft_hold_capacity(
                    db, db.get(Event, ctx.event_id), resource_type=ctx.resource_type,
                    window_start=NOW + timedelta(days=30), window_end=NOW + timedelta(days=30, hours=2),
                    quantity=1,
                )

    def test_a_draft_pool_grants_nothing(self, ctx):
        with Session(engine) as db:
            db.execute(text("UPDATE capacity_pools SET status='draft' WHERE id=:p"), {"p": ctx.pool_id})
            db.commit()
            with pytest.raises(ValueError, match="No active capacity pool"):
                self._hold(db, ctx, 1)
            db.execute(text("UPDATE capacity_pools SET status='active' WHERE id=:p"), {"p": ctx.pool_id})
            db.commit()

    def test_concurrent_reservations_cannot_oversubscribe(self, ctx):
        """The core guarantee (doc C4). Four threads race for a pool of 2; the row lock in
        _claim_pool_capacity must let exactly 2 through and refuse the rest."""
        def attempt():
            with Session(engine) as db:
                try:
                    self._hold(db, ctx, 1)
                    return "granted"
                except ValueError:
                    return "refused"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = [f.result() for f in [pool.submit(attempt) for _ in range(4)]]

        assert results.count("granted") == 2, results
        assert results.count("refused") == 2, results
        with Session(engine) as db:
            util = crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))
            # The invariant that actually matters: never more committed than exists.
            assert util["reserved_capacity"] <= util["total_capacity"] == 2
            assert util["available_capacity"] == 0

    def test_release_returns_capacity_to_the_pool(self, ctx):
        with Session(engine) as db:
            r = self._hold(db, ctx, 2)
            assert crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))["available_capacity"] == 0
            crud.release_capacity(db, r, reason="test")
            assert crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))["available_capacity"] == 2

    def test_expired_soft_hold_releases_its_capacity(self, ctx):
        with Session(engine) as db:
            r = self._hold(db, ctx, 2)
            db.execute(text("UPDATE capacity_reservations SET soft_hold_expires_at = :t WHERE id = :i"),
                       {"t": datetime.now(timezone.utc) - timedelta(minutes=5), "i": r.id})
            db.commit()
            assert crud.expire_stale_soft_holds(db) >= 1
            db.refresh(r)
            assert r.state == "expired"
            assert crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))["available_capacity"] == 2

    def test_consumed_capacity_still_holds_inventory(self, ctx):
        """Consumption is not a release — the resource was actually used."""
        with Session(engine) as db:
            r = self._hold(db, ctx, 1)
            r.state = "hard_reserved"
            db.commit()
            crud.consume_capacity(db, r)
            util = crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))
            assert util["consumed"] == 1 and util["available_capacity"] == 1

    def test_capacity_is_never_granted_from_payment_state(self, ctx):
        """Doc B4 / phase item 6: no payment_success -> capacity_reserved path exists.
        Neither capacity entry point may so much as name a payment concept in its CODE."""
        for fn in (crud.hard_reserve_capacity, crud.soft_hold_capacity, crud._claim_pool_capacity):
            src = code_only(fn)
            for forbidden in ("Payment", "payment", "_captured_amount", "financial_readiness"):
                assert forbidden not in src, f"{fn.__name__} references {forbidden!r}"


# ══════════════════════════════════════════════════════════════════════════════════════
# C. INVOICE NUMBERING — atomic allocation, per-entity series, immutability
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestInvoiceNumbering:

    @pytest.fixture
    def entity(self):
        code = f"test_entity_{uuid.uuid4().hex[:8]}"
        with Session(engine) as db:
            e = SellerLegalEntity(code=code, legal_name="Test Entity Ltd", country="GB", status="active")
            db.add(e)
            db.commit()
            eid = e.id
        yield SimpleNamespace(id=eid, code=code)
        with Session(engine) as db:
            db.execute(text("DELETE FROM invoice_number_sequences WHERE scope LIKE :s"), {"s": f"%{code}"})
            db.execute(text("DELETE FROM seller_legal_entities WHERE id = :i"), {"i": eid})
            db.commit()

    def test_numbers_are_sequential_and_keep_the_existing_format(self, entity):
        with Session(engine) as db:
            e = db.get(SellerLegalEntity, entity.id)
            nums = [crud._allocate_invoice_number(db, ledger="live_event", seller_entity=e) for _ in range(3)]
            db.commit()
        assert nums == ["ZST-LE-INV-000001", "ZST-LE-INV-000002", "ZST-LE-INV-000003"]

    def test_concurrent_allocation_never_duplicates(self, entity):
        """Replaces a read-then-increment that handed two callers the same number."""
        def allocate():
            with Session(engine) as db:
                e = db.get(SellerLegalEntity, entity.id)
                n = crud._allocate_invoice_number(db, ledger="live_event", seller_entity=e)
                db.commit()
                return n

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            numbers = [f.result() for f in [pool.submit(allocate) for _ in range(24)]]

        assert len(numbers) == len(set(numbers)) == 24, "duplicate invoice number allocated"

    def test_series_are_scoped_per_seller_entity(self, entity):
        """Each legal entity keeps its own series (doc Section 3: do not reuse sequences)."""
        other_code = f"test_entity_{uuid.uuid4().hex[:8]}"
        with Session(engine) as db:
            other = SellerLegalEntity(code=other_code, legal_name="Other Ltd", country="GB", status="active")
            db.add(other)
            db.commit()
            first = crud._allocate_invoice_number(db, ledger="live_event", seller_entity=db.get(SellerLegalEntity, entity.id))
            other_first = crud._allocate_invoice_number(db, ledger="live_event", seller_entity=other)
            db.commit()
            db.execute(text("DELETE FROM invoice_number_sequences WHERE scope LIKE :s"), {"s": f"%{other_code}"})
            db.execute(text("DELETE FROM seller_legal_entities WHERE code = :c"), {"c": other_code})
            db.commit()
        # Independent counters: both start at 1 rather than sharing one global run.
        assert first == other_first == "ZST-LE-INV-000001"


# ══════════════════════════════════════════════════════════════════════════════════════
# D. SELLER LEGAL ENTITY — registry, activation, fail-closed resolution
# ══════════════════════════════════════════════════════════════════════════════════════

class _StubSession:
    def __init__(self, get_result=None, scalar_result=None):
        self.added = []
        self._get, self._scalar = get_result, scalar_result

    def add(self, o):
        self.added.append(o)

    def get(self, model, pk):
        return self._get

    def scalar(self, stmt=None):
        return self._scalar

    def commit(self):
        pass

    def refresh(self, o):
        pass


def test_invoice_blocked_when_no_seller_entity_is_assigned():
    account = CommercialAccount(id=uuid.uuid4(), org_id=uuid.uuid4(), seller_legal_entity_id=None)
    order = SimpleNamespace(id=uuid.uuid4(), commercial_account_id=account.id)
    with pytest.raises(ValueError, match="no seller legal entity"):
        crud.resolve_seller_entity(_StubSession(get_result=account), order)


def test_invoice_blocked_when_seller_entity_is_not_registered():
    """The old code returned the literal "zoiko_tech_inc" whether or not it existed."""
    account = CommercialAccount(id=uuid.uuid4(), org_id=uuid.uuid4(), seller_legal_entity_id="zoiko_tech_inc")
    order = SimpleNamespace(id=uuid.uuid4(), commercial_account_id=account.id)
    with pytest.raises(ValueError, match="not in the registry"):
        crud.resolve_seller_entity(_StubSession(get_result=account, scalar_result=None), order)


@pytest.mark.parametrize("status", ["draft", "suspended", "retired"])
def test_invoice_blocked_when_seller_entity_is_not_active(status):
    account = CommercialAccount(id=uuid.uuid4(), org_id=uuid.uuid4(), seller_legal_entity_id="e1")
    order = SimpleNamespace(id=uuid.uuid4(), commercial_account_id=account.id)
    entity = SellerLegalEntity(code="e1", legal_name="E1", country="GB", status=status)
    with pytest.raises(ValueError, match=f"is '{status}', not active"):
        crud.resolve_seller_entity(_StubSession(get_result=account, scalar_result=entity), order)


def test_active_registered_seller_entity_resolves():
    account = CommercialAccount(id=uuid.uuid4(), org_id=uuid.uuid4(), seller_legal_entity_id="e1")
    order = SimpleNamespace(id=uuid.uuid4(), commercial_account_id=account.id)
    entity = SellerLegalEntity(code="e1", legal_name="E1", country="GB", status="active")
    assert crud.resolve_seller_entity(_StubSession(get_result=account, scalar_result=entity), order) is entity


def test_activation_requires_identity_facts():
    """Doc L1 makes the seller mandatory on issued documents — activating a nameless entity
    would just move the fail-closed point downstream."""
    entity = SellerLegalEntity(code="e1", legal_name="", country=None, status="draft")
    with pytest.raises(ValueError, match="missing"):
        crud.activate_seller_entity(_StubSession(), entity, SimpleNamespace(id=uuid.uuid4(), email="a@b.c"))


def test_no_seller_entity_is_hard_coded_anymore():
    """No code default may name a seller entity; comments recording the removal may."""
    src = code_only(crud)
    assert "zoiko_tech_inc" not in src


# ══════════════════════════════════════════════════════════════════════════════════════
# E. TAX — NOT DETERMINED vs explicit ZERO, and post-issue immutability
# ══════════════════════════════════════════════════════════════════════════════════════

def _order(**over):
    fields = dict(id=uuid.uuid4(), status="draft", commercial_account_id=uuid.uuid4(),
                  subtotal=Decimal("100.00"), total_amount=Decimal("100.00"), currency="USD",
                  tax_amount=None, tax_treatment=None, tax_jurisdiction=None, tax_source=None,
                  tax_rule_version=None, tax_effective_at=None, tax_determined_at=None,
                  tax_determined_by=None, tax_exemption_reason=None)
    fields.update(over)
    return SimpleNamespace(**fields)


_ACTOR = SimpleNamespace(id=uuid.uuid4(), email="finance@zoikostream.com")
_VALID_TAX = dict(tax_amount=Decimal("20.00"), treatment="standard_rate",
                  jurisdiction="GB", source="finance_manual")


def test_not_determined_blocks_invoice_issuance():
    with pytest.raises(ValueError, match="no tax determination"):
        crud.issue_invoice(_StubSession(), _order())


def test_zero_tax_requires_an_explicit_reason():
    """This is the NOT_DETERMINED vs ZERO_TAX line: a bare 0.00 is refused, so a determined
    zero can never be confused with an undetermined one."""
    with pytest.raises(ValueError, match="exemption/zero-rating reason"):
        crud.record_tax_determination(_StubSession(), _order(), _ACTOR,
                                      **{**_VALID_TAX, "tax_amount": Decimal("0.00")})


def test_zero_tax_with_a_reason_is_a_valid_determination():
    order = _order()
    crud.record_tax_determination(_StubSession(), order, _ACTOR, tax_amount=Decimal("0.00"),
                                  treatment="zero_rated", jurisdiction="GB", source="finance_manual",
                                  exemption_reason="Cross-border B2B supply, reverse charge applies")
    assert order.tax_amount == Decimal("0.00")            # a real, determined zero
    assert order.tax_exemption_reason.startswith("Cross-border")
    assert order.tax_determined_at is not None            # ...and provably determined
    assert order.total_amount == Decimal("100.00")


def test_determined_zero_is_distinguishable_from_missing_tax():
    undetermined, determined = _order(), _order()
    crud.record_tax_determination(_StubSession(), determined, _ACTOR, tax_amount=Decimal("0.00"),
                                  treatment="exempt", jurisdiction="GB", source="finance_manual",
                                  exemption_reason="Exempt supply")
    assert undetermined.tax_amount is None and undetermined.tax_determined_at is None
    assert determined.tax_amount == Decimal("0.00") and determined.tax_determined_at is not None


def test_nonzero_tax_needs_no_exemption_reason():
    order = _order()
    crud.record_tax_determination(_StubSession(), order, _ACTOR, **_VALID_TAX)
    assert order.tax_amount == Decimal("20.00") and order.tax_exemption_reason is None


def test_issued_invoice_snapshots_tax_and_survives_re_determination():
    """Doc Section 26: an issued document is immutable. Re-determining the ORDER must not
    reach back into an invoice already sent to the customer."""
    order = _order()
    crud.record_tax_determination(_StubSession(), order, _ACTOR, **_VALID_TAX)
    entity = SellerLegalEntity(code="e1", legal_name="E1", country="GB", status="active")
    session = _StubSession(get_result=CommercialAccount(
        id=order.commercial_account_id, org_id=uuid.uuid4(), seller_legal_entity_id="e1"),
        scalar_result=entity)

    class _AllocSession(_StubSession):
        def execute(self, stmt, params=None):
            return SimpleNamespace(scalar_one=lambda: 7)

    alloc = _AllocSession(get_result=session._get, scalar_result=entity)
    invoice = crud.issue_invoice(alloc, order)
    assert invoice.number == "ZST-LE-INV-000007"
    assert invoice.tax_amount == Decimal("20.00")
    assert invoice.tax_treatment == "standard_rate"

    crud.record_tax_determination(_StubSession(), order, _ACTOR, tax_amount=Decimal("99.00"),
                                  treatment="revised_rate", jurisdiction="IE", source="finance_manual")
    assert order.tax_amount == Decimal("99.00")            # order moved on
    assert invoice.tax_amount == Decimal("20.00")          # issued document did not
    assert invoice.tax_treatment == "standard_rate"


def test_scope_change_invalidates_a_determination():
    order = _order(tax_amount=Decimal("20.00"), tax_treatment="standard_rate",
                   tax_exemption_reason="stale")
    crud._clear_tax_determination(order)
    assert order.tax_amount is None
    assert order.tax_treatment is None
    assert order.tax_exemption_reason is None


def test_no_tax_rate_is_hard_coded_anywhere():
    src = code_only(crud)
    for forbidden in ("0.18", "18%", "GST", "VAT_RATE", "TAX_RATE", "DEFAULT_TAX"):
        assert forbidden not in src, f"hard-coded tax value {forbidden!r} present"


# ══════════════════════════════════════════════════════════════════════════════════════
# F. IMMUTABLE ORDER VERSIONS
# ══════════════════════════════════════════════════════════════════════════════════════

def test_accepted_order_is_snapshotted_with_its_lines():
    order = _order(status="accepted", order_version=1, catalog_version_id=uuid.uuid4(),
                   accepted_at=NOW, accepted_by=_ACTOR.id, risk_tier="r2",
                   billing_classification="commercial", billing_source="direct_zoikostream",
                   terms_version="tos-v1", service_profile_id=None, cancellation_policy_id=None,
                   tax_amount=Decimal("20.00"), total_amount=Decimal("120.00"))
    order.lines = [SimpleNamespace(
        id=uuid.uuid4(), catalog_line_id=uuid.uuid4(), service_code="MEM-MANAGED",
        description="Managed memorial", quantity=Decimal(1), unit_price=Decimal("100.00"),
        unit_basis="per_event", line_total=Decimal("100.00"), tax_treatment="standard_rate",
        is_addon=False, is_complimentary=False,
    )]
    version = crud.snapshot_order_version(_StubSession(scalar_result=None), order, actor=_ACTOR)
    assert version.order_version == 1
    assert version.total_amount == Decimal("120.00")
    assert version.snapshot["order"]["risk_tier"] == "r2"
    assert len(version.snapshot["lines"]) == 1
    assert version.snapshot["lines"][0]["unit_basis"] == "per_event"


def test_snapshotting_the_same_version_twice_does_not_fork_history():
    order = _order(status="accepted", order_version=1, catalog_version_id=uuid.uuid4())
    order.lines = []
    existing = SimpleNamespace(id=uuid.uuid4(), order_version=1)
    assert crud.snapshot_order_version(_StubSession(scalar_result=existing), order) is existing


# ══════════════════════════════════════════════════════════════════════════════════════
# G. MULTI-TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════════════════

def _route(path: str, method: str = "POST"):
    from app.routers import commercial as router_mod
    for r in router_mod.router.routes:
        if r.path == f"/commercial{path}" and method in r.methods:
            return r
    raise AssertionError(f"route {method} /commercial{path} not found")


def _dependency_names(route) -> set[str]:
    """Names of the callables behind a route's Depends(...) — including the factory that
    produced them, so require_commercial("x") is identifiable."""
    names = set()
    for dep in route.dependant.dependencies:
        call = dep.call
        names.add(getattr(call, "__name__", "") or "")
        names.add(getattr(call, "__qualname__", "") or "")
    return names


@pytest.mark.parametrize("path,method", [
    ("/seller-entities", "GET"),
    ("/seller-entities", "POST"),
    ("/capacity-pools", "GET"),
    ("/capacity-pools", "POST"),
])
def test_zoiko_side_registries_are_super_admin_only(path, method):
    """Seller entities and capacity pools are Zoiko's own commercial/operational data, not
    tenant data — a customer must not enumerate Zoiko's legal entities, tax registrations or
    total operator headroom."""
    assert any("require_super_admin" in n or "_dep" in n for n in _dependency_names(_route(path, method))), \
        f"{method} {path} is not gated"


@pytest.mark.parametrize("path", [
    "/orders/{order_id}/versions",
    "/orders/{order_id}/tax-determination",
    "/orders/{order_id}/invoices",
])
def test_order_scoped_routes_resolve_through_the_org_gate(path):
    """Every order-scoped route must go through _get_order_or_404, which resolves the parent
    event via org_scoped — so another org's order 404s instead of leaking."""
    from app.routers import commercial as router_mod
    method = "GET" if path.endswith("/versions") else "POST"
    endpoint = _route(path, method).endpoint
    assert "_get_order_or_404" in code_only(endpoint), f"{path} does not org-scope its lookup"


def test_capacity_and_event_routes_resolve_through_the_org_gate():
    for path, method in [("/events/{event_id}/capacity/hold", "POST"),
                         ("/events/{event_id}/capacity", "GET"),
                         ("/events/{event_id}/orders", "POST")]:
        assert "_get_event_or_404" in code_only(_route(path, method).endpoint), path


@needs_db
def test_draft_catalog_versions_are_hidden_from_customers(  ):
    """Unapproved pricing must not be readable by tenants (doc B2/T1). Previously every
    authenticated user could list every draft price book."""
    vertical = f"tenant-test-{uuid.uuid4().hex[:8]}"
    with Session(engine) as db:
        draft = CatalogVersion(version_label="v-draft", vertical=vertical, status="draft")
        published = CatalogVersion(version_label="v-pub", vertical=vertical, status="published")
        retired = CatalogVersion(version_label="v-old", vertical=vertical, status="retired")
        db.add_all([draft, published, retired])
        db.commit()
        try:
            customer_view = crud.list_catalog_versions(db, vertical=vertical, published_only=True)
            staff_view = crud.list_catalog_versions(db, vertical=vertical)
            assert {c.status for c in customer_view} == {"published"}
            assert {c.status for c in staff_view} == {"draft", "published", "retired"}
        finally:
            db.execute(text("DELETE FROM catalog_versions WHERE vertical = :v"), {"v": vertical})
            db.commit()


@needs_db
def test_capacity_pools_are_not_tenant_scoped_but_reservations_are():
    """Pools are Zoiko inventory (no org_id by design — one shared operator roster serves
    every customer). Isolation is enforced one level down: a reservation belongs to an event,
    which belongs to an org, and every capacity route resolves the event through the org gate.
    This test pins that structural decision so it is deliberate rather than an oversight."""
    assert not hasattr(CapacityPool, "org_id")
    from app.models import CapacityReservation
    assert hasattr(CapacityReservation, "event_id")
    assert "_get_event_or_404" in code_only(_route("/events/{event_id}/capacity/hold", "POST").endpoint)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
