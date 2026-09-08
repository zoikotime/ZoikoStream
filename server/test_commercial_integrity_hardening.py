"""Three narrow integrity fixes found by the 2026-08-28 read-only audit of the Live Events
commercial layer (Ledger 2). Each closes a defect in code that was otherwise already verified:

  FIX 1  issue_invoice minted a legal financial document and wrote no audit row — the only
         money-affecting function in crud/commercial.py without one.
  FIX 2  Invoice had no uniqueness on event_order_id. UNIQUE(seller_legal_entity_id, number)
         makes NUMBERS unique per seller, not DOCUMENTS per order, so two concurrent issues
         each allocated their own number and both succeeded.
  FIX 3  The maker-checker comparisons were null-PERMISSIVE
         (`if x.requested_by and str(...) == str(approver.id)`), so a row with a NULL maker
         skipped the rule entirely and self-approved.

Nothing here asserts a business rule that was not already in the code: no plan tiers, no
pricing, no payment terms. The `needs_db` tests use the isolated local PostgreSQL database
(see conftest.py — TEST_DATABASE_URL is mandatory) because a partial unique index and a real
concurrent race cannot be demonstrated against a stub.
"""
import concurrent.futures
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import commercial as crud
from app.db import engine
from app.models import (
    AuditLog, CatalogVersion, CommercialAccount, Event, EventOrder, Invoice, Organization,
    SellerLegalEntity, User,
)


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")

_MAKER = SimpleNamespace(id=uuid.uuid4(), email="maker@zoikostream.com")
_CHECKER = SimpleNamespace(id=uuid.uuid4(), email="checker@zoikostream.com")


# ══════════════════════════════════════════════════════════════════════════════════════
# FIX 3 · maker-checker fails closed — pure logic, no DB
# ══════════════════════════════════════════════════════════════════════════════════════

def test_helper_rejects_same_maker_and_checker():
    with pytest.raises(ValueError, match="Maker-checker violation"):
        crud.assert_distinct_maker_checker(
            _MAKER.id, _MAKER, violation="v", missing_maker="m")


def test_helper_allows_distinct_maker_and_checker():
    # No exception is the assertion.
    crud.assert_distinct_maker_checker(_MAKER.id, _CHECKER, violation="v", missing_maker="m")


def test_helper_rejects_null_maker():
    """The regression this fix exists for: a NULL maker used to skip the comparison and pass."""
    with pytest.raises(ValueError, match="records no requester|Maker-checker violation"):
        crud.assert_distinct_maker_checker(
            None, _CHECKER, violation="v", missing_maker="records no requester")


def test_helper_rejects_null_checker():
    with pytest.raises(ValueError, match="no identified approver"):
        crud.assert_distinct_maker_checker(
            _MAKER.id, None, violation="v", missing_maker="m")


def test_helper_rejects_checker_without_an_id():
    """An object that is not None but carries no id must not be treated as an approver."""
    with pytest.raises(ValueError, match="no identified approver"):
        crud.assert_distinct_maker_checker(
            _MAKER.id, SimpleNamespace(), violation="v", missing_maker="m")


def test_helper_compares_by_value_not_identity():
    """UUID vs str of the same id is the same person — the original code stringified both,
    and that behaviour must survive."""
    with pytest.raises(ValueError, match="Maker-checker violation"):
        crud.assert_distinct_maker_checker(
            str(_MAKER.id), _MAKER, violation="v", missing_maker="m")


class _ApprovalStub:
    """Minimal session for the four approval flows: they add an audit row and commit."""

    def __init__(self):
        self.added = []

    def add(self, o):
        self.added.append(o)

    def get(self, model, pk):
        return None

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, o):
        pass


def _exception(requested_by, status="requested"):
    from app.models import CommercialException
    return CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                               status=status, requested_by=requested_by)


def _refund(requested_by, status="pending"):
    from app.models import RefundCredit
    return RefundCredit(id=uuid.uuid4(), event_order_id=uuid.uuid4(), type="credit",
                        amount=Decimal("10.00"), status=status, requested_by=requested_by)


def _period(prepared_by, status="pending_close"):
    from app.models import FinancialPeriod
    return FinancialPeriod(id=uuid.uuid4(), label="2026-08", status=status,
                           prepared_by=prepared_by)


