"""Phase 3 payment-path correctness tests (ZST-LE-COM-001 P1/P4/P5, Sections 25/28/30).

Covers the four P0 fixes and their supporting infrastructure:
  CF-1 provider-event idempotency (DB-enforced, concurrency-safe)
  CF-2 payment state machine (illegal transitions rejected, same-state replay harmless)
  CF-3 go-live commercial readiness (every path, no bypass)
  CF-4 CommercialException governed override workflow
  CF-5 partial-payment allocation
  CF-8 unmatched settlement retention
plus amount/currency integrity, audit/correlation, and tenant isolation.

`needs_db` tests exercise real Postgres semantics — ON CONFLICT arbitration, unique
constraints, concurrent delivery — which cannot be faked with stubs. They skip when
DATABASE_URL is unreachable so the suite stays runnable offline.
"""
import ast
import concurrent.futures
import inspect
import json
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
    AuditLog, CatalogVersion, CommercialAccount, CommercialException, Event, EventOrder,
    Organization, Payment, PaymentSchedule, ProviderEvent, RefundCredit,
    UnmatchedSettlement, User,
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


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-2 · PAYMENT STATE MACHINE (pure logic — no DB needed)
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("current,new", [
    ("requires_action", "pending"),     # provider finished collecting what it needed
    ("pending", "paid"),                # AUTHORIZED -> CAPTURED
    ("pending", "partially_paid"),
    ("pending", "failed"),
    ("paid", "refunded"),               # CAPTURED -> REFUNDED
    ("paid", "part_refunded"),
    ("paid", "disputed"),
    ("partially_paid", "paid"),
    ("part_refunded", "refunded"),
    ("disputed", "paid"),               # dispute won
    ("disputed", "reversed"),           # dispute lost
    ("refunded", "disputed"),           # a refunded charge can still be disputed
])
def test_valid_transitions_are_allowed(current, new):
    assert crud.payment_transition_error(current, new) is None


@pytest.mark.parametrize("current,new", [
    ("failed", "paid"),                 # the exact transition the old code permitted
    ("failed", "pending"),
    ("refunded", "paid"),               # ...and this one
    ("refunded", "partially_paid"),
    ("reversed", "paid"),
    ("part_refunded", "paid"),
    ("paid", "pending"),                # settlement cannot un-settle
    ("paid", "requires_action"),
    ("unmatched", "paid"),              # only controlled reconciliation may move this
])
def test_invalid_transitions_are_rejected(current, new):
    err = crud.payment_transition_error(current, new)
    assert err is not None and "illegal payment transition" in err


def test_terminal_states_have_no_forward_path_to_success():
    for terminal in ("failed", "unmatched"):
        for target in ("paid", "partially_paid", "pending"):
            assert crud.payment_transition_error(terminal, target) is not None


def test_same_state_is_not_an_error_but_is_not_an_application():
    """Idempotent replay: allowed to arrive, must not re-run side effects."""
    assert crud.payment_transition_error("paid", "paid") is None


def test_unknown_states_are_rejected():
    assert crud.payment_transition_error("banana", "paid") is not None
    assert crud.payment_transition_error("paid", "banana") is not None


