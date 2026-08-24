"""Phase 4D — hardening the legacy authorization endpoint.

    POST /api/commercial/orders/{order_id}/payments/authorize

The audit found this path let a CUSTOMER role choose the payment amount: `amount` was a
required client field passed straight to the provider and onto the Payment row, with no
comparison against the order. Currency was already server-derived, and tenancy was already
enforced — those are pinned here so the fix cannot regress them.

Written test-first. Every test that describes the hardened behaviour was failing before the
implementation change.
"""
import concurrent.futures
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.crud import commercial as crud
from app.db import engine
from app.models import (
    CatalogLine, CatalogVersion, CommercialAccount, Event, EventOrder, Organization,
    Payment, User,
)
from app.services import payments as pay


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


# ══════════════════════════════════════════════════════════════════════════════════════
# SCHEMA CONTRACT (no DB)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_amount_is_optional_so_the_server_can_decide_it():
    """The client may omit `amount` entirely; the server then uses the authoritative
    outstanding balance. Previously `amount` was REQUIRED, which forced the client to name a
    figure — the root of this P1."""
    from app.schemas.commercial import PaymentAuthorizeCreate
    field = PaymentAuthorizeCreate.model_fields["amount"]
    assert not field.is_required(), "amount must be optional so the server can be authoritative"


def test_client_still_cannot_supply_a_currency():
    """Was already safe; pinned so the fix cannot accidentally open it."""
    from app.schemas.commercial import PaymentAuthorizeCreate
    for forbidden in ("currency", "tax", "tax_amount", "discount", "seller",
                      "seller_legal_entity_id", "total", "total_amount"):
        assert forbidden not in PaymentAuthorizeCreate.model_fields, \
            f"client can supply {forbidden!r}"


def test_provider_name_must_be_explicit():
    """A defaulted "mock" meant an HTTP caller that omitted the field silently got the
    SIMULATOR against a real order, minting fake pending payments."""
    from app.schemas.commercial import PaymentAuthorizeCreate
    assert PaymentAuthorizeCreate.model_fields["provider_name"].is_required(), \
        "provider_name must be explicit — a silent 'mock' default simulates a real payment"


def test_client_supplied_extras_do_not_reach_the_model():
    from app.schemas.commercial import PaymentAuthorizeCreate
    parsed = PaymentAuthorizeCreate.model_validate({
        "idempotency_key": "k", "provider_name": "mock",
        "currency": "JPY", "tax_amount": "0", "total_amount": "1.00",
    })
    for forbidden in ("currency", "tax_amount", "total_amount"):
        assert not hasattr(parsed, forbidden)


# ══════════════════════════════════════════════════════════════════════════════════════
# AMOUNT AUTHORITY (no DB — guards operate before any query)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_crud_signature_no_longer_takes_a_mandatory_amount():
    import inspect
    sig = inspect.signature(crud.authorize_payment)
    assert sig.parameters["amount"].default is None, \
        "amount must default to None so the server derives it"


def test_authorize_reads_the_authoritative_amount():
    """Structural: the function must consult order_payable_amount rather than trusting a
    caller-supplied figure."""
    src = code_only(crud.authorize_payment)
    assert "order_payable_amount" in src, "authorize must derive the amount from the order"


def test_currency_still_comes_from_the_order_not_the_caller():
    src = code_only(crud.authorize_payment)
    assert "order.currency" in src or "currency" in src
    # No literal currency anywhere in the path.
    for forbidden in ('"USD"', "'USD'", "DEFAULT_CURRENCY"):
        assert forbidden not in src


def test_no_hardcoded_commercial_values_in_the_changed_path():
    for obj in (crud.authorize_payment, crud.order_payable_amount):
        src = code_only(obj)
        for token in ("price_id", "PRICE_ID", "prices.create", "products.create", "TAX_RATE",
                      "GST", "0.18", "DEFAULT_CURRENCY", "zoiko_tech_inc", "price_monthly",
                      "DEFAULT_CAPACITY"):
            assert token not in src, f"hardcoded commercial value {token!r}"


def test_no_stripe_knowledge_leaked_into_the_domain():
    src = code_only(crud).lower()
    assert "import stripe" not in src and "payments_stripe" not in src


