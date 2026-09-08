"""Maker-checker for period close and reconciliation-exception resolution (audit 2026-08-27,
P0.10) — closes the two gaps the audit found in crud.commercial.close_period and
crud.commercial.resolve_exception, which previously let one actor both prepare and finalize.

Mirrors the existing CommercialException maker-checker tests in test_payment_path.py:
  * pure-logic tests run against `_StubSession` (no DB, always run)
  * `needs_db` tests exercise the real two-step lifecycle end-to-end against Postgres, and skip
    when no database is reachable (see conftest.py for how TEST_DATABASE_URL is required).
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import commercial as crud
from app.db import engine
from app.models import AuditLog, FinancialPeriod, Organization, ReconciliationException, User


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


class _StubSession:
    """Same minimal stub as test_payment_path.py's — sufficient for any code path that only
    adds/commits/refreshes, never runs a real query. close_period's snapshot/exception-filing
    steps DO run real queries and are covered separately under `needs_db` below."""

    def __init__(self):
        self.added = []

    def add(self, o):
        self.added.append(o)

    def flush(self):
        pass

    def commit(self):
        pass

    def refresh(self, o):
        pass


_MAKER = SimpleNamespace(id=uuid.uuid4(), email="finance-maker@zoikostream.com")
_CHECKER = SimpleNamespace(id=uuid.uuid4(), email="finance-checker@zoikostream.com")


# ══════════════════════════════════════════════════════════════════════════════════════
# Period close — pure logic
# ══════════════════════════════════════════════════════════════════════════════════════

def test_close_period_refuses_a_non_open_period_before_any_query():
    """The status guard runs before _file_exceptions/_period_snapshot, so this is safely
    testable with a stub — a real query would fail loudly, not silently pass."""
    period = FinancialPeriod(id=uuid.uuid4(), label="2026-08", status="pending_close")
    with pytest.raises(ValueError, match="already pending_close"):
        crud.close_period(_StubSession(), period, _MAKER)


def test_confirm_period_close_refuses_a_period_not_pending():
    period = FinancialPeriod(id=uuid.uuid4(), label="2026-08", status="open")
    with pytest.raises(ValueError, match="not awaiting confirmation"):
        crud.confirm_period_close(_StubSession(), period, _CHECKER)


def test_confirm_period_close_refuses_the_preparer_as_confirmer():
    period = FinancialPeriod(id=uuid.uuid4(), label="2026-08", status="pending_close",
                             prepared_by=_MAKER.id)
    with pytest.raises(ValueError, match="Maker-checker violation"):
        crud.confirm_period_close(_StubSession(), period, _MAKER)


def test_confirm_period_close_by_a_different_actor_succeeds_and_audits():
    period = FinancialPeriod(id=uuid.uuid4(), label="2026-08", status="pending_close",
                             prepared_by=_MAKER.id)
    session = _StubSession()
    result = crud.confirm_period_close(session, period, _CHECKER)
    assert result.status == "closed"
    assert result.closed_by == _CHECKER.id
    assert result.closed_at is not None
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.period.confirm_close"
    assert entry.meta["prepared_by"] == str(_MAKER.id)


def test_confirm_period_close_does_not_recompute_the_snapshot():
    frozen = {"orders": {"count": 3, "total_amount": "900.00"}}
    period = FinancialPeriod(id=uuid.uuid4(), label="2026-08", status="pending_close",
                             prepared_by=_MAKER.id, snapshot=frozen)
    crud.confirm_period_close(_StubSession(), period, _CHECKER)
    assert period.snapshot == frozen  # unchanged by confirm — prepare froze it once


# ══════════════════════════════════════════════════════════════════════════════════════
# Reconciliation exception resolution — pure logic
# ══════════════════════════════════════════════════════════════════════════════════════

def test_investigating_applies_immediately_no_maker_checker():
    """Not a financial decision — no dual control expected or applied."""
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="open")
    session = _StubSession()
    result = crud.resolve_exception(session, exc, _MAKER, status="investigating")
    assert result.status == "investigating"
    assert result.owner_id == _MAKER.id
    assert result.prepared_by is None  # only a terminal proposal sets prepared_by


def test_resolve_exception_rejects_invalid_status():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="open")
    with pytest.raises(ValueError, match="Invalid exception resolution status"):
        crud.resolve_exception(_StubSession(), exc, _MAKER, status="closed")


def test_resolve_exception_refuses_an_already_terminal_exception():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="resolved")
    with pytest.raises(ValueError, match="already resolved"):
        crud.resolve_exception(_StubSession(), exc, _MAKER, status="accepted_risk")


def test_proposing_a_terminal_outcome_does_not_finalize_it():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="open")
    session = _StubSession()
    result = crud.resolve_exception(session, exc, _MAKER, status="resolved", resolution_notes="looks fine")
    assert result.status == "pending_review"          # NOT "resolved" yet
    assert result.proposed_status == "resolved"
    assert result.prepared_by == _MAKER.id
    assert result.resolved_at is None                  # only confirm sets this
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.reconciliation_exception.propose_resolution"


def test_cannot_propose_a_second_resolution_while_one_is_pending():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="pending_review",
                                  proposed_status="resolved", prepared_by=_MAKER.id)
    with pytest.raises(ValueError, match="already has a resolution awaiting"):
        crud.resolve_exception(_StubSession(), exc, _CHECKER, status="accepted_risk")


def test_confirm_exception_resolution_refuses_a_non_pending_exception():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="open")
    with pytest.raises(ValueError, match="Cannot confirm"):
        crud.confirm_exception_resolution(_StubSession(), exc, _CHECKER)


def test_confirm_exception_resolution_refuses_the_preparer_as_confirmer():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="pending_review",
                                  proposed_status="accepted_risk", prepared_by=_MAKER.id)
    with pytest.raises(ValueError, match="Maker-checker violation"):
        crud.confirm_exception_resolution(_StubSession(), exc, _MAKER)


def test_confirm_exception_resolution_by_a_different_actor_applies_the_proposed_status():
    exc = ReconciliationException(id=uuid.uuid4(), period_id=uuid.uuid4(), category="c",
                                  reference_type="t", reference_id=uuid.uuid4(),
                                  description="d", status="pending_review",
                                  proposed_status="accepted_risk", prepared_by=_MAKER.id)
    session = _StubSession()
    result = crud.confirm_exception_resolution(session, exc, _CHECKER, resolution_notes="ok, low value")
    assert result.status == "accepted_risk"            # exactly what was proposed
    assert result.resolved_at is not None
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.reconciliation_exception.confirm_resolution"
    assert entry.meta["prepared_by"] == str(_MAKER.id)


def test_confirmer_cannot_choose_a_different_outcome_than_proposed():
    """confirm_exception_resolution takes no status argument at all — this test documents
    that constraint at the call-signature level rather than only by convention."""
    import inspect
    params = inspect.signature(crud.confirm_exception_resolution).parameters
    assert "status" not in params


# ══════════════════════════════════════════════════════════════════════════════════════
# Full two-step lifecycle against a real database
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestGovernanceLifecycle:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"gov-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            maker = User(org_id=org.id, full_name="Maker", email=f"maker-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"maker{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            checker = User(org_id=org.id, full_name="Checker", email=f"checker-{uuid.uuid4().hex[:8]}@t.test",
                          username=f"checker{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add_all([maker, checker])
            db.flush()
            ids = SimpleNamespace(org_id=org.id, maker=maker, checker=checker, periods=[], exceptions=[])
            yield db, ids
            for xid in ids.exceptions:
                db.execute(text("DELETE FROM reconciliation_exceptions WHERE id=:x"), {"x": xid})
            for pid in ids.periods:
                # close_period FILES exceptions against the period (that is the feature), so
                # rows exist here that this fixture never created and cannot know the ids of.
                # They must go before their parent or the FK refuses the delete.
                db.execute(text("DELETE FROM reconciliation_exceptions WHERE period_id=:p"), {"p": pid})
                db.execute(text("DELETE FROM financial_periods WHERE id=:p"), {"p": pid})
            db.execute(text("DELETE FROM users WHERE id IN (:m,:c)"), {"m": maker.id, "c": checker.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()

    def _period(self, db, ids, label=None):
        period = crud.get_or_create_period(
            db, label=label or f"t{uuid.uuid4().hex[:6]}",
            period_start=datetime.now(timezone.utc) - timedelta(days=30),
            period_end=datetime.now(timezone.utc),
        )
        ids.periods.append(period.id)
        return period

    def test_period_close_requires_two_different_actors_end_to_end(self, ctx):
        db, ids = ctx
        period = self._period(db, ids)

        prepared = crud.close_period(db, period, ids.maker)
        assert prepared.status == "pending_close"
        assert prepared.snapshot is not None

        with pytest.raises(ValueError, match="Maker-checker violation"):
            crud.confirm_period_close(db, period, ids.maker)
        assert period.status == "pending_close"  # unchanged by the rejected attempt

        confirmed = crud.confirm_period_close(db, period, ids.checker)
        assert confirmed.status == "closed"
        assert confirmed.prepared_by == ids.maker.id
        assert confirmed.closed_by == ids.checker.id

    def test_closed_period_cannot_be_closed_again(self, ctx):
        db, ids = ctx
        period = self._period(db, ids)
        crud.close_period(db, period, ids.maker)
        crud.confirm_period_close(db, period, ids.checker)
        with pytest.raises(ValueError, match="already closed"):
            crud.close_period(db, period, ids.maker)

    def test_reconciliation_exception_resolution_requires_two_different_actors(self, ctx):
        db, ids = ctx
        period = self._period(db, ids)
        exc = ReconciliationException(
            id=uuid.uuid4(), period_id=period.id, category="unmatched_settlement",
            reference_type="event_order", reference_id=uuid.uuid4(),
            description="Test-only exception", status="open",
        )
        db.add(exc)
        db.commit()
        ids.exceptions.append(exc.id)

        proposed = crud.resolve_exception(db, exc, ids.maker, status="accepted_risk",
                                          resolution_notes="Immaterial amount")
        assert proposed.status == "pending_review"

        with pytest.raises(ValueError, match="Maker-checker violation"):
            crud.confirm_exception_resolution(db, exc, ids.maker)

        confirmed = crud.confirm_exception_resolution(db, exc, ids.checker)
        assert confirmed.status == "accepted_risk"
        assert confirmed.resolved_at is not None