def _recon_exception(prepared_by, status="pending_review"):
    from app.models import ReconciliationException
    return ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                   reference_type="t", reference_id=uuid.uuid4(),
                                   description="d", status=status,
                                   proposed_status="resolved", prepared_by=prepared_by)


# Each row: (label, callable(session, record, actor), record factory)
_FLOWS = [
    ("commercial_exception",
     lambda s, r, a: crud.approve_commercial_exception(s, r, a), _exception),
    ("refund_credit",
     lambda s, r, a: crud.approve_refund_credit(s, r, a), _refund),
    ("period_close",
     lambda s, r, a: crud.confirm_period_close(s, r, a), _period),
    ("reconciliation_exception",
     lambda s, r, a: crud.confirm_exception_resolution(s, r, a), _recon_exception),
]


@pytest.mark.parametrize("label,call,factory", _FLOWS, ids=[f[0] for f in _FLOWS])
def test_flow_rejects_self_approval(label, call, factory):
    with pytest.raises(ValueError, match="Maker-checker violation"):
        call(_ApprovalStub(), factory(_MAKER.id), _MAKER)


@pytest.mark.parametrize("label,call,factory", _FLOWS, ids=[f[0] for f in _FLOWS])
def test_flow_rejects_null_maker(label, call, factory):
    """THE FIX. Before this, every one of these four passed with a NULL maker."""
    with pytest.raises(ValueError, match="Maker-checker violation"):
        call(_ApprovalStub(), factory(None), _CHECKER)


@pytest.mark.parametrize("label,call,factory", _FLOWS, ids=[f[0] for f in _FLOWS])
def test_flow_allows_a_genuinely_different_checker(label, call, factory):
    """The valid path must still work — the fix must not turn dual control into no control."""
    session = _ApprovalStub()
    call(session, factory(_MAKER.id), _CHECKER)
    assert any(isinstance(o, AuditLog) for o in session.added), \
        f"{label} must still audit a successful approval"


