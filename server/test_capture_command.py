"""Operator-driven capture command — POST /commercial/payments/{payment_id}/capture

ZST-LE-COM-001 puts capture on the Finance/Billing Ops side of the RBAC matrix (Sec. 25) and
keeps authorization, capture, settlement and payout as DISTINCT evidence states (Sec. 8 D3).
`capture_method=manual` is therefore deliberate, not an oversight: an authorized Live Event
payment stays `pending` until an authorized actor captures it.

The adapter's capture() and the inbound `capture_succeeded` webhook were already covered. What
was not covered is this command itself — the state guard that makes a second capture safe, the
fact that no caller supplies an amount, and that it moves state only through the one state
machine. That is what this file tests.

Uses the `mock` provider so the real crud path runs with no network and no Stripe credentials.
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.crud import commercial as crud
from app.db import engine
from app.models import (
    AuditLog, CatalogLine, CatalogVersion, CommercialAccount, Event, EventOrder, Organization,
    Payment, User,
)


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="DATABASE_URL not reachable")


@needs_db
class TestCaptureCommand:

    @pytest.fixture
    def ctx(self):
        """A real accepted, tax-determined order (1200.00 + 240.00 = 1440.00) with an
        authorized payment sitting in `pending`, exactly as hosted checkout leaves it."""
        tag = uuid.uuid4().hex[:8]
        with Session(engine) as db:
            org = Organization(name=f"cap-{tag}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Cap", email=f"cap-{tag}@t.test",
                        username=f"cap{tag}", password_hash="x", role="org_admin")
            staff = User(org_id=org.id, full_name="Fin", email=f"cf-{tag}@t.test",
                         username=f"cf{tag}", password_hash="x", role="super_admin")
            db.add_all([user, staff])
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Capture command test")
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"cap-{tag}", status="published")
            db.add_all([event, account, catalog])
            db.flush()
            line = CatalogLine(catalog_version_id=catalog.id, service_code="MEM-MANAGED",
                               name="Managed", unit_price=Decimal("1200.00"), currency="USD",
                               unit_basis="per_event", tax_treatment="standard_rate")
            db.add(line)
            db.flush()
            order = crud.create_order(
                db, event, commercial_account=account, catalog_version=catalog,
                purchaser_type="organization", purchaser_id=user.id, service_profile=None,
                cancellation_policy=None, currency="USD", idempotency_key=f"ord-{tag}")
            crud.add_order_line(db, order, line, quantity=Decimal(1))
            crud.submit_order_for_acceptance(db, order)
            crud.accept_order(db, order, staff, terms_version="tos-v1")
            crud.record_tax_determination(db, order, staff, tax_amount=Decimal("240.00"),
                                          treatment="standard_rate", jurisdiction="GB",
                                          source="finance_manual")
            payment = Payment(event_order_id=order.id, provider="mock",
                              provider_payment_ref=f"pi_mock_{tag}",
                              checkout_session_ref=f"cs_mock_{tag}",
                              amount=Decimal("1440.00"), currency="USD", state="pending",
                              idempotency_key=f"cap_{tag}")
            db.add(payment)
            db.commit()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, staff_id=staff.id,
                                  event_id=event.id, order_id=order.id, account_id=account.id,
                                  catalog_id=catalog.id, line_id=line.id,
                                  payment_id=payment.id, tag=tag)
        yield ids
        with Session(engine) as db:
            for sql, p in [
                ("DELETE FROM provider_events WHERE payment_id=:p", {"p": ids.payment_id}),
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
                db.execute(text(sql), p)
            db.commit()

    # ── the documented transition (Sec. 8 D3, Sec. 28) ────────────────────────────────────

    def test_capture_moves_an_authorized_payment_to_paid(self, ctx):
        with Session(engine) as db:
            payment = db.get(Payment, ctx.payment_id)
            staff = db.get(User, ctx.staff_id)
            assert payment.state == "pending", "fixture must start authorized, not settled"
            crud.capture_payment(db, payment, actor=staff)
            db.expire_all()
            after = db.get(Payment, ctx.payment_id)
            assert after.state == "paid"
            assert after.captured_at is not None
            assert after.settled_at is not None

    def test_capture_does_not_alter_amount_or_currency(self, ctx):
        """Sec. 28: the order is the amount authority; capture settles it, never restates it."""
        with Session(engine) as db:
            payment = db.get(Payment, ctx.payment_id)
            crud.capture_payment(db, payment, actor=db.get(User, ctx.staff_id))
            db.expire_all()
            after = db.get(Payment, ctx.payment_id)
            assert after.amount == Decimal("1440.00")
            assert after.currency == "USD"

    def test_capture_takes_no_amount_from_the_caller(self):
        """There is no parameter through which an operator could capture a different figure."""
        import inspect
        assert "amount" not in inspect.signature(crud.capture_payment).parameters

    # ── duplicate / illegal requests ──────────────────────────────────────────────────────

    def test_a_second_capture_is_refused(self, ctx):
        """Duplicate-request safety: the state guard makes a repeated capture a 400, not a
        second charge. `paid` is not in the set of capturable states."""
        with Session(engine) as db:
            payment = db.get(Payment, ctx.payment_id)
            staff = db.get(User, ctx.staff_id)
            crud.capture_payment(db, payment, actor=staff)
            db.expire_all()
            again = db.get(Payment, ctx.payment_id)
            with pytest.raises(ValueError, match="Cannot capture a payment in state 'paid'"):
                crud.capture_payment(db, again, actor=staff)
            db.expire_all()
            assert db.get(Payment, ctx.payment_id).state == "paid", "state must be untouched"

    @pytest.mark.parametrize("state", ["requires_action", "failed", "paid", "refunded"])
    def test_capture_is_refused_from_a_non_authorized_state(self, ctx, state):
        with Session(engine) as db:
            payment = db.get(Payment, ctx.payment_id)
            payment.state = state
            db.commit()
            with pytest.raises(ValueError, match="Cannot capture a payment in state"):
                crud.capture_payment(db, payment, actor=db.get(User, ctx.staff_id))
            db.expire_all()
            assert db.get(Payment, ctx.payment_id).state == state

    def test_only_one_payment_row_exists_after_capture(self, ctx):
        with Session(engine) as db:
            crud.capture_payment(db, db.get(Payment, ctx.payment_id),
                                 actor=db.get(User, ctx.staff_id))
            rows = db.scalars(
                select(Payment).where(Payment.event_order_id == ctx.order_id)).all()
            assert len(rows) == 1

    # ── evidence (Sec. 26 "correlation IDs", Sec. 30) ─────────────────────────────────────

    def test_capture_is_audited_against_the_owning_org(self, ctx):
        with Session(engine) as db:
            crud.capture_payment(db, db.get(Payment, ctx.payment_id),
                                 actor=db.get(User, ctx.staff_id))
            rows = db.scalars(
                select(AuditLog).where(AuditLog.org_id == ctx.org_id)).all()
            actions = [r.action for r in rows]
            assert any("payment" in a for a in actions), f"no payment audit entry: {actions}"
            assert all(r.correlation_id for r in rows if "payment" in r.action), \
                "every payment audit row needs a correlation id"

    # ── outstanding balance follows captured money (Sec. 8 D6) ────────────────────────────

    def test_capture_clears_the_outstanding_balance(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            before, _ = crud.order_payable_amount(db, order)
            assert before == Decimal("1440.00")
            crud.capture_payment(db, db.get(Payment, ctx.payment_id),
                                 actor=db.get(User, ctx.staff_id))
            db.expire_all()
            order = db.get(EventOrder, ctx.order_id)
            with pytest.raises(ValueError, match="already paid in full"):
                crud.order_payable_amount(db, order)

    def test_authorization_alone_leaves_the_balance_outstanding(self, ctx):
        """The gap this command closes: an authorized payment is NOT collected money."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            amount, currency = crud.order_payable_amount(db, order)
            assert amount == Decimal("1440.00") and currency == "USD"
            assert db.get(Payment, ctx.payment_id).state == "pending"