# ══════════════════════════════════════════════════════════════════════════════════════
# AGAINST REAL COMMERCIAL RECORDS
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestLegacyAuthorizeHardened:

    @pytest.fixture
    def ctx(self):
        """A priced, accepted, tax-determined order: 1200.00 + 240.00 = 1440.00 outstanding."""
        with Session(engine) as db:
            org = Organization(name=f"la-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="LA", email=f"la-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"la{uuid.uuid4().hex[:8]}", password_hash="x", role="org_admin")
            staff = User(org_id=org.id, full_name="Fin", email=f"lf-{uuid.uuid4().hex[:8]}@t.test",
                         username=f"lf{uuid.uuid4().hex[:8]}", password_hash="x",
                         role="super_admin")
            db.add_all([user, staff])
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Legacy authorize test")
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"la-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([event, account, catalog])
            db.flush()
            line = CatalogLine(catalog_version_id=catalog.id, service_code="MEM",
                               name="Managed memorial", unit_price=Decimal("1200.00"),
                               currency="USD", unit_basis="per_event",
                               tax_treatment="standard_rate")
            db.add(line)
            db.flush()
            order = crud.create_order(
                db, event, commercial_account=account, catalog_version=catalog,
                purchaser_type="organization", purchaser_id=user.id, service_profile=None,
                cancellation_policy=None, currency="USD",
                idempotency_key=f"ord-{uuid.uuid4().hex[:10]}")
            crud.add_order_line(db, order, line, quantity=Decimal(1))
            crud.submit_order_for_acceptance(db, order)
            crud.accept_order(db, order, staff, terms_version="tos-v1")
            crud.record_tax_determination(db, order, staff, tax_amount=Decimal("240.00"),
                                          treatment="standard_rate", jurisdiction="GB",
                                          source="finance_manual")
            db.refresh(order)
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, staff_id=staff.id,
                                  event_id=event.id, order_id=order.id, account_id=account.id,
                                  catalog_id=catalog.id, line_id=line.id,
                                  outstanding=order.total_amount)
        yield ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM provider_events WHERE payment_id IN "
                 "(SELECT id FROM payments WHERE event_order_id=:o)", {"o": ids.order_id}),
                ("DELETE FROM audit_logs WHERE org_id=:g", {"g": ids.org_id}),
                ("DELETE FROM payments WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM payment_schedules WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_order_versions WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_order_lines WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_orders WHERE id=:o", {"o": ids.order_id}),
                ("DELETE FROM catalog_lines WHERE id=:l", {"l": ids.line_id}),
                ("DELETE FROM events WHERE id=:e", {"e": ids.event_id}),
                ("DELETE FROM catalog_versions WHERE id=:c", {"c": ids.catalog_id}),
                ("DELETE FROM commercial_accounts WHERE id=:a", {"a": ids.account_id}),
                ("DELETE FROM users WHERE org_id=:g", {"g": ids.org_id}),
                ("DELETE FROM organizations WHERE id=:g", {"g": ids.org_id}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    def _auth(self, db, ctx, **kw):
        return crud.authorize_payment(
            db, db.get(EventOrder, ctx.order_id),
            idempotency_key=kw.pop("idempotency_key", f"k-{uuid.uuid4().hex[:12]}"),
            actor=db.get(User, ctx.user_id), **kw)

    # ── 1/7. the server decides the amount ─────────────────────────────────────────────
    def test_omitted_amount_uses_the_authoritative_outstanding(self, ctx):
        with Session(engine) as db:
            payment = self._auth(db, ctx)
            assert payment.amount == ctx.outstanding == Decimal("1440.00")
            assert payment.currency == "USD"

    @pytest.mark.parametrize("attempt", ["1.00", "0.01", "999999.00", "1439.99"])
    def test_client_supplied_amount_cannot_override_the_order(self, ctx, attempt):
        """The headline fix: authorizing 1.00 against a 1440.00 order used to succeed."""
        with Session(engine) as db:
            with pytest.raises(ValueError, match="does not match"):
                self._auth(db, ctx, amount=Decimal(attempt))
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
            assert n == 0, "a payment was created for a client-chosen amount"

    def test_a_matching_amount_is_accepted(self, ctx):
        """Supplying the correct figure still works — the contract is preserved for callers
        that already send the right number."""
        with Session(engine) as db:
            payment = self._auth(db, ctx, amount=ctx.outstanding)
            assert payment.amount == ctx.outstanding

    def test_amount_tracks_the_outstanding_balance_after_partial_settlement(self, ctx):
        with Session(engine) as db:
            db.add(Payment(event_order_id=ctx.order_id, provider="mock",
                           provider_payment_ref=f"r{uuid.uuid4().hex[:8]}",
                           amount=Decimal("440.00"), currency="USD", state="paid",
                           idempotency_key=str(uuid.uuid4())))
            db.commit()
            payment = self._auth(db, ctx)
            assert payment.amount == Decimal("1000.00")

    # ── 5. already paid ────────────────────────────────────────────────────────────────
    def test_fully_paid_order_cannot_be_authorized_again(self, ctx):
        with Session(engine) as db:
            db.add(Payment(event_order_id=ctx.order_id, provider="mock",
                           provider_payment_ref=f"r{uuid.uuid4().hex[:8]}",
                           amount=ctx.outstanding, currency="USD", state="paid",
                           idempotency_key=str(uuid.uuid4())))
            db.commit()
            with pytest.raises(ValueError, match="already paid in full"):
                self._auth(db, ctx)

    @pytest.mark.parametrize("bad", ["draft", "pending_acceptance", "canceled", "terminated"])
    def test_invalid_order_state_is_refused(self, ctx, bad):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            order.status = bad
            db.commit()
            with pytest.raises(ValueError):
                self._auth(db, ctx)

    def test_undetermined_tax_blocks_authorization(self, ctx):
        """Same gate as Checkout: without a tax determination the total is not a commercial
        fact, so there is no figure to charge."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            crud._clear_tax_determination(order)
            db.commit()
            with pytest.raises(ValueError, match="no tax determination"):
                self._auth(db, ctx)

    # ── 6. idempotency ─────────────────────────────────────────────────────────────────
    def test_replayed_request_returns_the_same_payment(self, ctx):
        with Session(engine) as db:
            key = f"k-{uuid.uuid4().hex[:12]}"
            first = self._auth(db, ctx, idempotency_key=key)
            second = self._auth(db, ctx, idempotency_key=key)
            assert second.id == first.id
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
            assert n == 1

    def test_concurrent_replays_create_one_payment(self, ctx):
        key = f"k-{uuid.uuid4().hex[:12]}"

        def go():
            with Session(engine) as db:
                try:
                    self._auth(db, ctx, idempotency_key=key)
                    return "ok"
                except Exception as e:
                    return f"err:{type(e).__name__}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = [f.result() for f in [pool.submit(go) for _ in range(4)]]
        with Session(engine) as db:
            n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                          .bindparams(o=ctx.order_id))
        assert n == 1, f"{n} payments created; results={results}"

    def test_idempotency_lookup_cannot_read_another_orders_payment(self, ctx):
        """The lookup was globally scoped: presenting a key belonging to a DIFFERENT order
        returned that order's payment as the response — a cross-tenant read primitive."""
        with Session(engine) as db:
            other_org = Organization(name=f"la-other-{uuid.uuid4().hex[:8]}")
            db.add(other_org)
            db.flush()
            other_user = User(org_id=other_org.id, full_name="O",
                              email=f"o-{uuid.uuid4().hex[:8]}@t.test",
                              username=f"o{uuid.uuid4().hex[:8]}", password_hash="x",
                              role="org_admin")
            db.add(other_user)
            db.flush()
            other_event = Event(org_id=other_org.id, created_by=other_user.id, title="Other")
            other_account = CommercialAccount(org_id=other_org.id)
            other_catalog = CatalogVersion(version_label="v1",
                                           vertical=f"lo-{uuid.uuid4().hex[:6]}",
                                           status="published")
            db.add_all([other_event, other_account, other_catalog])
            db.flush()
            other_order = EventOrder(event_id=other_event.id,
                                     commercial_account_id=other_account.id,
                                     catalog_version_id=other_catalog.id, currency="USD",
                                     subtotal=Decimal("50.00"), tax_amount=Decimal("0.00"),
                                     total_amount=Decimal("50.00"), status="accepted",
                                     idempotency_key=str(uuid.uuid4()))
            db.add(other_order)
            db.flush()
            leaked_key = f"leaked-{uuid.uuid4().hex[:10]}"
            victim = Payment(event_order_id=other_order.id, provider="mock",
                             provider_payment_ref=f"v{uuid.uuid4().hex[:8]}",
                             amount=Decimal("50.00"), currency="USD", state="pending",
                             idempotency_key=leaked_key)
            db.add(victim)
            db.commit()
            victim_id, other = victim.id, SimpleNamespace(
                org_id=other_org.id, user_id=other_user.id, event_id=other_event.id,
                order_id=other_order.id, account_id=other_account.id,
                catalog_id=other_catalog.id, payment_id=victim.id)
        try:
            with Session(engine) as db:
                # Our order, but the OTHER order's idempotency key. It must be REFUSED, not
                # answered with the other order's payment. (Payment.idempotency_key is
                # globally unique, so the key namespace is shared across orders — hence the
                # ownership check rather than an order-scoped lookup.)
                with pytest.raises(ValueError, match="different order"):
                    self._auth(db, ctx, idempotency_key=leaked_key)
                # And the victim's payment is untouched and still theirs.
                db.expire_all()
                victim_row = db.get(Payment, victim_id)
                assert victim_row.event_order_id == other.order_id
                assert victim_row.amount == Decimal("50.00")
                assert victim_row.state == "pending"
                # No payment was created on our order either.
                n = db.scalar(text("SELECT count(*) FROM payments WHERE event_order_id=:o")
                              .bindparams(o=ctx.order_id))
                assert n == 0
        finally:
            with Session(engine) as db:
                for sql, params in [
                    ("DELETE FROM audit_logs WHERE org_id=:g", {"g": other.org_id}),
                    ("DELETE FROM payments WHERE id=:p", {"p": other.payment_id}),
                    ("DELETE FROM event_orders WHERE id=:o", {"o": other.order_id}),
                    ("DELETE FROM events WHERE id=:e", {"e": other.event_id}),
                    ("DELETE FROM catalog_versions WHERE id=:c", {"c": other.catalog_id}),
                    ("DELETE FROM commercial_accounts WHERE id=:a", {"a": other.account_id}),
                    ("DELETE FROM users WHERE id=:u", {"u": other.user_id}),
                    ("DELETE FROM organizations WHERE id=:g", {"g": other.org_id}),
                ]:
                    db.execute(text(sql), params)
                db.commit()

    # ── 7. what the provider receives ──────────────────────────────────────────────────
    def test_provider_receives_only_the_server_authoritative_values(self, ctx, monkeypatch):
        """Even when the caller supplies the correct amount, the figure handed to the provider
        is the one the server computed."""
        seen = {}

        class _Spy(pay.MockPaymentProvider):
            def authorize(self, *, amount, currency, idempotency_key, simulate_failure=False):
                seen.update(amount=amount, currency=currency)
                return super().authorize(amount=amount, currency=currency,
                                          idempotency_key=idempotency_key,
                                          simulate_failure=simulate_failure)

        monkeypatch.setattr(pay, "get_provider", lambda name="mock": _Spy())
        with Session(engine) as db:
            self._auth(db, ctx, amount=ctx.outstanding)
        assert seen["amount"] == ctx.outstanding == Decimal("1440.00")
        assert seen["currency"] == "USD"

    def test_authorization_is_audited_with_the_server_amount(self, ctx):
        with Session(engine) as db:
            payment = self._auth(db, ctx)
            rows = db.execute(text(
                "SELECT action, meta FROM audit_logs WHERE target_id=:t"
            ).bindparams(t=str(payment.id))).all()
            actions = [r[0] for r in rows]
            assert "commercial.payment.authorize" in actions
            meta = next(r[1] for r in rows if r[0] == "commercial.payment.authorize")
            assert meta["amount"] == str(ctx.outstanding)


# ══════════════════════════════════════════════════════════════════════════════════════
# CHECKOUT MUST BE UNAFFECTED
# ══════════════════════════════════════════════════════════════════════════════════════

def test_checkout_still_derives_its_own_amount():
    src = code_only(crud.start_hosted_checkout)
    assert "order_payable_amount" in src


def test_checkout_request_schema_unchanged():
    from app.schemas.commercial import CheckoutSessionCreate
    assert set(CheckoutSessionCreate.model_fields) == {"provider_name"}


def test_checkout_endpoint_still_registered():
    from app.routers import commercial as router_mod
    paths = {r.path for r in router_mod.router.routes}
    assert "/commercial/orders/{order_id}/payments/checkout-session" in paths
    assert "/commercial/orders/{order_id}/payments/authorize" in paths


def test_both_payment_paths_share_one_amount_authority():
    """The point of the fix: legacy authorize and hosted Checkout now read the SAME
    authoritative source, so they cannot disagree about what is owed."""
    for obj in (crud.authorize_payment, crud.start_hosted_checkout):
        assert "order_payable_amount" in code_only(obj)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