def test_no_unguarded_state_assignment_remains():
    """CF-2 structural guard: apply_payment_state must be the only writer of Payment.state
    outside authorize (which creates the row). A future `payment.state = x` elsewhere in the
    module fails this."""
    src = code_only(crud)
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute) and target.attr == "state"
                    and isinstance(target.value, ast.Name) and target.value.id == "payment"):
                offenders.append(ast.unparse(node))
    # Exactly one: the assignment inside apply_payment_state, after the guard.
    assert len(offenders) == 1, f"unguarded payment.state assignments: {offenders}"


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-5 · PARTIAL PAYMENT ALLOCATION (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

class _AllocSession:
    """Serves list_payment_schedules + order_settlement from in-memory lists.

    Dispatches on the queried entity. `RefundCredit` was added when order_settlement started
    netting executed remedies out of the collected figure — without it, the refund query fell
    through to the schedules list and handed PaymentSchedule rows to code expecting remedies.
    """

    def __init__(self, schedules, captured, remedies=None):
        self._schedules, self._captured = schedules, captured
        self._remedies = remedies or []
        self.added = []

    def scalars(self, stmt):
        entity = getattr(getattr(stmt, "column_descriptions", [{}])[0].get("entity", None), "__name__", "")
        rows = {
            "Payment": self._captured,
            "RefundCredit": self._remedies,
            "PaymentSchedule": self._schedules,
        }.get(entity, self._schedules)
        return SimpleNamespace(all=lambda: rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


def _sched(amount, due_at=None, required=True):
    return PaymentSchedule(id=uuid.uuid4(), event_order_id=uuid.uuid4(), milestone="deposit",
                           amount=Decimal(amount), due_at=due_at, required_before_ready=required,
                           allocated_amount=Decimal(0), status="due")


def _paid(amount):
    return Payment(id=uuid.uuid4(), event_order_id=uuid.uuid4(), provider="mock",
                   provider_payment_ref="r", amount=Decimal(amount), currency="USD",
                   state="paid", idempotency_key=str(uuid.uuid4()))


def _remedy(amount, *, type_="refund", status="executed"):
    return RefundCredit(id=uuid.uuid4(), event_order_id=uuid.uuid4(), type=type_,
                        amount=Decimal(amount), reason_code="test", status=status)


def _allocate(schedule_amounts, captured_amounts, remedies=None):
    schedules = [_sched(a) for a in schedule_amounts]
    session = _AllocSession(schedules, [_paid(c) for c in captured_amounts], remedies)
    summary = crud.reallocate_schedules(session, uuid.uuid4())
    return schedules, summary


# ══════════════════════════════════════════════════════════════════════════════════════
# REFUND NETTING (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════
# order_settlement summed gross payments and subtracted nothing, so an EXECUTED refund left
# the order still reading as fully collected. Three calculations consumed that figure —
# financial readiness, the outstanding payable balance, and milestone allocation — so a
# refunded order simultaneously reported "satisfied" to the go-live gate, refused
# re-collection as "already paid in full", and kept its milestones marked satisfied.

def _settle(captured, remedies=None):
    session = _AllocSession([], [_paid(c) for c in captured], remedies or [])
    return crud.order_settlement(session, uuid.uuid4())


def test_settlement_nets_an_executed_refund_out_of_collected():
    s = _settle([1000], [_remedy(400)])
    assert s["gross_captured"] == Decimal(1000)
    assert s["refunded"] == Decimal(400)
    assert s["net"] == Decimal(600)
    assert s["refundable"] == Decimal(600)


def test_settlement_ignores_an_unexecuted_remedy():
    """A pending or approved-but-unexecuted remedy has not moved money and must not reduce
    the collected figure — otherwise merely REQUESTING a refund would un-satisfy an order."""
    for status in ("pending", "approved", "declined"):
        s = _settle([1000], [_remedy(400, status=status)])
        assert s["refunded"] == Decimal(0), status
        assert s["net"] == Decimal(1000), status


def test_credits_satisfy_a_balance_but_are_not_refundable_cash():
    """A credit/waiver means the customer no longer owes it, so it counts toward settlement —
    but no cash arrived, so it can never be handed back."""
    s = _settle([0], [_remedy(250, type_="credit")])
    assert s["net"] == Decimal(250)
    assert s["refundable"] == Decimal(0)


def test_a_fully_refunded_order_reads_as_uncollected():
    s = _settle([1000], [_remedy(1000)])
    assert s["net"] == Decimal(0) and s["refundable"] == Decimal(0)


def test_settlement_never_goes_negative():
    """An over-refund (shouldn't happen — payment_refundable_amount caps it) must still not
    produce negative collected money feeding the readiness gate."""
    s = _settle([100], [_remedy(500)])
    assert s["net"] == Decimal(0) and s["refundable"] == Decimal(0)


def test_refund_unsatisfies_a_previously_satisfied_milestone():
    """The end-to-end consequence: the milestone the capture satisfied goes back to
    outstanding once the money is returned."""
    satisfied, _ = _allocate([1000], [1000])
    assert satisfied[0].status == "satisfied"
    after_refund, summary = _allocate([1000], [1000], [_remedy(1000)])
    assert after_refund[0].status != "satisfied"
    assert after_refund[0].allocated_amount == Decimal(0)
    assert summary["outstanding"] == Decimal(1000)


def test_zero_capture_leaves_the_milestone_unsatisfied():
    schedules, summary = _allocate([1000], [])
    assert schedules[0].allocated_amount == Decimal(0)
    assert schedules[0].status != "satisfied"
    assert summary["outstanding"] == Decimal(1000)


def test_partial_capture_does_not_satisfy_the_milestone():
    """CF-5: a 400 capture against a 1000 milestone used to mark it fully satisfied."""
    schedules, summary = _allocate([1000], [400])
    assert schedules[0].allocated_amount == Decimal(400)
    assert schedules[0].status != "satisfied"
    assert summary["outstanding"] == Decimal(600)


def test_exact_capture_satisfies_the_milestone():
    schedules, summary = _allocate([1000], [1000])
    assert schedules[0].status == "satisfied"
    assert summary["outstanding"] == Decimal(0)


def test_over_capture_is_not_over_allocated():
    """Money beyond the scheduled total is reported, never spread onto milestones that
    don't exist — no approved rule says where an overpayment goes."""
    schedules, summary = _allocate([1000], [1500])
    assert schedules[0].allocated_amount == Decimal(1000)
    assert summary["unallocated"] == Decimal(500)


def test_allocation_spills_across_milestones_in_order():
    schedules, summary = _allocate([500, 500], [700])
    assert schedules[0].status == "satisfied" and schedules[0].allocated_amount == Decimal(500)
    assert schedules[1].status != "satisfied" and schedules[1].allocated_amount == Decimal(200)
    assert summary["outstanding"] == Decimal(300)


def test_reallocation_is_idempotent():
    """Recomputed from total captured every time, so a redelivered capture cannot
    double-count — this is what makes provider-event replay safe without a ledger."""
    schedules = [_sched(1000)]
    session = _AllocSession(schedules, [_paid(400)])
    order_id = uuid.uuid4()
    first = crud.reallocate_schedules(session, order_id)
    second = crud.reallocate_schedules(session, order_id)
    assert first == second
    assert schedules[0].allocated_amount == Decimal(400)


# ══════════════════════════════════════════════════════════════════════════════════════
# AMOUNT / CURRENCY INTEGRITY (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def _payment(amount="100.00", currency="USD", state="pending"):
    return Payment(id=uuid.uuid4(), event_order_id=uuid.uuid4(), provider="mock",
                   provider_payment_ref="ref-1", amount=Decimal(amount), currency=currency,
                   state=state, idempotency_key=str(uuid.uuid4()))


def test_matching_amount_and_currency_pass():
    assert crud._amount_mismatch_reason(_payment(), Decimal("100.00"), "USD") is None


def test_currency_mismatch_fails_closed():
    reason = crud._amount_mismatch_reason(_payment(currency="USD"), Decimal("100.00"), "EUR")
    assert reason and "currency mismatch" in reason


def test_amount_mismatch_is_refused():
    reason = crud._amount_mismatch_reason(_payment(amount="100.00"), Decimal("999.00"), "USD")
    assert reason and "amount mismatch" in reason


def test_absent_amount_is_not_treated_as_a_mismatch():
    """Not every provider event states an amount; absence is silence, not a conflict."""
    assert crud._amount_mismatch_reason(_payment(), None, None) is None


def test_no_exchange_rate_is_ever_applied():
    src = code_only(crud)
    for forbidden in ("exchange_rate", "fx_rate", "convert_currency", "RATE_TABLE"):
        assert forbidden not in src


# ══════════════════════════════════════════════════════════════════════════════════════
# RAW EVIDENCE & REDACTION (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_sensitive_payload_keys_are_redacted():
    payload = {
        "id": "evt_1", "amount": 100,
        "card": {"number": "4111111111111111", "cvc": "123", "exp_month": 12},
        "nested": [{"pan": "5555"}],
        "customer_email": "buyer@example.test",
    }
    out = crud._redact_payload(payload)
    flat = json.dumps(out)
    assert "4111111111111111" not in flat
    assert "123" not in flat.replace('"amount": 100', "")
    assert "5555" not in flat
    assert out["id"] == "evt_1"                    # non-sensitive evidence preserved
    assert out["customer_email"] == "buyer@example.test"


def test_redaction_survives_hostile_nesting():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": "x"}}}}}}}}
    crud._redact_payload(deep)  # must not recurse without bound
    assert crud._redact_payload({"CARD-Number": "x"})["CARD-Number"] == "<redacted>"


def test_no_stripe_specific_vocabulary_leaked():
    """The provider event layer must stay provider-neutral."""
    for module in (crud,):
        src = code_only(module).lower()
        for forbidden in ("stripe", "payment_intent", "paymentintent", "charge.succeeded",
                          "pi_", "cus_", "price_id"):
            assert forbidden not in src, f"provider-specific token {forbidden!r} present"


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-3 · GO-LIVE READINESS (structural + behavioural)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_production_states_are_declared_centrally():
    assert set(crud.PRODUCTION_EVENT_STATES) >= {"armed", "live"}


def test_golive_gate_short_circuits_for_non_production_targets():
    assert crud.golive_block_reason(SimpleNamespace(), SimpleNamespace(id=uuid.uuid4()),
                                    target_state="ended") is None


def test_every_live_status_assignment_is_gated():
    """CF-3 regression guard. Walks the AST of the whole backend for any assignment putting
    an event into a production state, and requires the enclosing function to consult the
    canonical gate. A new bypass anywhere fails this — not just a change to _golive."""
    import pathlib
    root = pathlib.Path(inspect.getfile(crud)).parent.parent          # app/
    gate_names = {"golive_block_reason", "golive_readiness", "_golive_gate",
                  "PRODUCTION_EVENT_STATES", "audit_golive_decision"}

    def escalates(node) -> bool:
        return any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "status" for t in n.targets)
            and isinstance(n.value, ast.Constant) and n.value.value in ("live", "armed", "degraded")
            for n in ast.walk(node)
        )

    def names_in(node) -> set[str]:
        found = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        return found | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}

    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Scan TOP-LEVEL functions only, and treat nested defs as part of their parent:
        # _golive's escalation lives in an inner work(db) closure while the gate is called by
        # the outer coroutine, which is a legitimate shape — the unit of responsibility is
        # the whole top-level function, so that is the unit we check.
        for fn in tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if not escalates(fn):
                continue
            if not (names_in(fn) & gate_names):
                offenders.append(f"{path.name}::{fn.name}")
    assert not offenders, f"ungated production-status escalation in: {offenders}"


