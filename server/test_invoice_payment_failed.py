"""E4 — the `invoice.payment_failed` dunning TRIGGER, and nothing more.

WHAT THIS CLOSES. `invoice.payment_failed` reached Ledger 2, matched no Payment, and was filed
as `unhandled_stripe_event_type` — so a failed subscription renewal left the local row reading
`active` with nothing anywhere recording that the money had not arrived. `past_due` is the
Section 12 state for that fact; it already existed and was already legal from `active`, and
nothing ever entered it from an invoice.

WHAT THIS DELIBERATELY DOES NOT DO. No grace period, no RESTRICTED state, no
CANCELED_FOR_NONPAYMENT, no 30-day timer, no suspension, no cancellation, and no change to
entitlement — `past_due` remains in SUBSCRIPTION_ENTITLED_STATES because past-due entitlement is
approved dunning policy and is undefined. TestNoDunningPolicyWasInvented at the bottom pins all
of that, so a later change that quietly adds a policy has to break a test first.

NO STRIPE NETWORK CALLS. Events are constructed locally and handed to the real handler.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app.crud import admin as admin_crud
from app.db import engine
from app.models import AuditLog, Organization, Plan, Subscription
from app.models.subscription import SUBSCRIPTION_ENTITLED_STATES, SUBSCRIPTION_STATES
from app.routers import commercial as commercial_router
from app.services import payments_stripe_events as stripe_events


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


def _invoice_event(event_id, sub_ref, *, customer="cus_e4", invoice="in_e4",
                   billing_reason="subscription_cycle", attempt_count=1, nested=False,
                   etype="invoice.payment_failed"):
    obj = {
        "id": invoice, "object": "invoice", "customer": customer,
        "billing_reason": billing_reason, "attempt_count": attempt_count,
        "next_payment_attempt": int((datetime.now(timezone.utc)
                                     + timedelta(days=3)).timestamp()),
    }
    if sub_ref is not None:
        if nested:
            obj["parent"] = {"subscription_details": {"subscription": sub_ref}}
        else:
            obj["subscription"] = sub_ref
    return {"id": event_id, "object": "event", "type": etype, "livemode": False,
            "created": int(datetime.now(timezone.utc).timestamp()),
            "data": {"object": obj}}


# ══════════════════════════════════════════════════════════════════════════════════════
# Extraction — no database
# ══════════════════════════════════════════════════════════════════════════════════════

class TestFactExtraction:
    def test_the_classic_payload_shape_resolves(self):
        f = stripe_events.invoice_payment_failure_facts(_invoice_event("evt", "sub_abc"))
        assert f["stripe_subscription_id"] == "sub_abc"
        assert f["state"] == "past_due"
        assert f["stripe_customer_id"] == "cus_e4"

    def test_the_2026_07_29_nested_shape_also_resolves(self):
        """Stripe relocated this field, the same way `current_period_end` moved onto
        items.data[]. Reading only one shape would make a real renewal failure resolve to no
        subscription at all."""
        f = stripe_events.invoice_payment_failure_facts(
            _invoice_event("evt", "sub_nested", nested=True))
        assert f["stripe_subscription_id"] == "sub_nested"

    def test_a_one_off_invoice_is_not_a_ledger_1_event(self):
        """No subscription named -> it is a Ledger 2 charge and must fall through."""
        assert stripe_events.invoice_payment_failure_facts(
            _invoice_event("evt", None)) is None

    def test_a_non_subscription_reference_is_refused(self):
        assert stripe_events.invoice_payment_failure_facts(
            _invoice_event("evt", "in_not_a_subscription")) is None

    @pytest.mark.parametrize("etype", ["invoice.paid", "invoice.payment_succeeded",
                                        "invoice.created", "customer.subscription.updated"])
    def test_no_other_invoice_event_is_consumed(self, etype):
        """Only the FAILURE is a Ledger 1 trigger. Consuming invoice.paid would make an
        invoice, rather than the subscription event, an activation signal."""
        assert stripe_events.invoice_payment_failure_facts(
            _invoice_event("evt", "sub_abc", etype=etype)) is None

    def test_evidence_is_carried_but_the_state_is_fixed(self):
        f = stripe_events.invoice_payment_failure_facts(
            _invoice_event("evt", "sub_abc", billing_reason="subscription_create",
                           attempt_count=4))
        assert f["billing_reason"] == "subscription_create" and f["attempt_count"] == 4
        assert f["state"] == "past_due", \
            "the reported state must not vary with billing_reason — the graph decides"


# ══════════════════════════════════════════════════════════════════════════════════════
# Behaviour against real Postgres
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestInvoiceFailureTrigger:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            tag = uuid.uuid4().hex[:8]
            plan = Plan(name=f"E4-{tag}", slug=f"e4-{tag}", is_active=True,
                        max_users=5, max_storage_gb=50)
            db.add(plan); db.flush()
            made = []

            def org(status="active", sub_ref=None, **kw):
                o = Organization(name=f"e4-{tag}-{len(made)}")
                db.add(o); db.flush()
                s = Subscription(org_id=o.id, plan_id=plan.id, status=status, seats=1,
                                 stripe_subscription_id=sub_ref, **kw)
                db.add(s); db.flush()
                made.append((o, s))
                return o, s

            db.commit()
            yield db, SimpleNamespace(tag=tag, plan=plan, org=org, made=made)
            for o, _ in made:
                db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM subscriptions WHERE org_id=:o"), {"o": o.id})
                db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": o.id})
            db.execute(text("DELETE FROM plans WHERE id=:p"), {"p": plan.id})
            db.execute(text("DELETE FROM provider_events WHERE correlation_id='e4-test'"))
            db.commit()

    def _fire(self, db, event):
        return commercial_router._handle_subscription_event(
            db, event, correlation_id="e4-test", raw_body=json.dumps(event).encode())

    def _actions(self, db, org_id):
        return [a.action for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id)).all()]

    # ── the trigger ─────────────────────────────────────────────────────────────────────
    def test_active_plus_invoice_payment_failed_becomes_past_due(self, ctx):
        db, ids = ctx
        ref = f"sub_active_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_pf_{ids.tag}", ref))
        db.refresh(sub)
        assert sub.status == "past_due"
        assert res["applied"] is True and res["reason"] == "invoice_payment_failed"

    def test_the_transition_is_audited(self, ctx):
        db, ids = ctx
        ref = f"sub_aud_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        self._fire(db, _invoice_event(f"evt_aud_{ids.tag}", ref))
        rows = {a.action: a for a in db.scalars(
            select(AuditLog).where(AuditLog.org_id == org.id)).all()}
        assert "subscription.transition" in rows
        t = rows["subscription.transition"].meta or {}
        assert t["from"] == "active" and t["to"] == "past_due"
        assert t["reason"] == "stripe:invoice.payment_failed"

    def test_the_invoice_evidence_is_recorded_separately(self, ctx):
        db, ids = ctx
        ref = f"sub_ev_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        self._fire(db, _invoice_event(f"evt_ev_{ids.tag}", ref, invoice="in_specific",
                                       attempt_count=3))
        row = [a for a in db.scalars(select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "subscription.invoice_payment_failed")).all()]
        assert len(row) == 1
        m = row[0].meta or {}
        assert m["invoice_ref"] == "in_specific" and m["attempt_count"] == 3
        assert m["transition_applied"] is True
        assert "undefined" in m["entitlement"], "the open policy must be stated in the record"
        assert "no grace" in m["dunning"]

    def test_the_nested_payload_shape_works_end_to_end(self, ctx):
        db, ids = ctx
        ref = f"sub_nest_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        self._fire(db, _invoice_event(f"evt_nest_{ids.tag}", ref, nested=True))
        db.refresh(sub)
        assert sub.status == "past_due"

    # ── idempotency ─────────────────────────────────────────────────────────────────────
    def test_replaying_the_same_event_is_idempotent(self, ctx):
        db, ids = ctx
        ref = f"sub_dup_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        ev = _invoice_event(f"evt_dup_{ids.tag}", ref)
        first = self._fire(db, ev)
        second = self._fire(db, ev)
        db.refresh(sub)
        assert first["applied"] is True
        assert second.get("duplicate") is True, "the stored outcome must be returned"
        assert sub.status == "past_due"
        transitions = [a for a in db.scalars(select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "subscription.transition")).all()]
        assert len(transitions) == 1, "a redelivery must not add a second transition row"

    def test_a_second_distinct_failure_on_the_same_subscription_adds_no_transition(self, ctx):
        """Stripe retries a failing invoice several times, each a NEW event id. The state is
        already past_due, and re-asserting a state is a Section 12 no-op — so the retries leave
        evidence without accumulating transitions."""
        db, ids = ctx
        ref = f"sub_retry_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        self._fire(db, _invoice_event(f"evt_r1_{ids.tag}", ref, attempt_count=1))
        res2 = self._fire(db, _invoice_event(f"evt_r2_{ids.tag}", ref, attempt_count=2))
        db.refresh(sub)
        assert sub.status == "past_due"
        assert res2["applied"] is False and res2["error"] is None
        transitions = [a for a in db.scalars(select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "subscription.transition")).all()]
        assert len(transitions) == 1
        evidence = [a for a in db.scalars(select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "subscription.invoice_payment_failed")).all()]
        assert len(evidence) == 2, "each retry is still recorded as evidence"

    def test_already_past_due_is_a_clean_no_op(self, ctx):
        db, ids = ctx
        ref = f"sub_pd_{ids.tag}"
        org, sub = ids.org("past_due", sub_ref=ref)
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_pd2_{ids.tag}", ref))
        db.refresh(sub)
        assert sub.status == "past_due"
        assert res["applied"] is False and res["error"] is None
        assert "subscription.transition" not in self._actions(db, org.id)

    # ── illegal sources are refused, not forced ─────────────────────────────────────────
    @pytest.mark.parametrize("state", ["trialing", "conversion_pending", "suspended",
                                        "canceled", "closed", "trial_expired"])
    def test_an_illegal_source_state_is_refused_and_audited(self, ctx, state):
        """Only `active -> past_due` is legal. This is what makes an initial-purchase failure
        safe without the handler knowing anything about billing reasons."""
        db, ids = ctx
        ref = f"sub_{state}_{ids.tag}"
        org, sub = ids.org(state, sub_ref=ref)
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_{state}_{ids.tag}", ref))
        db.refresh(sub)
        assert sub.status == state, "a refused transition must not be applied"
        assert res["applied"] is False and res["error"]
        assert "subscription.transition_rejected" in self._actions(db, org.id)
        assert "subscription.transition" not in self._actions(db, org.id)

    def test_an_initial_purchase_failure_does_not_strand_a_converting_tenant(self, ctx):
        """billing_reason=subscription_create on a conversion_pending row: refused, recorded,
        and the tenant is left mid-conversion rather than pushed into dunning."""
        db, ids = ctx
        ref = f"sub_create_{ids.tag}"
        org, sub = ids.org("conversion_pending", sub_ref=ref)
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_create_{ids.tag}", ref,
                                             billing_reason="subscription_create"))
        db.refresh(sub)
        assert sub.status == "conversion_pending"
        assert res["applied"] is False and res["error"]

    # ── unmatched / fail closed ─────────────────────────────────────────────────────────
    def test_an_unmatched_subscription_is_never_guessed_onto_a_tenant(self, ctx):
        db, ids = ctx
        org, sub = ids.org("active", sub_ref=f"sub_known_{ids.tag}")
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_unk_{ids.tag}",
                                             f"sub_unknown_{ids.tag}"))
        db.refresh(sub)
        assert res["applied"] is False
        assert res["reason"] == "no_matching_subscription"
        assert sub.status == "active", "an unmatched event must touch nobody"

    def test_a_one_off_invoice_falls_through_to_ledger_2(self, ctx):
        """Returning None is what lets the Ledger 2 path see it. Consuming it here would put a
        one-off charge inside a subscription's lifecycle."""
        db, ids = ctx
        assert self._fire(db, _invoice_event(f"evt_oneoff_{ids.tag}", None)) is None

    @pytest.mark.parametrize("etype", ["invoice.paid", "invoice.payment_succeeded",
                                        "invoice.created", "invoice.finalized"])
    def test_other_invoice_events_still_fall_through(self, ctx, etype):
        db, ids = ctx
        org, sub = ids.org("active", sub_ref=f"sub_other_{ids.tag}")
        db.commit()
        assert self._fire(db, _invoice_event(f"evt_o_{etype}_{ids.tag}",
                                             f"sub_other_{ids.tag}", etype=etype)) is None
        db.refresh(sub)
        assert sub.status == "active", "only the FAILURE is a Ledger 1 trigger"

    # ── tenant isolation ────────────────────────────────────────────────────────────────
    def test_the_event_reaches_only_the_owning_organization(self, ctx):
        db, ids = ctx
        ref_a = f"sub_a_{ids.tag}"
        org_a, sub_a = ids.org("active", sub_ref=ref_a)
        org_b, sub_b = ids.org("active", sub_ref=f"sub_b_{ids.tag}")
        db.commit()
        self._fire(db, _invoice_event(f"evt_iso_{ids.tag}", ref_a))
        db.refresh(sub_a); db.refresh(sub_b)
        assert sub_a.status == "past_due"
        assert sub_b.status == "active", "the other tenant must be untouched"
        assert "subscription.transition" not in self._actions(db, org_b.id)

    def test_the_stripe_customer_is_never_used_to_locate_a_tenant(self, ctx):
        """Two organizations can share a Stripe customer id; it is indexed but not unique.
        Resolving on it could apply one tenant's dunning to another."""
        db, ids = ctx
        shared = f"cus_shared_{ids.tag}"
        org_a, sub_a = ids.org("active", sub_ref=f"sub_ca_{ids.tag}",
                               stripe_customer_id=shared)
        org_b, sub_b = ids.org("active", sub_ref=f"sub_cb_{ids.tag}",
                               stripe_customer_id=shared)
        db.commit()
        res = self._fire(db, _invoice_event(f"evt_cus_{ids.tag}", f"sub_missing_{ids.tag}",
                                             customer=shared))
        db.refresh(sub_a); db.refresh(sub_b)
        assert res["reason"] == "no_matching_subscription"
        assert sub_a.status == "active" and sub_b.status == "active"

    # ── entitlement is unchanged ────────────────────────────────────────────────────────
    def test_entitlement_and_plan_are_untouched(self, ctx):
        from app.services import org as org_svc
        db, ids = ctx
        ref = f"sub_ent_{ids.tag}"
        org, sub = ids.org("active", sub_ref=ref)
        db.commit()
        self._fire(db, _invoice_event(f"evt_ent_{ids.tag}", ref))
        db.refresh(sub)
        assert sub.plan_id == ids.plan.id, "no plan change"
        assert sub.seats == 1
        assert sub.cancelled_at is None, "a failed payment is not a cancellation"
        assert org_svc.enforcement_plan(db, org.id).id == ids.plan.id
        assert "past_due" in SUBSCRIPTION_ENTITLED_STATES, \
            "entitlement behaviour must be exactly as it was"

    def test_no_period_boundary_is_moved(self, ctx):
        """A failed renewal has not moved the period end, and that boundary anchors the
        approved effective date for a scheduled plan change."""
        db, ids = ctx
        ref = f"sub_per_{ids.tag}"
        boundary = datetime.now(timezone.utc) + timedelta(days=9)
        org, sub = ids.org("active", sub_ref=ref, current_period_end=boundary)
        db.commit()
        self._fire(db, _invoice_event(f"evt_per_{ids.tag}", ref))
        db.refresh(sub)
        assert abs((sub.current_period_end - boundary).total_seconds()) < 2