# ══════════════════════════════════════════════════════════════════════════════════════
# FIX 1 + FIX 2 · invoice audit and one-live-invoice-per-order, against real PostgreSQL
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestInvoiceIntegrity:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"inv-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Fin", email=f"inv-{uuid.uuid4().hex[:8]}@t.test",
                       username=f"inv{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            entity_code = f"ent_{uuid.uuid4().hex[:8]}"
            entity = SellerLegalEntity(code=entity_code, legal_name="Test Entity Ltd",
                                       country="GB", status="active")
            account = CommercialAccount(org_id=org.id, seller_legal_entity_id=entity_code)
            catalog = CatalogVersion(version_label="v1", vertical=f"inv-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([entity, account, catalog])
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Invoice integrity",
                         status="published", billing_classification="commercial")
            db.add(event)
            db.flush()
            order = EventOrder(
                event_id=event.id, commercial_account_id=account.id,
                catalog_version_id=catalog.id, currency="GBP",
                subtotal=Decimal("100.00"), tax_amount=Decimal("20.00"),
                tax_treatment="standard_rate", tax_jurisdiction="GB",
                tax_source="finance_manual_determination",
                total_amount=Decimal("120.00"), status="accepted",
                idempotency_key=str(uuid.uuid4()),
            )
            db.add(order)
            db.commit()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, user=user, event_id=event.id,
                                  order_id=order.id, account_id=account.id,
                                  catalog_id=catalog.id, entity_code=entity_code)
        yield ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM invoices WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM invoice_number_sequences WHERE scope LIKE :s",
                 {"s": f"%{ids.entity_code}%"}),
                ("DELETE FROM audit_logs WHERE org_id = :o OR actor_id = :u",
                 {"o": ids.org_id, "u": ids.user_id}),
                ("DELETE FROM payment_schedules WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM commercial_state_transitions WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM event_orders WHERE id = :o", {"o": ids.order_id}),
                ("DELETE FROM events WHERE id = :e", {"e": ids.event_id}),
                ("DELETE FROM catalog_versions WHERE id = :c", {"c": ids.catalog_id}),
                ("DELETE FROM commercial_accounts WHERE id = :a", {"a": ids.account_id}),
                ("DELETE FROM seller_legal_entities WHERE code = :c", {"c": ids.entity_code}),
                ("DELETE FROM users WHERE id = :u", {"u": ids.user_id}),
                ("DELETE FROM organizations WHERE id = :o", {"o": ids.org_id}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    # ── FIX 1 ────────────────────────────────────────────────────────────────────────
    def test_successful_issuance_writes_an_audit_record(self, ctx):
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            invoice = crud.issue_invoice(db, order, actor=actor)

            entry = db.query(AuditLog).filter(
                AuditLog.action == "commercial.invoice.issue",
                AuditLog.target_id == str(invoice.id),
            ).one()
            assert entry.target_type == "invoice"
            assert entry.actor_id == actor.id
            assert entry.org_id == ctx.org_id

    def test_audit_record_identifies_the_financial_object(self, ctx):
        """An audit row that cannot be tied back to the document and the money it represents
        is not evidence."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            invoice = crud.issue_invoice(db, order, actor=actor)

            entry = db.query(AuditLog).filter(
                AuditLog.action == "commercial.invoice.issue",
                AuditLog.target_id == str(invoice.id),
            ).one()
            assert entry.meta["event_order_id"] == str(ctx.order_id)
            assert entry.meta["number"] == invoice.number
            assert entry.meta["total_amount"] == "120.00"
            assert entry.meta["currency"] == "GBP"
            assert entry.meta["seller_legal_entity_id"] == ctx.entity_code

    def test_failed_issuance_records_no_successful_audit(self, ctx):
        """Tax undetermined → refusal. No invoice, and above all no audit row claiming one was
        issued. The flush-then-audit ordering is what guarantees this."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            order.tax_amount = None
            db.commit()

            actor = db.get(User, ctx.user_id)
            with pytest.raises(ValueError, match="no tax determination"):
                crud.issue_invoice(db, order, actor=actor)
            db.rollback()

        with Session(engine) as db:
            assert db.query(AuditLog).filter(
                AuditLog.action == "commercial.invoice.issue",
                AuditLog.org_id == ctx.org_id,
            ).count() == 0
            assert db.query(Invoice).filter(Invoice.event_order_id == ctx.order_id).count() == 0

    # ── FIX 2 ────────────────────────────────────────────────────────────────────────
    def test_second_invoice_for_the_same_order_is_refused(self, ctx):
        from sqlalchemy.exc import IntegrityError

        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            first = crud.issue_invoice(db, order, actor=actor)
            assert first.id is not None

        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            with pytest.raises(IntegrityError):
                crud.issue_invoice(db, order, actor=actor)
            db.rollback()

        with Session(engine) as db:
            assert db.query(Invoice).filter(Invoice.event_order_id == ctx.order_id).count() == 1

    def test_concurrent_issuance_cannot_create_two_invoices(self, ctx):
        """The reason this is a DATABASE constraint and not an `if exists` check: two requests
        that both pass an application-level check still race. Exactly one must win."""
        results, errors = [], []
        barrier = __import__("threading").Barrier(2)

        def issue():
            try:
                with Session(engine) as db:
                    order = db.get(EventOrder, ctx.order_id)
                    actor = db.get(User, ctx.user_id)
                    barrier.wait(timeout=15)
                    inv = crud.issue_invoice(db, order, actor=actor)
                    results.append(inv.id)
            except Exception as exc:
                errors.append(type(exc).__name__)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(issue) for _ in range(2)]
            concurrent.futures.wait(futures, timeout=60)

        assert len(results) == 1, f"exactly one issuance must succeed; got {results} / {errors}"
        assert len(errors) == 1, f"exactly one issuance must be refused; got {errors}"
        with Session(engine) as db:
            assert db.query(Invoice).filter(Invoice.event_order_id == ctx.order_id).count() == 1

    def test_a_voided_invoice_frees_the_order_for_reissue(self, ctx):
        """The index is PARTIAL on state <> 'void' precisely so the model's declared
        draft|issued|paid|void vocabulary keeps its void-then-reissue path. A blanket unique
        would have removed a behaviour the model already allows."""
        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            first = crud.issue_invoice(db, order, actor=actor)
            first_id = first.id

        with Session(engine) as db:
            db.get(Invoice, first_id).state = "void"
            db.commit()

        with Session(engine) as db:
            order = db.get(EventOrder, ctx.order_id)
            actor = db.get(User, ctx.user_id)
            second = crud.issue_invoice(db, order, actor=actor)
            assert second.id != first_id
            assert second.number != db.get(Invoice, first_id).number, \
                "numbering must keep advancing — a re-issue is a new document"