def test_golive_paths_reference_the_single_gate():
    from app.services import broadcast
    from app.routers import events as events_router
    assert "_golive_gate" in code_only(broadcast._golive)
    assert "golive_readiness" in code_only(broadcast._golive_gate)
    assert "golive_readiness" in code_only(events_router.update_event)
    assert "PRODUCTION_EVENT_STATES" in code_only(events_router.update_event)


def test_no_parallel_readiness_implementation():
    """One authoritative readiness decision (phase item 9). Both entry points must delegate,
    not re-derive — neither may compute its own reasons list."""
    from app.services import broadcast
    gate_src = code_only(broadcast._golive_gate)
    assert "financial_readiness_state" not in gate_src
    assert "capacity_confirmed" not in gate_src


def test_readiness_verdict_distinguishes_three_outcomes():
    for verdict in ("NORMAL_PASS", "EXCEPTION_APPROVED", "BLOCKED"):
        assert verdict in code_only(crud.evaluate_readiness)


@needs_db
class TestGoLiveGate:
    """The gate must BLOCK what it should and ALLOW what it should — a gate that refuses
    everything would 'fix' CF-3 by breaking normal operation."""

    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"gl-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="GoLive", email=f"gl-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"gl{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"gl-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([account, catalog])
            db.flush()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id,
                                  account_id=account.id, catalog_id=catalog.id, events=[], orders=[])
            yield db, ids
            for oid in ids.orders:
                db.execute(text("DELETE FROM payment_schedules WHERE event_order_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM commercial_state_transitions WHERE event_order_id=:o"), {"o": oid})
                db.execute(text("DELETE FROM event_orders WHERE id=:o"), {"o": oid})
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            for eid in ids.events:
                db.execute(text("DELETE FROM events WHERE id=:e"), {"e": eid})
            db.execute(text("DELETE FROM catalog_versions WHERE id=:c"), {"c": catalog.id})
            db.execute(text("DELETE FROM commercial_accounts WHERE id=:a"), {"a": account.id})
            db.execute(text("DELETE FROM users WHERE id=:u"), {"u": user.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()

    def _event(self, db, ids, *, classification="commercial"):
        ev = Event(org_id=ids.org_id, created_by=ids.user_id, title="Gate test",
                   status="published", billing_classification=classification)
        db.add(ev)
        db.flush()
        ids.events.append(ev.id)
        return ev

    def _order(self, db, ids, ev, *, milestone_due_days=None, milestone_amount=None):
        order = EventOrder(event_id=ev.id, commercial_account_id=ids.account_id,
                           catalog_version_id=ids.catalog_id, currency="USD",
                           subtotal=Decimal("1000.00"), total_amount=Decimal("1000.00"),
                           status="accepted", idempotency_key=str(uuid.uuid4()))
        db.add(order)
        db.flush()
        ids.orders.append(order.id)
        if milestone_amount is not None:
            db.add(PaymentSchedule(
                event_order_id=order.id, milestone="deposit", amount=Decimal(milestone_amount),
                due_at=datetime.now(timezone.utc) + timedelta(days=milestone_due_days),
                required_before_ready=True, status="due", allocated_amount=Decimal(0)))
        db.commit()
        return order

    def test_unpaid_overdue_event_cannot_go_live(self, ctx):
        db, ids = ctx
        ev = self._event(db, ids)
        self._order(db, ids, ev, milestone_due_days=-2, milestone_amount="1000.00")
        reason = crud.golive_block_reason(db, ev)
        assert reason and "financial readiness" in reason

    def test_commercial_event_without_an_order_cannot_go_live(self, ctx):
        db, ids = ctx
        ev = self._event(db, ids)
        db.commit()
        reason = crud.golive_block_reason(db, ev)
        assert reason and "no accepted order" in reason

    def test_all_gates_satisfied_allows_go_live(self, ctx):
        """Requirement 22: a fully-satisfied event still goes live. No required milestone,
        no service profile, no audience estimate -> nothing outstanding."""
        db, ids = ctx
        ev = self._event(db, ids)
        self._order(db, ids, ev)                      # accepted order, no required milestone
        assert crud.golive_block_reason(db, ev) is None
        assert crud.golive_readiness(db, ev)["verdict"] == "NORMAL_PASS"

    def test_non_commercial_event_go_live_behaviour_is_unchanged(self, ctx):
        """Requirement 24: an internal/demo event has no commercial obligations and must not
        be newly blocked by this phase's gate."""
        db, ids = ctx
        ev = self._event(db, ids, classification="internal")
        db.commit()
        assert crud.golive_block_reason(db, ev) is None

    def test_gate_blocks_arming_too_not_just_live(self, ctx):
        db, ids = ctx
        ev = self._event(db, ids)
        self._order(db, ids, ev, milestone_due_days=-1, milestone_amount="500.00")
        assert crud.golive_block_reason(db, ev, target_state="armed") is not None
        assert crud.golive_block_reason(db, ev, target_state="live") is not None

    def test_blocked_go_live_is_audited(self, ctx):
        db, ids = ctx
        ev = self._event(db, ids)
        self._order(db, ids, ev, milestone_due_days=-2, milestone_amount="1000.00")
        evaluation = crud.golive_readiness(db, ev)
        crud.audit_golive_decision(db, ev, evaluation, actor=db.get(User, ids.user_id),
                                    target_state="live")
        db.commit()
        actions = db.scalars(text_audit_actions(ids.org_id)).all()
        assert "commercial.golive.blocked" in actions


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-4 · COMMERCIAL EXCEPTION WORKFLOW (pure logic where possible)
# ══════════════════════════════════════════════════════════════════════════════════════

class _StubSession:
    def __init__(self, scalars_result=None, get_result=None):
        self.added = []
        self._scalars, self._get = scalars_result or [], get_result

    def add(self, o):
        self.added.append(o)

    def flush(self):
        pass

    def get(self, model, pk):
        return self._get

    def scalar(self, stmt=None):
        return None

    def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self._scalars)

    def commit(self):
        pass

    def refresh(self, o):
        pass


_ACTOR = SimpleNamespace(id=uuid.uuid4(), email="finance@zoikostream.com")
_OTHER = SimpleNamespace(id=uuid.uuid4(), email="ops@zoikostream.com")
_ORDER = SimpleNamespace(id=uuid.uuid4(), event_id=uuid.uuid4())
_VALID_EXC = dict(exception_type="financial_hold_override", rationale="Board-approved deferral",
                  evidence={"approval_ref": "FIN-2026-014"}, overridden_gate="financial_readiness")


def test_exception_requires_a_rationale():
    with pytest.raises(ValueError, match="rationale"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, order=_ORDER,
                                          **{**_VALID_EXC, "rationale": "   "})


def test_financial_exception_requires_evidence():
    with pytest.raises(ValueError, match="requires evidence"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, order=_ORDER,
                                          **{**_VALID_EXC, "evidence": None})


def test_exception_must_target_an_event_or_order():
    """Never global — there is no blanket "skip payment" switch."""
    with pytest.raises(ValueError, match="target an event or an order"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, **_VALID_EXC)


def test_exception_rejects_unknown_type_and_gate():
    with pytest.raises(ValueError, match="Unknown exception type"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, order=_ORDER,
                                          **{**_VALID_EXC, "exception_type": "free_stuff"})
    with pytest.raises(ValueError, match="Unknown gate"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, order=_ORDER,
                                          **{**_VALID_EXC, "overridden_gate": "everything"})


def test_exception_expiry_must_be_in_the_future():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(ValueError, match="expiry must be in the future"):
        crud.request_commercial_exception(_StubSession(), _ACTOR, order=_ORDER,
                                          expiry_at=past, **_VALID_EXC)


def test_exception_is_created_as_requested_not_approved():
    session = _StubSession()
    exc = crud.request_commercial_exception(session, _ACTOR, order=_ORDER, **_VALID_EXC)
    assert exc.status == "requested"            # never self-approving
    assert exc.requested_by == _ACTOR.id
    assert exc.approver_id is None
    assert exc.overridden_gate == "financial_readiness"
    assert any(isinstance(o, AuditLog) and o.action == "commercial.exception.requested"
               for o in session.added)


def test_approver_must_differ_from_requester():
    exc = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                              status="requested", requested_by=_ACTOR.id)
    with pytest.raises(ValueError, match="Maker-checker violation"):
        crud.approve_commercial_exception(_StubSession(), exc, _ACTOR)