# ══════════════════════════════════════════════════════════════════════════════════════
# Structural — no dunning policy was invented
# ══════════════════════════════════════════════════════════════════════════════════════

class TestNoDunningPolicyWasInvented:
    def test_no_new_commercial_state_was_added(self):
        for forbidden in ("grace", "restricted", "canceled_for_nonpayment",
                          "cancelled_for_nonpayment"):
            assert forbidden not in SUBSCRIPTION_STATES, \
                f"{forbidden!r} is a dunning policy state and is not authorized"

    def test_past_due_entitlement_is_unchanged(self):
        assert "past_due" in SUBSCRIPTION_ENTITLED_STATES

    def test_the_handler_applies_no_restriction_suspension_or_cancellation(self):
        """Checked against CODE, not prose. The branch's audit metadata deliberately contains
        the words 'restriction', 'suspension' and 'cancellation' in a sentence stating that none
        is applied — so string literals are stripped before the check, or the record of what is
        NOT done would itself trip the assertion."""
        import ast
        src = code_only(commercial_router._handle_subscription_event)
        block = src.split("invoice_failure is not None", 1)[1].split("if checkout is not None", 1)[0]

        class StripStrings(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    return ast.copy_location(ast.Constant(value=""), node)
                return node

        # Parse the branch in isolation by wrapping it in a function body.
        wrapped = "def _b():\n" + "\n".join(
            "    " + ln for ln in ("if True" + block).splitlines())
        tree = StripStrings().visit(ast.parse(wrapped))
        code = ast.unparse(tree).lower()
        for forbidden in ("suspend", "restrict", "cancel", "grace", "nonpayment",
                          "entitle", "max_storage", "max_users", "plan_id ="):
            assert forbidden not in code, \
                f"the invoice-failure branch must not {forbidden!r} — that is undecided policy"

    def test_the_reported_outcome_is_the_transition_not_the_evidence_claim(self):
        """`record_provider_event_evidence` returns its own `applied: False` — it records
        evidence and applies nothing. Spreading that claim LAST would overwrite the real
        transition outcome with a constant, so a genuine active -> past_due move would report
        `applied: False`. The claim must therefore be spread first."""
        src = code_only(commercial_router._handle_subscription_event)
        block = src.split("invoice_failure is not None", 1)[1].split("if checkout is not None", 1)[0]
        ret = [ln for ln in block.splitlines() if "invoice_payment_failed" in ln
               and "return" in block[:block.index(ln)] + ln]
        assert "**claim, " in block or "**claim," in block, \
            "the claim must be spread FIRST so the explicit outcome keys win"
        assert not block.rstrip().rstrip("}").endswith("**claim"), \
            "the claim must not be spread last — it would overwrite `applied`"

    def test_the_trigger_adds_no_timer(self):
        from app.services import maintenance
        assert not any(k in j.__name__ for j in maintenance.JOBS
                       for k in ("dunning", "grace", "restrict", "nonpayment")), \
            "no dunning timer may exist until the policy is approved"

    def test_the_reported_state_is_the_existing_past_due_only(self):
        src = code_only(stripe_events.invoice_payment_failure_facts)
        assert "'past_due'" in src or '"past_due"' in src
        for forbidden in ("grace", "restricted", "nonpayment"):
            assert forbidden not in src

    def test_the_state_machine_still_gates_the_transition(self):
        src = code_only(commercial_router._handle_subscription_event)
        assert "apply_subscription_provider_event" in src, \
            "the transition must go through the Section 12 machine, never a direct assignment"