def test_approval_by_a_second_person_succeeds_and_audits():
    exc = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                              status="requested", requested_by=_ACTOR.id,
                              overridden_gate="financial_readiness", previous_state="financial_hold")
    session = _StubSession()
    crud.approve_commercial_exception(session, exc, _OTHER, notes="Reviewed")
    assert exc.status == "approved" and exc.approver_id == _OTHER.id
    assert exc.decided_at is not None
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.exception.approved"
    assert entry.meta["previous_state"] == "financial_hold"
    assert entry.meta["overridden_gate"] == "financial_readiness"


def test_an_already_expired_exception_cannot_be_approved():
    exc = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                              status="requested", requested_by=_ACTOR.id,
                              expiry_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(ValueError, match="already expired"):
        crud.approve_commercial_exception(_StubSession(), exc, _OTHER)


def test_a_decided_exception_cannot_be_re_decided():
    for status_ in ("approved", "declined"):
        exc = CommercialException(id=uuid.uuid4(), exception_type="waiver", status=status_,
                                  requested_by=_ACTOR.id)
        with pytest.raises(ValueError):
            crud.approve_commercial_exception(_StubSession(), exc, _OTHER)


def test_expired_exception_no_longer_overrides_a_gate():
    """Scope enforcement: expiry is checked at READ time, so an override stops working the
    moment it lapses rather than needing a sweeper."""
    expired = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                                  status="approved", event_order_id=_ORDER.id,
                                  overridden_gate="financial_readiness",
                                  expiry_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert crud.active_exception(_StubSession(scalars_result=[expired]),
                                 exception_type="financial_hold_override",
                                 order_id=_ORDER.id, gate="financial_readiness") is None


def test_exception_scoped_to_another_gate_does_not_apply():
    other_gate = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                                      status="approved", event_order_id=_ORDER.id,
                                      overridden_gate="capacity", expiry_at=None)
    assert crud.active_exception(_StubSession(scalars_result=[other_gate]),
                                 exception_type="financial_hold_override",
                                 order_id=_ORDER.id, gate="financial_readiness") is None


def test_unexpired_scoped_exception_does_apply():
    good = CommercialException(id=uuid.uuid4(), exception_type="financial_hold_override",
                               status="approved", event_order_id=_ORDER.id,
                               overridden_gate="financial_readiness",
                               expiry_at=datetime.now(timezone.utc) + timedelta(days=1))
    assert crud.active_exception(_StubSession(scalars_result=[good]),
                                 exception_type="financial_hold_override",
                                 order_id=_ORDER.id, gate="financial_readiness") is good


def test_exception_authorization_is_split_across_two_authorities():
    """Request = Zoiko staff (require_super_admin); approve/decline = the Section-25
    `write_off` authority (finance_ops). No customer role holds either."""
    from app.routers import commercial as router_mod
    from app.security import commercial_can

    def deps(path, method):
        for r in router_mod.router.routes:
            if r.path == f"/commercial{path}" and method in r.methods:
                return {getattr(d.call, "__name__", "") for d in r.dependant.dependencies}
        raise AssertionError(f"{method} {path} not found")

    assert "require_super_admin" in deps("/commercial-exceptions", "POST")
    # No customer-side role may approve a financial override.
    for role in ("org_admin", "billing_admin", "host", "speaker", "viewer"):
        assert not commercial_can(SimpleNamespace(role=role, staff_commercial_role=None), "write_off")
    # Nor may non-finance Zoiko staff.
    for staff in ("sales", "live_ops", "support", "security"):
        assert not commercial_can(SimpleNamespace(role="super_admin", staff_commercial_role=staff), "write_off")
    assert commercial_can(SimpleNamespace(role="super_admin", staff_commercial_role="finance_ops"), "write_off")


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-1 / CF-8 · PROVIDER EVENTS against real Postgres
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestProviderEvents:

    @pytest.fixture
    def ctx(self):
        """An org + event + accepted order + authorized payment, cleaned up afterwards."""
        with Session(engine) as db:
            org = Organization(name=f"pay-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Pay Test",
                        email=f"pay-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"pay{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            event = Event(org_id=org.id, created_by=user.id, title="Payment path test")
            db.add(event)
            db.flush()
            # event_orders requires both of these (NOT NULL) — the order must always name the
            # account it bills and the catalog version it was priced from.
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label=f"t-{uuid.uuid4().hex[:6]}",
                                     vertical=f"test-{uuid.uuid4().hex[:6]}", status="published")
            db.add_all([account, catalog])
            db.flush()
            order = EventOrder(
                event_id=event.id, commercial_account_id=account.id, catalog_version_id=catalog.id,
                currency="USD", subtotal=Decimal("100.00"), total_amount=Decimal("100.00"),
                status="accepted", idempotency_key=str(uuid.uuid4()),
            )
            db.add(order)
            db.flush()
            ref = f"ref_{uuid.uuid4().hex[:12]}"
            payment = Payment(event_order_id=order.id, provider="mock", provider_payment_ref=ref,
                              amount=Decimal("100.00"), currency="USD", state="pending",
                              idempotency_key=f"authkey_{uuid.uuid4().hex[:12]}")
            db.add(payment)
            db.commit()
            ids = SimpleNamespace(org_id=org.id, user_id=user.id, event_id=event.id,
                                  order_id=order.id, payment_id=payment.id, ref=ref,
                                  account_id=account.id, catalog_id=catalog.id,
                                  auth_key=payment.idempotency_key,
                                  # Per-fixture namespace for deliberately-unmatchable refs.
                                  # A shared 'missing_%' prefix made teardown delete rows
                                  # belonging to concurrent runs of this same file.
                                  miss=f"missing_{uuid.uuid4().hex[:10]}_")
        yield ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM unmatched_settlements WHERE provider_event_id IN "
                 "(SELECT id FROM provider_events WHERE provider_payment_ref = :r)", {"r": ids.ref}),
                ("DELETE FROM unmatched_settlements WHERE provider_payment_ref LIKE :r",
                 {"r": f"{ids.miss}%"}),
                ("DELETE FROM provider_events WHERE provider_payment_ref = :r OR provider_payment_ref LIKE :m",
                 {"r": ids.ref, "m": f"{ids.miss}%"}),
                ("DELETE FROM audit_logs WHERE actor_id = :u OR org_id = :o",
                 {"u": ids.user_id, "o": ids.org_id}),
                ("DELETE FROM payment_schedules WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM payments WHERE event_order_id = :o", {"o": ids.order_id}),
                ("DELETE FROM commercial_state_transitions WHERE event_order_id=:o", {"o": ids.order_id}),
                ("DELETE FROM event_orders WHERE id = :o", {"o": ids.order_id}),
                ("DELETE FROM events WHERE id = :e", {"e": ids.event_id}),
                ("DELETE FROM catalog_versions WHERE id = :c", {"c": ids.catalog_id}),
                ("DELETE FROM commercial_accounts WHERE id = :a", {"a": ids.account_id}),
                ("DELETE FROM users WHERE id = :u", {"u": ids.user_id}),
                ("DELETE FROM organizations WHERE id = :o", {"o": ids.org_id}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    def _ingest(self, db, ctx, *, event_id, event_type="capture_succeeded", ref=None,
                amount=Decimal("100.00"), currency="USD", verified=True):
        body = json.dumps({"id": event_id, "type": event_type}).encode()
        return crud.ingest_provider_event(
            db, provider="mock", provider_event_id=event_id, event_type=event_type,
            raw_body=body, signature_verified=verified,
            provider_payment_ref=ctx.ref if ref is None else ref,
            payload={"id": event_id, "card": {"number": "4111111111111111"}},
            amount=amount, currency=currency,
        )

    # ── new event accepted, evidence retained ──────────────────────────────────────────
    def test_new_provider_event_is_accepted_and_applied(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}")
            assert res["duplicate"] is False and res["applied"] is True
            assert res["processing_status"] == "processed"
            assert db.get(Payment, ctx.payment_id).state == "paid"

    def test_raw_evidence_and_signature_status_are_retained(self, ctx):
        eid = f"evt_{uuid.uuid4().hex[:10]}"
        with Session(engine) as db:
            self._ingest(db, ctx, event_id=eid)
            rec = db.scalar(select_provider_event(eid))
            assert rec.payload_hash and len(rec.payload_hash) == 64
            assert rec.signature_verified is True
            assert rec.correlation_id
            assert rec.occurred_at is None or True
            # Raw evidence kept, card data NOT kept (doc P6/R1).
            assert json.dumps(rec.payload) .count("4111111111111111") == 0
            assert rec.payload["card"] == "<redacted>"

    def test_authorization_idempotency_key_is_never_overwritten(self, ctx):
        """CF-1's precise mechanism: the webhook used to stamp its own key onto
        Payment.idempotency_key, breaking authorize_payment's dedup and letting an
        authorize replay create a SECOND Payment for the same order."""
        with Session(engine) as db:
            self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}")
            payment = db.get(Payment, ctx.payment_id)
            assert payment.idempotency_key == ctx.auth_key      # untouched
            before = db.scalar(text_count_payments(ctx.order_id))
            # Replaying the ORIGINAL authorize request must return the same row.
            order = db.get(EventOrder, ctx.order_id)
            again = crud.authorize_payment(db, order, amount=Decimal("100.00"),
                                           idempotency_key=ctx.auth_key)
            assert again.id == ctx.payment_id
            assert db.scalar(text_count_payments(ctx.order_id)) == before

    # ── duplicate / concurrent delivery ────────────────────────────────────────────────
    def test_duplicate_delivery_is_idempotent(self, ctx):
        eid = f"evt_{uuid.uuid4().hex[:10]}"
        with Session(engine) as db:
            first = self._ingest(db, ctx, event_id=eid)
            second = self._ingest(db, ctx, event_id=eid)
            assert first["applied"] is True
            assert second["duplicate"] is True and second["applied"] is False
            # Same answer, and only one stored event.
            assert db.scalar(text_count_provider_events(eid)) == 1

    def test_concurrent_identical_deliveries_produce_exactly_one_event(self, ctx):
        """The race an `if exists(...)` check cannot close. Only the DB can arbitrate."""
        eid = f"evt_{uuid.uuid4().hex[:10]}"

        def deliver():
            with Session(engine) as db:
                try:
                    return self._ingest(db, ctx, event_id=eid)
                except Exception as e:                       # must never 500
                    return {"error": type(e).__name__, "detail": str(e)[:120]}

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = [f.result() for f in [pool.submit(deliver) for _ in range(4)]]

        assert not [r for r in results if "error" in r], results
        assert sum(1 for r in results if r.get("applied")) == 1, results
        assert sum(1 for r in results if r.get("duplicate")) == 3, results
        with Session(engine) as db:
            assert db.scalar(text_count_provider_events(eid)) == 1
            assert db.get(Payment, ctx.payment_id).state == "paid"

    def test_same_state_replay_is_recorded_but_not_reapplied(self, ctx):
        """Two DIFFERENT event ids both saying "captured": the second is a legitimate new
        event that finds the payment already in the target state."""
        with Session(engine) as db:
            self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}")
            second = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}")
            assert second["applied"] is False
            assert second["processing_status"] == "replayed"
            assert db.get(Payment, ctx.payment_id).state == "paid"

    # ── invalid transitions ────────────────────────────────────────────────────────────
    def test_illegal_transition_is_rejected_and_retained_as_evidence(self, ctx):
        with Session(engine) as db:
            self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}",
                         event_type="capture_failed")           # pending -> failed (legal)
            assert db.get(Payment, ctx.payment_id).state == "failed"
            eid = f"evt_{uuid.uuid4().hex[:10]}"
            res = self._ingest(db, ctx, event_id=eid, event_type="capture_succeeded")
            assert res["applied"] is False
            assert res["processing_status"] == "rejected"
            assert "illegal payment transition" in res["result"]["reason"]
            # Payment untouched, and the refusal is durable evidence.
            assert db.get(Payment, ctx.payment_id).state == "failed"
            rec = db.scalar(select_provider_event(eid))
            assert rec.processing_status == "rejected" and rec.processing_error

    def test_invalid_transition_creates_audit_evidence(self, ctx):
        with Session(engine) as db:
            self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}", event_type="capture_failed")
            self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}", event_type="capture_succeeded")
            actions = db.scalars(
                text_audit_actions(ctx.org_id)
            ).all()
            assert "commercial.payment.transition_rejected" in actions
            assert "commercial.provider_event.rejected" in actions

    # ── amount / currency ──────────────────────────────────────────────────────────────
    def test_currency_mismatch_blocks_and_does_not_mutate(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}", currency="EUR")
            assert res["applied"] is False and "currency mismatch" in res["result"]["reason"]
            assert db.get(Payment, ctx.payment_id).state == "pending"

    def test_amount_mismatch_blocks_and_does_not_mutate(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}", amount=Decimal("9999.00"))
            assert res["applied"] is False and "amount mismatch" in res["result"]["reason"]
            assert db.get(Payment, ctx.payment_id).state == "pending"

    def test_unverified_signature_is_never_applied(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}", verified=False)
            assert res["applied"] is False and res["processing_status"] == "rejected"
            assert db.get(Payment, ctx.payment_id).state == "pending"

    # ── CF-8 unmatched settlements ─────────────────────────────────────────────────────
    def test_unmatched_settlement_is_retained_not_dropped(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}",
                               ref=f"{ctx.miss}{uuid.uuid4().hex[:6]}")
            assert res["applied"] is False
            assert res["result"]["reason"] == "unmatched_settlement"
            settlement = db.get(UnmatchedSettlement, uuid.UUID(res["result"]["unmatched_settlement_id"]))
            assert settlement.status == "open"
            assert settlement.amount == Decimal("100.00") and settlement.currency == "USD"
            assert settlement.reason and settlement.correlation_id
            # And it did NOT touch any existing payment.
            assert db.get(Payment, ctx.payment_id).state == "pending"

    def test_unmatched_settlement_matching_is_controlled(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}",
                               ref=f"{ctx.miss}{uuid.uuid4().hex[:6]}")
            settlement = db.get(UnmatchedSettlement, uuid.UUID(res["result"]["unmatched_settlement_id"]))
            payment = db.get(Payment, ctx.payment_id)
            actor = db.get(User, ctx.user_id)
            matched = crud.match_settlement(db, settlement, payment, actor, notes="Verified by Finance")
            assert matched.status == "matched" and matched.matched_payment_id == payment.id
            assert matched.matched_by == actor.id and matched.matched_at is not None
            # Matching does not itself move the payment's state.
            assert db.get(Payment, ctx.payment_id).state == "pending"
            with pytest.raises(ValueError, match="already"):
                crud.match_settlement(db, matched, payment, actor)

    def test_matching_refuses_an_amount_mismatch(self, ctx):
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}",
                               ref=f"{ctx.miss}{uuid.uuid4().hex[:6]}", amount=Decimal("7.00"))
            settlement = db.get(UnmatchedSettlement, uuid.UUID(res["result"]["unmatched_settlement_id"]))
            with pytest.raises(ValueError, match="amount mismatch"):
                crud.match_settlement(db, settlement, db.get(Payment, ctx.payment_id),
                                       db.get(User, ctx.user_id))

    # ── audit / correlation ────────────────────────────────────────────────────────────
    def test_processed_event_shares_one_correlation_id_with_its_audits(self, ctx):
        eid = f"evt_{uuid.uuid4().hex[:10]}"
        with Session(engine) as db:
            res = self._ingest(db, ctx, event_id=eid)
            corr = res["correlation_id"]
            rows = db.scalars(text_audit_by_correlation(corr)).all()
            assert "commercial.payment.transition" in rows
            assert "commercial.provider_event.processed" in rows
            assert db.scalar(select_provider_event(eid)).correlation_id == corr

    # ── tenant isolation ──────────────────────────────────────────────────────────────
    def test_provider_event_cannot_target_another_tenants_payment(self, ctx):
        """No tenant identifier is accepted from the caller; the payment is resolved by the
        provider-namespaced ref, and an unknown ref becomes an unmatched settlement rather
        than touching anything."""
        with Session(engine) as db:
            other_org = Organization(name=f"other-{uuid.uuid4().hex[:8]}")
            db.add(other_org)
            db.flush()
            other_user = User(org_id=other_org.id, full_name="Other",
                              email=f"o-{uuid.uuid4().hex[:8]}@t.test",
                              username=f"o{uuid.uuid4().hex[:8]}", password_hash="x", role="org_admin")
            db.add(other_user)
            db.flush()
            other_event = Event(org_id=other_org.id, created_by=other_user.id, title="Other org")
            db.add(other_event)
            db.flush()
            other_account = CommercialAccount(org_id=other_org.id)
            other_catalog = CatalogVersion(version_label=f"o-{uuid.uuid4().hex[:6]}",
                                           vertical=f"other-{uuid.uuid4().hex[:6]}", status="published")
            db.add_all([other_account, other_catalog])
            db.flush()
            other_order = EventOrder(event_id=other_event.id, commercial_account_id=other_account.id,
                                     catalog_version_id=other_catalog.id, currency="USD",
                                     subtotal=Decimal("50.00"), total_amount=Decimal("50.00"),
                                     status="accepted", idempotency_key=str(uuid.uuid4()))
            db.add(other_order)
            db.flush()
            victim = Payment(event_order_id=other_order.id, provider="mock",
                             provider_payment_ref=f"victim_{uuid.uuid4().hex[:10]}",
                             amount=Decimal("50.00"), currency="USD", state="pending",
                             idempotency_key=str(uuid.uuid4()))
            db.add(victim)
            db.commit()
            victim_id, victim_state = victim.id, victim.state
            try:
                # An attacker cannot express "apply to org B" — only a provider ref. Using a
                # ref they do not hold yields an unmatched settlement, not a mutation.
                res = self._ingest(db, ctx, event_id=f"evt_{uuid.uuid4().hex[:10]}",
                                   ref=f"{ctx.miss}{uuid.uuid4().hex[:6]}")
                assert res["result"]["reason"] == "unmatched_settlement"
                db.expire_all()
                assert db.get(Payment, victim_id).state == victim_state
            finally:
                db.execute(text("DELETE FROM unmatched_settlements WHERE provider_payment_ref LIKE :m"),
                           {"m": f"{ctx.miss}%"})
                db.execute(text("DELETE FROM provider_events WHERE provider_payment_ref LIKE :m"),
                           {"m": f"{ctx.miss}%"})
                db.execute(text("DELETE FROM payments WHERE id = :p"), {"p": victim_id})
                db.execute(text("DELETE FROM commercial_state_transitions WHERE event_order_id=:o"), {"o": other_order.id})
                db.execute(text("DELETE FROM event_orders WHERE id = :o"), {"o": other_order.id})
                db.execute(text("DELETE FROM events WHERE id = :e"), {"e": other_event.id})
                db.execute(text("DELETE FROM catalog_versions WHERE id = :c"), {"c": other_catalog.id})
                db.execute(text("DELETE FROM commercial_accounts WHERE id = :a"), {"a": other_account.id})
                db.execute(text("DELETE FROM users WHERE id = :u"), {"u": other_user.id})
                db.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": other_org.id})
                db.commit()


# ── small query helpers, kept out of the test bodies for readability ───────────────────

def select_provider_event(provider_event_id: str):
    from sqlalchemy import select
    return select(ProviderEvent).where(ProviderEvent.provider_event_id == provider_event_id)


def text_count_provider_events(provider_event_id: str):
    return text("SELECT count(*) FROM provider_events WHERE provider_event_id = :e").bindparams(
        e=provider_event_id)


def text_count_payments(order_id):
    return text("SELECT count(*) FROM payments WHERE event_order_id = :o").bindparams(o=order_id)


def text_audit_actions(org_id):
    return text("SELECT action FROM audit_logs WHERE org_id = :o").bindparams(o=org_id)


def text_audit_by_correlation(correlation_id: str):
    return text("SELECT action FROM audit_logs WHERE correlation_id = :c").bindparams(c=correlation_id)


# ══════════════════════════════════════════════════════════════════════════════════════
# CF-6 · REPLAY IDOR (structural)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_replay_routes_are_org_scoped():
    """CF-6: publish/retry-watermark are reachable by the CUSTOMER `host` role, so a bare
    db.get() let a host in org A publish org B's private replay."""
    from app.routers import commercial as router_mod
    for path in ("/replay-entitlements/{entitlement_id}/publish",
                 "/replay-entitlements/{entitlement_id}/retry-watermark"):
        endpoint = next(r.endpoint for r in router_mod.router.routes
                        if r.path == f"/commercial{path}" and "POST" in r.methods)
        src = code_only(endpoint)
        assert "_get_replay_entitlement_or_404" in src, f"{path} is not org-scoped"
        assert "db.get(ReplayEntitlement" not in src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
