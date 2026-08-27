"""Phase 5 commercial-completion tests (ZST-LE-COM-001 Sections 9, 10, 13, 25, 28).

Covers the areas the previous phases left untested — every defect Phase 5 fixed sat in a
function with zero test references:

  * the derived commercial lifecycle and its append-only transition log (Section 28)
  * cancellation: double-cancel, the refundable-cash cap, refunds bound to real payments
  * refund netting: an executed refund un-satisfies what it paid for
  * change orders: catalog-backed line operations, computed deltas, discount approval
  * activate_order's capacity/financial gates
  * reschedule: capacity release, preserved history, live-event refusal
  * write-off: the RBAC action that had no code path
  * risk tier / service profile / managed-only / Assured Event enforcement
  * the extended RBAC matrix and its Finance/Operations separation
  * audience-commerce (Ledger 3) separation

`needs_db` tests exercise real Postgres. The rest are pure logic and run offline.
"""
import ast
import inspect
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from _testsupport import code_only
from app import security
from app.crud import commercial as crud
from app.db import engine
from app.models import (
    CancellationPolicy, CapacityPool, CapacityReservation, CatalogLine, CatalogVersion,
    ChangeOrder, CommercialAccount, CommercialException, CommercialStateTransition,
    COMMERCIAL_LIFECYCLE_STATES, COMMERCIAL_LIFECYCLE_TRANSITIONS, Event, EventOrder,
    EventOrderLine, EventReschedule, Organization, Payment, PaymentDispute,
    PaymentSchedule, RefundCredit,
    SellerLegalEntity, ServiceProfile, User,
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
NOW = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════════════
# LIFECYCLE GRAPH (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_every_lifecycle_transition_targets_a_real_state():
    for state, targets in COMMERCIAL_LIFECYCLE_TRANSITIONS.items():
        assert state in COMMERCIAL_LIFECYCLE_STATES, f"unknown source state {state}"
        for t in targets:
            assert t in COMMERCIAL_LIFECYCLE_STATES, f"{state} -> unknown {t}"


def test_every_state_appears_in_the_graph():
    for state in COMMERCIAL_LIFECYCLE_STATES:
        assert state in COMMERCIAL_LIFECYCLE_TRANSITIONS, f"{state} has no outgoing entry"


def test_completed_and_canceled_are_terminal():
    assert COMMERCIAL_LIFECYCLE_TRANSITIONS["completed"] == ()
    assert COMMERCIAL_LIFECYCLE_TRANSITIONS["canceled"] == ()


def test_live_is_only_reachable_from_ready():
    """The go-live gate's whole purpose: an event cannot start delivering without clearing
    readiness. Any other state offering a direct edge to `live` would be a bypass."""
    sources = [s for s, targets in COMMERCIAL_LIFECYCLE_TRANSITIONS.items() if "live" in targets]
    assert sources == ["ready"], f"live reachable from {sources}"


def test_confirmed_is_not_reachable_from_draft_or_quoted():
    """Nothing may skip acceptance. CONFIRMED means an accepted order that is paid and
    resourced, so a draft or quoted event reaching it directly would be a skipped gate."""
    for state in ("draft", "quoted"):
        assert "confirmed" not in COMMERCIAL_LIFECYCLE_TRANSITIONS[state]
        assert "ready" not in COMMERCIAL_LIFECYCLE_TRANSITIONS[state]


@pytest.mark.parametrize("previous,new", [
    ("draft", "live"),          # skips acceptance, payment, capacity and readiness
    ("quoted", "completed"),    # a quote cannot be a delivered engagement
    ("canceled", "ready"),      # terminal
    ("draft", "confirmed"),
    ("draft", "ready"),
    ("quoted", "live"),
    ("completed", "ready"),
    ("canceled", "live"),
])
def test_forbidden_lifecycle_transitions_are_rejected(previous, new):
    """The moves the standard names explicitly, plus their neighbours. `lifecycle_transition_
    allowed` is the graph's opinion; the go-live and activation gates are the enforcement —
    both are tested (see the DB class)."""
    assert not crud.lifecycle_transition_allowed(previous, new)


@pytest.mark.parametrize("previous,new", [
    ("draft", "quoted"),
    ("quoted", "order_accepted"),
    ("order_accepted", "financial_hold"),
    ("financial_hold", "capacity_held"),
    ("capacity_held", "confirmed"),
    ("confirmed", "ready"),
    ("ready", "live"),
    ("live", "completed"),
])
def test_the_canonical_path_is_permitted_end_to_end(previous, new):
    assert crud.lifecycle_transition_allowed(previous, new)


def test_a_first_observation_is_not_a_transition():
    """A NULL previous state means the lifecycle has never been computed for this order — the
    first observation must not be recorded as an illegal jump from nowhere."""
    assert crud.lifecycle_transition_allowed(None, "confirmed")


def test_same_state_reobservation_is_allowed():
    assert crud.lifecycle_transition_allowed("confirmed", "confirmed")


# ══════════════════════════════════════════════════════════════════════════════════════
# STRIPE DISPUTE TRANSLATION (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════
# Dispute events were evidence-only, so a real chargeback left the ledger reading `paid`.
# These cover the translation half; the DB class covers the case record and payment transition.

def _dispute_event(status, *, etype="charge.dispute.created", amount=144000,
                    currency="usd", withdrawn=False, dispute_id="dp_1", intent="pi_1"):
    obj = {"id": dispute_id, "object": "dispute", "amount": amount, "currency": currency,
           "status": status, "reason": "fraudulent", "payment_intent": intent,
           "evidence_details": {"due_by": 1770600000},
           "balance_transactions": [{"amount": -amount}] if withdrawn else []}
    return {"id": f"evt_{dispute_id}_{status}", "type": etype, "created": 1770000000,
            "data": {"object": obj}}


@pytest.mark.parametrize("stripe_status,expected", [
    ("needs_response", "evidence_required"),
    ("warning_needs_response", "evidence_required"),
    ("under_review", "evidence_submitted"),
    ("warning_under_review", "evidence_submitted"),
    ("won", "won"),
    ("lost", "lost"),
    ("warning_closed", "withdrawn"),
])
def test_stripe_dispute_status_maps_to_our_vocabulary(stripe_status, expected):
    from app.services import payments_stripe_events as se
    from app.models import DISPUTE_STATES

    t = se.translate(_dispute_event(stripe_status))
    assert t.is_dispute
    assert t.dispute["status"] == expected
    assert expected in DISPUTE_STATES


def test_a_dispute_event_is_never_a_financial_instruction():
    """The payment consequence is derived from the CASE status by the commercial layer, not
    from the generic event map — a dispute is a case with an outcome, not a state instruction."""
    from app.services import payments_stripe_events as se

    t = se.translate(_dispute_event("needs_response"))
    assert t.is_financial is False
    assert t.generic_event_type is None


def test_dispute_amount_is_converted_out_of_minor_units():
    from app.services import payments_stripe_events as se

    t = se.translate(_dispute_event("needs_response", amount=144000, currency="usd"))
    assert t.dispute["amount"] == Decimal("1440.00")
    assert t.dispute["currency"] == "USD"


def test_a_reserve_is_only_reported_once_funds_are_withheld():
    """Before the network pulls the money the dispute is open but nothing has moved — a
    reserve of the full amount at that point would overstate the exposure."""
    from app.services import payments_stripe_events as se

    open_case = se.translate(_dispute_event("needs_response", withdrawn=False))
    assert open_case.dispute["reserve_amount"] is None
    withheld = se.translate(_dispute_event("needs_response", withdrawn=True))
    assert withheld.dispute["reserve_amount"] == Decimal("1440.00")


def test_a_dispute_without_an_identifier_stays_evidence():
    """No dispute id means no case identity to be idempotent against, so it must not be
    attached to a payment."""
    from app.services import payments_stripe_events as se

    event = _dispute_event("needs_response")
    del event["data"]["object"]["id"]
    t = se.translate(event)
    assert not t.is_dispute
    assert t.evidence_reason == "dispute_event_without_identifier"
    assert t.follow_up_required


def test_an_unmapped_dispute_status_is_not_guessed():
    from app.services import payments_stripe_events as se

    t = se.translate(_dispute_event("some_future_stripe_status"))
    assert t.is_dispute                      # still a dispute event
    assert t.dispute["status"] is None       # but no invented case status


def test_every_mapped_dispute_status_has_a_payment_consequence():
    """crud._DISPUTE_PAYMENT_STATE must cover every status the translator can produce, or an
    ingest would KeyError on a real chargeback."""
    from app.services import payments_stripe_events as se

    for case_status in set(se.STRIPE_DISPUTE_STATUS_MAP.values()):
        assert case_status in crud._DISPUTE_PAYMENT_STATE, case_status


def test_only_a_decided_dispute_moves_money():
    """An open dispute contests funds; it does not return them."""
    assert crud._DISPUTE_PAYMENT_STATE["evidence_required"] == "disputed"
    assert crud._DISPUTE_PAYMENT_STATE["evidence_submitted"] == "disputed"
    assert crud._DISPUTE_PAYMENT_STATE["won"] == "paid"
    assert crud._DISPUTE_PAYMENT_STATE["lost"] == "reversed"


def test_disputes_cannot_be_opened_against_stripe():
    """A dispute originates from the cardholder's bank. Returning a fabricated DisputeResult
    would put invented evidence in the ledger."""
    from app.services.payments import ProviderInvalidRequest
    from app.services.payments_stripe import StripePaymentProvider

    provider = StripePaymentProvider(api_key="sk_test_x")
    with pytest.raises(ProviderInvalidRequest, match="cardholder"):
        provider.open_dispute("pi_1", amount=Decimal("10.00"), reason_code="fraudulent")


# ══════════════════════════════════════════════════════════════════════════════════════
# REPLAY RETENTION (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════
# `expires_at` was stored and read by nothing, so a replay past its retention window stayed
# playable forever.

def _entitlement(**kw):
    base = dict(publish_state="published", expires_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_no_expiry_set_means_no_expiry():
    assert not crud.replay_access_expired(_entitlement())


def test_a_future_expiry_is_not_expired():
    assert not crud.replay_access_expired(
        _entitlement(expires_at=NOW + timedelta(days=1)))


def test_a_past_expiry_is_expired():
    assert crud.replay_access_expired(_entitlement(expires_at=NOW - timedelta(seconds=1)))


def test_an_expired_publish_state_is_expired_regardless_of_the_date():
    """Whichever notices first — the live check or the maintenance sweep — access stops, and
    the two can never disagree about a given row."""
    assert crud.replay_access_expired(_entitlement(publish_state="expired"))


def test_a_missing_entitlement_is_not_treated_as_expired():
    """None means "never published", which the watch gate already handles separately. Calling
    it expired here would conflate two different states."""
    assert not crud.replay_access_expired(None)


def test_the_watch_gate_consults_replay_expiry():
    """Structural: the playback gate must call the expiry check. Without this the retention
    window is stored and enforced nowhere."""
    from app.routers import events as events_router

    src = code_only(events_router)
    assert "replay_access_expired" in src


def test_the_retention_sweep_does_not_delete_media():
    """Retention lapsing ends ACCESS, not the record of what was delivered. A sweep that
    deleted the object would destroy a customer's recording on a date nobody confirmed."""
    src = code_only(crud.expire_lapsed_replay_entitlements)
    for destructive in ("db.delete", "delete_object", "remove_object"):
        assert destructive not in src, f"retention sweep performs {destructive}"


def test_lifecycle_state_has_no_write_path():
    """The state is DERIVED — "no state should be manually bypassable" holds because there is
    no setter. Guards against a future endpoint accepting it as input: the request schemas must
    never carry `lifecycle_state`, and only sync_lifecycle may assign the cache column."""
    from app.schemas import commercial as schemas

    for name in dir(schemas):
        obj = getattr(schemas, name)
        fields = getattr(obj, "model_fields", None)
        if not fields or not name.endswith(("Create", "Accept", "Request", "Update")):
            continue
        assert "lifecycle_state" not in fields, f"{name} accepts lifecycle_state as input"

    src = code_only(crud)
    assigns = [n for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(crud))))
               if isinstance(n, ast.Attribute) and n.attr == "lifecycle_state"
               and isinstance(getattr(n, "ctx", None), ast.Store)]
    # Exactly one assignment, inside sync_lifecycle.
    assert len(assigns) == 1, f"{len(assigns)} writers of lifecycle_state; expected only sync_lifecycle"
    assert "def sync_lifecycle" in src


# ══════════════════════════════════════════════════════════════════════════════════════
# RBAC MATRIX (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def _staff(role):
    return SimpleNamespace(role="super_admin", staff_commercial_role=role)


def _customer(role):
    return SimpleNamespace(role=role, staff_commercial_role=None)


def test_unscoped_super_admin_keeps_full_access():
    """Every existing account has staff_commercial_role NULL. Adding matrix columns must not
    narrow them — that would be a silent privilege regression on live accounts."""
    admin = _staff(None)
    for action in security.COMMERCIAL_ACTIONS:
        assert security.commercial_can(admin, action), action


@pytest.mark.parametrize("action", ["finance", "reconcile", "capacity", "readiness", "configure"])
def test_no_customer_role_holds_a_zoiko_side_action(action):
    for role in ("org_admin", "billing_admin", "host", "moderator", "viewer"):
        assert not security.commercial_can(_customer(role), action), f"{role} has {action}"


def test_finance_ops_holds_money_but_not_delivery():
    fin = _staff("finance_ops")
    for allowed in ("finance", "reconcile", "refund_approve", "write_off"):
        assert security.commercial_can(fin, allowed), allowed
    # Separation of duties: Finance must not be able to commit capacity or attest readiness
    # for the event whose money it controls.
    for denied in ("capacity", "readiness", "configure"):
        assert not security.commercial_can(fin, denied), denied


def test_live_ops_holds_delivery_but_not_money():
    ops = _staff("live_ops")
    for allowed in ("capacity", "readiness", "media_access", "change"):
        assert security.commercial_can(ops, allowed), allowed
    for denied in ("finance", "reconcile", "refund_approve", "write_off", "configure"):
        assert not security.commercial_can(ops, denied), denied


@pytest.mark.parametrize("role", ["support", "security"])
def test_support_and_security_hold_nothing(role):
    """Before the split these rows passed require_super_admin on capacity, invoices and
    reconciliation — the same authority as finance_ops."""
    user = _staff(role)
    for action in security.COMMERCIAL_ACTIONS:
        assert not security.commercial_can(user, action), f"{role} has {action}"


def test_configure_is_super_admin_only():
    """Commercial configuration and seller entities are Super Admin's per the doc. No scoped
    staff role may publish a catalog or activate a selling entity."""
    for role in ("sales", "finance_ops", "live_ops", "support", "security"):
        assert not security.commercial_can(_staff(role), "configure"), role


def test_a_new_action_defaults_to_denied():
    """_NO_COMMERCIAL is the base for every role grant, so adding a column to
    COMMERCIAL_ACTIONS cannot silently hand it to anyone."""
    for table in (security._CUSTOMER_COMMERCIAL, security._STAFF_COMMERCIAL):
        for role, grants in table.items():
            missing = set(security.COMMERCIAL_ACTIONS) - set(grants)
            assert not missing, f"{role} is missing explicit entries for {missing}"


# ══════════════════════════════════════════════════════════════════════════════════════
# AUDIENCE COMMERCE / LEDGER 3 SEPARATION (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def test_audience_commerce_is_disabled():
    assert crud.AUDIENCE_COMMERCE_ENABLED is False
    with pytest.raises(ValueError, match="not enabled"):
        crud.assert_audience_commerce_disabled("ticket sale")


def test_no_audience_payable_route_exists():
    """Ledger 3 is separation-by-absence, which holds only while nothing is added. Asserts no
    commercial route is reachable by an unauthenticated or audience-scoped caller other than
    the provider webhooks, which are signature-gated and take no tenant input."""
    from app.routers import commercial as router_mod

    src = code_only(router_mod)
    for token in ("audience_payment", "attendee_payment", "ticket_purchase", "organizer_payout"):
        assert token not in src, f"an audience/organizer money path appeared: {token}"

    # Every route either resolves a USER through a dependency or is one of the two provider
    # webhooks (signature-gated, and they accept no tenant identifier).
    #
    # Detected by "has a Depends that isn't get_db" rather than by name: require_commercial()
    # returns a closure, so its Depends stringifies as the inner function and a name match
    # would silently pass every route it gates.
    from fastapi import params as fastapi_params

    from app.db import get_db

    unauthenticated = []
    for route in router_mod.router.routes:
        fn = getattr(route, "endpoint", None)
        if fn is None:
            continue
        has_user_dep = False
        for p in inspect.signature(fn).parameters.values():
            dep = p.default
            if isinstance(dep, fastapi_params.Depends) and dep.dependency is not get_db:
                has_user_dep = True
                break
        if not has_user_dep:
            unauthenticated.append(route.path)
    assert set(unauthenticated) <= {"/commercial/webhooks/payments", "/commercial/webhooks/stripe"}, \
        f"unauthenticated commercial routes: {sorted(set(unauthenticated))}"


# ══════════════════════════════════════════════════════════════════════════════════════
# ASSURED EVENT + READINESS (pure logic)
# ══════════════════════════════════════════════════════════════════════════════════════

def _profile(**kw):
    base = dict(version_label="v1", risk_tier="r2", name="P", managed_only=False,
                assured_event_eligible=False, requires_backup_contribution=False,
                requires_dual_recording=False, requires_preview_return=False,
                requires_command_owner=False, requires_reserved_capacity=False,
                requires_change_freeze=False, requires_full_rehearsal=False, status="published")
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_non_assured_order_has_no_assured_requirements():
    assert crud.assured_event_reasons(SimpleNamespace(assured_event=False), _profile()) == []
    assert crud.assured_event_reasons(None, _profile()) == []


def test_assured_event_requires_an_eligible_profile():
    order = SimpleNamespace(assured_event=True)
    reasons = crud.assured_event_reasons(order, _profile(assured_event_eligible=False))
    assert any("not Assured-Event-eligible" in r for r in reasons)


def test_assured_event_requires_dual_recording_and_backup():
    """Eligibility alone is not the commitment: an Assured Event without independent recording
    or a backup contribution path cannot deliver what it promises."""
    order = SimpleNamespace(assured_event=True)
    reasons = crud.assured_event_reasons(order, _profile(assured_event_eligible=True))
    assert any("dual recording" in r for r in reasons)
    assert any("backup contribution" in r for r in reasons)


def test_a_fully_configured_assured_profile_passes():
    order = SimpleNamespace(assured_event=True)
    profile = _profile(assured_event_eligible=True, requires_dual_recording=True,
                        requires_backup_contribution=True)
    assert crud.assured_event_reasons(order, profile) == []


def test_assured_event_with_no_profile_is_blocked():
    reasons = crud.assured_event_reasons(SimpleNamespace(assured_event=True), None)
    assert reasons and "no service profile" in reasons[0]


def test_managed_only_makes_the_command_owner_gate_non_waivable():
    """requires_command_owner unset but managed_only set must still demand a command owner —
    that is what "managed-only" means operationally."""
    codes = crud.required_readiness_checks(_profile(managed_only=True, requires_command_owner=False))
    assert crud.COMMAND_OWNER_CHECK in codes


def test_r3_requires_recorded_operational_acceptance():
    codes = crud.required_readiness_checks(_profile(risk_tier="r3"))
    assert crud.OPERATIONAL_ACCEPTANCE_CHECK in codes


def test_r2_does_not_require_operational_acceptance():
    codes = crud.required_readiness_checks(_profile(risk_tier="r2"))
    assert crud.OPERATIONAL_ACCEPTANCE_CHECK not in codes


def test_no_profile_requires_nothing():
    assert crud.required_readiness_checks(None) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# REAL POSTGRES
# ══════════════════════════════════════════════════════════════════════════════════════

@needs_db
class TestCommercialLifecycleDB:
    """One org + published registries + an event, rebuilt per test."""

    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            suffix = uuid.uuid4().hex[:8]
            org = Organization(name=f"lc-{suffix}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Maker", email=f"mk-{suffix}@t.test",
                        username=f"mk{suffix}", password_hash="x", role="super_admin")
            checker = User(org_id=org.id, full_name="Checker", email=f"ck-{suffix}@t.test",
                           username=f"ck{suffix}", password_hash="x", role="super_admin")
            db.add_all([user, checker])
            db.flush()
            entity = SellerLegalEntity(code=f"ent_{suffix}", legal_name="Zoiko Test Ltd",
                                        country="GB", status="active", effective_from=NOW)
            db.add(entity)
            db.flush()
            account = CommercialAccount(org_id=org.id, seller_legal_entity_id=entity.code,
                                         billing_classification="commercial")
            db.add(account)
            catalog = CatalogVersion(version_label=f"cv-{suffix}", vertical="memorials",
                                      status="published", effective_at=NOW)
            db.add(catalog)
            db.flush()
            line = CatalogLine(catalog_version_id=catalog.id, service_code="base",
                                name="Base production", unit_price=Decimal("1000.00"),
                                currency="USD", unit_basis="per_event")
            extra = CatalogLine(catalog_version_id=catalog.id, service_code="extra",
                                 name="Extra camera", unit_price=Decimal("250.00"),
                                 currency="USD", unit_basis="per_event", is_addon=True)
            db.add_all([line, extra])
            policy = CancellationPolicy(version_label=f"cp-{suffix}", vertical="memorials",
                                         risk_tier=None, lead_time_min_hours=0,
                                         lead_time_max_hours=None,
                                         refund_percentage=Decimal("50.00"),
                                         status="published", effective_at=NOW)
            db.add(policy)
            pool = CapacityPool(resource_type=f"op_{suffix}", window_start=NOW - timedelta(hours=2),
                                 window_end=NOW + timedelta(hours=10), total_capacity=3,
                                 status="active")
            db.add(pool)
            event = Event(org_id=org.id, created_by=user.id, title="Lifecycle event",
                           status="published", billing_classification="commercial",
                           category="memorials", start_time=NOW + timedelta(days=30),
                           end_time=NOW + timedelta(days=30, hours=2))
            db.add(event)
            db.commit()
            ids = SimpleNamespace(
                org_id=org.id, user_id=user.id, checker_id=checker.id, account_id=account.id,
                catalog_id=catalog.id, line_id=line.id, extra_line_id=extra.id,
                policy_id=policy.id, pool_id=pool.id, resource_type=pool.resource_type,
                event_id=event.id, entity_code=entity.code, entity_id=entity.id,
                profile_ids=[],
            )
        yield ids
        with Session(engine) as db:
            for sql in (
                "DELETE FROM commercial_state_transitions WHERE event_id=:e",
                "DELETE FROM event_reschedules WHERE event_id=:e",
                "DELETE FROM capacity_reservations WHERE event_id=:e",
                "DELETE FROM readiness_checks WHERE event_id=:e",
                # Added for the dispute/replay tests. FK-safe order: provider_events and
                # payment_disputes both reference payments, so they go before the per-order
                # deletes below; replay_entitlements references the event.
                "DELETE FROM replay_entitlements WHERE event_id=:e",
            ):
                db.execute(text(sql), {"e": ids.event_id})
            order_ids = [r[0] for r in db.execute(
                text("SELECT id FROM event_orders WHERE event_id=:e"), {"e": ids.event_id})]
            for oid in order_ids:
                for sql in (
                    "DELETE FROM provider_events WHERE payment_id IN "
                    "(SELECT id FROM payments WHERE event_order_id=:o)",
                    "DELETE FROM payment_disputes WHERE event_order_id=:o",
                    "DELETE FROM refund_credits WHERE event_order_id=:o",
                    "DELETE FROM payments WHERE event_order_id=:o",
                    "DELETE FROM payment_schedules WHERE event_order_id=:o",
                    "DELETE FROM invoices WHERE event_order_id=:o",
                    "DELETE FROM event_order_versions WHERE event_order_id=:o",
                    "DELETE FROM change_orders WHERE event_order_id=:o",
                    "DELETE FROM event_order_lines WHERE event_order_id=:o",
                    "DELETE FROM commercial_exceptions WHERE event_order_id=:o",
                ):
                    db.execute(text(sql), {"o": oid})
            db.execute(text("DELETE FROM commercial_exceptions WHERE event_id=:e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM event_orders WHERE event_id=:e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM commercial_quotes WHERE event_id=:e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": ids.org_id})
            db.execute(text("DELETE FROM events WHERE id=:e"), {"e": ids.event_id})
            db.execute(text("DELETE FROM capacity_pools WHERE id=:p"), {"p": ids.pool_id})
            db.execute(text("DELETE FROM cancellation_policies WHERE id=:p"), {"p": ids.policy_id})
            db.execute(text("DELETE FROM catalog_lines WHERE catalog_version_id=:c"), {"c": ids.catalog_id})
            db.execute(text("DELETE FROM catalog_versions WHERE id=:c"), {"c": ids.catalog_id})
            db.execute(text("DELETE FROM commercial_accounts WHERE id=:a"), {"a": ids.account_id})
            db.execute(text("DELETE FROM seller_legal_entities WHERE id=:s"), {"s": ids.entity_id})
            for pid in ids.profile_ids:
                db.execute(text("UPDATE events SET service_profile_id=NULL WHERE service_profile_id=:p"), {"p": pid})
                db.execute(text("DELETE FROM service_profiles WHERE id=:p"), {"p": pid})
            db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": ids.org_id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": ids.org_id})
            db.commit()

    # ── helpers ──────────────────────────────────────────────────────────────────────

    def _order(self, db, ids, *, accept=True, with_tax=True, lines=(("line_id", 1),),
                profile=None):
        # `profile` goes on the ORDER, not just the event: accept_order syncs
        # event.service_profile_id FROM the order, so setting it only on the event is undone
        # the moment the order is accepted.
        order = crud.create_order(
            db, db.get(Event, ids.event_id),
            commercial_account=db.get(CommercialAccount, ids.account_id),
            catalog_version=db.get(CatalogVersion, ids.catalog_id),
            purchaser_type="organization", purchaser_id=ids.user_id,
            service_profile=profile, cancellation_policy=db.get(CancellationPolicy, ids.policy_id),
            currency="USD", idempotency_key=f"k-{uuid.uuid4().hex[:10]}",
        )
        for attr, qty in lines:
            crud.add_order_line(db, order, db.get(CatalogLine, getattr(ids, attr)),
                                 quantity=Decimal(qty))
        if with_tax:
            crud.record_tax_determination(
                db, order, db.get(User, ids.user_id), tax_amount=Decimal("0.00"),
                treatment="zero_rated", jurisdiction="GB", source="test",
                exemption_reason="test fixture")
        if accept:
            crud.submit_order_for_acceptance(db, order)
            crud.accept_order(db, order, db.get(User, ids.user_id))
        return order

    def _pay(self, db, ids, order, amount, *, provider="mock"):
        """A settled payment, created directly — the provider path is covered elsewhere.

        `provider` matters for the dispute tests: crud.ingest_dispute_event resolves a payment
        by (provider, provider_payment_ref), so a dispute arriving as "stripe" cannot match a
        payment recorded under "mock" — it correctly becomes unmatched money instead.
        """
        payment = Payment(event_order_id=order.id, provider=provider,
                           provider_payment_ref=f"pi_{uuid.uuid4().hex[:10]}",
                           amount=Decimal(amount), currency="USD", state="paid",
                           idempotency_key=str(uuid.uuid4()), captured_at=NOW, settled_at=NOW)
        db.add(payment)
        db.commit()
        db.refresh(payment)
        return payment

    def _hold(self, db, ids, order, *, resource=None):
        return crud.soft_hold_capacity(
            db, db.get(Event, ids.event_id), resource_type=resource or ids.resource_type,
            window_start=NOW, window_end=NOW + timedelta(hours=2), quantity=1,
            event_order=order, actor=db.get(User, ids.user_id))

    # ── lifecycle derivation ─────────────────────────────────────────────────────────

    def test_event_with_no_order_is_draft(self, ctx):
        with Session(engine) as db:
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), None)
            assert state["state"] == "draft"

    def test_an_issued_quote_makes_it_quoted(self, ctx):
        with Session(engine) as db:
            quote = crud.create_quote(
                db, db.get(Event, ctx.event_id),
                catalog_version=db.get(CatalogVersion, ctx.catalog_id), amount=Decimal("1000"),
                tax_amount=None, currency="USD", created_by=db.get(User, ctx.user_id),
                valid_until=NOW + timedelta(days=7))
            crud.issue_quote(db, quote)
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), None)
            assert state["state"] == "quoted"

    def test_a_draft_quote_is_not_quoted(self, ctx):
        """A quote nobody has issued is not a commercial position."""
        with Session(engine) as db:
            crud.create_quote(
                db, db.get(Event, ctx.event_id),
                catalog_version=db.get(CatalogVersion, ctx.catalog_id), amount=Decimal("1000"),
                tax_amount=None, currency="USD", created_by=db.get(User, ctx.user_id),
                valid_until=None)
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), None)
            assert state["state"] == "draft"

    def test_accepted_unpaid_order_is_order_accepted_or_financial_hold(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), order)
            # No required milestone exists yet, so nothing is in hold — capacity is what's left.
            assert state["state"] in ("order_accepted", "confirmed", "ready")

    def test_an_overdue_milestone_puts_the_order_in_financial_hold(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.create_payment_schedule(db, order, milestone="deposit",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW - timedelta(days=2))
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), order)
            assert state["state"] == "financial_hold"
            assert state["financial_state"] == "financial_hold"

    def test_paying_the_milestone_clears_financial_hold(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.create_payment_schedule(db, order, milestone="deposit",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW - timedelta(days=2))
            self._pay(db, ctx, order, "1000.00")
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), order)
            assert state["state"] != "financial_hold"
            assert state["financial_state"] == "satisfied"

    def test_a_canceled_order_is_terminal_regardless_of_other_facts(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            order.status = "canceled"
            db.commit()
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), order)
            assert state["state"] == "canceled"

    def test_a_live_event_reports_live(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            ev.status = "live"
            db.commit()
            assert crud.commercial_lifecycle_state(db, ev, order)["state"] == "live"

    def test_an_ended_event_reports_completed(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            ev.status = "ended"
            db.commit()
            assert crud.commercial_lifecycle_state(db, ev, order)["state"] == "completed"

    def test_transitions_are_logged_append_only(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            rows = crud.lifecycle_history(db, order_id=order.id)
            assert rows, "acceptance recorded no transition"
            assert rows[0].to_state in COMMERCIAL_LIFECYCLE_STATES
            assert rows[0].trigger == "order.accept"
            before = len(rows)
            # A second sync with no change must not append a duplicate row.
            crud.sync_lifecycle(db, db.get(Event, ctx.event_id), order, trigger="test.noop")
            db.commit()
            assert len(crud.lifecycle_history(db, order_id=order.id)) == before

    def test_a_real_state_change_appends_a_transition(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            before = len(crud.lifecycle_history(db, order_id=order.id))
            crud.create_payment_schedule(db, order, milestone="deposit",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW - timedelta(days=2))
            crud.sync_lifecycle(db, db.get(Event, ctx.event_id), order, trigger="test.hold")
            db.commit()
            history = crud.lifecycle_history(db, order_id=order.id)
            assert len(history) == before + 1
            assert history[-1].to_state == "financial_hold"
            assert history[-1].financial_state == "financial_hold"

    # ── cancellation ─────────────────────────────────────────────────────────────────

    def test_cancelling_twice_is_refused(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                               db.get(User, ctx.user_id), reason="first")
            with pytest.raises(ValueError, match="already"):
                crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                                   db.get(User, ctx.user_id), reason="second")

    def test_cancelling_an_unpaid_order_raises_no_refund(self, ctx):
        """The policy entitles 50% of the total, but nothing was collected — a refund for money
        never received is not a refund."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            result = crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                                        db.get(User, ctx.user_id), reason="unpaid")
            assert result["policy_refund_amount"] == Decimal("500.00")
            assert result["refund_amount"] == Decimal("0.00")
            assert result["refund_credits"] == []

    def test_cancellation_refund_is_capped_at_collected_cash(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            self._pay(db, ctx, order, "200.00")      # less than the 500 entitlement
            result = crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                                        db.get(User, ctx.user_id), reason="part paid")
            assert result["policy_refund_amount"] == Decimal("500.00")
            assert result["refund_amount"] == Decimal("200.00")

    def test_cancellation_refund_is_bound_to_a_real_payment(self, ctx):
        """The defect: the remedy was created with source_payment_id NULL, so executing it
        marked it `executed` while the payment stayed `paid` and no money was returned."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00")
            result = crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                                        db.get(User, ctx.user_id), reason="paid")
            assert result["refund_amount"] == Decimal("500.00")
            credits = result["refund_credits"]
            assert len(credits) == 1
            assert credits[0].source_payment_id == payment.id

    def test_a_cancellation_refund_actually_moves_money(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00")
            result = crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                                        db.get(User, ctx.user_id), reason="paid")
            rc = result["refund_credits"][0]
            crud.approve_refund_credit(db, rc, db.get(User, ctx.checker_id))
            crud.execute_refund_credit(db, rc, actor=db.get(User, ctx.checker_id))
            db.refresh(payment)
            assert rc.status == "executed"
            assert rc.provider_ref, "no provider reference — nothing was actually refunded"
            assert payment.state == "part_refunded"
            assert crud.order_settlement(db, order.id)["net"] == Decimal("500.00")

    def test_cancellation_releases_held_capacity(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            reservation = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, reservation, order, actor=db.get(User, ctx.user_id))
            crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                               db.get(User, ctx.user_id), reason="release")
            db.refresh(reservation)
            assert reservation.state == "released"
            util = crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))
            assert util["available_capacity"] == util["total_capacity"]

    # ── refund netting end to end ────────────────────────────────────────────────────

    def test_a_refund_reopens_the_payable_balance(self, ctx):
        """order_payable_amount refused re-collection as "already paid in full" after a full
        refund, because the refund was never netted out of the collected figure."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00")
            with pytest.raises(ValueError, match="already paid in full"):
                crud.order_payable_amount(db, order)
            rc = RefundCredit(event_order_id=order.id, source_payment_id=payment.id,
                               type="refund", amount=Decimal("1000.00"),
                               reason_code="test", requested_by=ctx.user_id, status="approved")
            db.add(rc)
            db.commit()
            crud.execute_refund_credit(db, rc, actor=db.get(User, ctx.checker_id))
            payable, currency = crud.order_payable_amount(db, order)
            assert payable == Decimal("1000.00") and currency == "USD"

    def test_the_same_payment_cannot_be_refunded_twice(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00")
            for _ in range(1):
                rc = RefundCredit(event_order_id=order.id, source_payment_id=payment.id,
                                   type="refund", amount=Decimal("1000.00"), reason_code="t",
                                   requested_by=ctx.user_id, status="approved")
                db.add(rc)
                db.commit()
                crud.execute_refund_credit(db, rc, actor=db.get(User, ctx.checker_id))
            second = RefundCredit(event_order_id=order.id, source_payment_id=payment.id,
                                   type="refund", amount=Decimal("1000.00"), reason_code="t",
                                   requested_by=ctx.user_id, status="approved")
            db.add(second)
            db.commit()
            with pytest.raises(ValueError, match="still refundable"):
                crud.execute_refund_credit(db, second, actor=db.get(User, ctx.checker_id))

    def test_a_refund_with_no_source_payment_is_refused(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            rc = RefundCredit(event_order_id=order.id, type="refund", amount=Decimal("10.00"),
                               reason_code="t", requested_by=ctx.user_id, status="approved")
            db.add(rc)
            db.commit()
            with pytest.raises(ValueError, match="not bound to a source payment"):
                crud.execute_refund_credit(db, rc, actor=db.get(User, ctx.checker_id))

    # ── activate_order gates ─────────────────────────────────────────────────────────

    def test_activation_is_refused_in_financial_hold(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.create_payment_schedule(db, order, milestone="deposit",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW - timedelta(days=2))
            with pytest.raises(ValueError, match="financial hold"):
                crud.activate_order(db, order, actor=db.get(User, ctx.user_id))

    def test_activation_is_refused_without_required_capacity(self, ctx):
        """ACTIVE previously meant only "status was accepted" — reachable with no reserved
        capacity at all, so the state told an operator nothing about deliverability."""
        with Session(engine) as db:
            profile = ServiceProfile(version_label="p-act", risk_tier="r2", name="R2",
                                      requires_reserved_capacity=True, status="published",
                                      effective_at=NOW)
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = self._order(db, ctx, profile=profile)
            assert db.get(Event, ctx.event_id).service_profile_id == profile.id
            with pytest.raises(ValueError, match="capacity"):
                crud.activate_order(db, order, actor=db.get(User, ctx.user_id))

    def test_activation_succeeds_once_the_gates_are_met(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.activate_order(db, order, actor=db.get(User, ctx.user_id))
            assert order.status == "active"

    # ── change orders ────────────────────────────────────────────────────────────────

    def test_a_change_order_needs_line_operations(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            with pytest.raises(ValueError, match="line operations"):
                crud.create_change_order(db, order, changes={}, price_delta=Decimal("100"),
                                          reason="no line ops", actor=db.get(User, ctx.user_id))

    def test_a_change_order_delta_is_computed_not_asserted(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            changes = {"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "2"}]}
            with pytest.raises(ValueError, match="does not match"):
                crud.create_change_order(db, order, changes=changes,
                                          price_delta=Decimal("1.00"),
                                          reason="mismatched delta", actor=db.get(User, ctx.user_id))
            co = crud.create_change_order(db, order, changes=changes, price_delta=None,
                                           reason="scope change", actor=db.get(User, ctx.user_id))
            assert co.price_delta == Decimal("500.00")     # 2 x 250

    def test_accepting_a_change_order_materializes_real_lines(self, ctx):
        """The defect: subtotal moved by the delta while no line changed, so the order's total
        no longer equalled the sum of its own lines."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            before_lines = len(order.lines)
            co = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]},
                price_delta=None, reason="test change", actor=db.get(User, ctx.user_id))
            crud.accept_change_order(db, co, db.get(User, ctx.checker_id))
            db.refresh(order)
            lines = db.scalars(
                select(EventOrderLine).where(EventOrderLine.event_order_id == order.id)).all()
            assert len(lines) == before_lines + 1
            assert order.subtotal == sum(Decimal(l.line_total) for l in lines)
            assert order.order_version == 2
            # A scope change invalidates the tax basis.
            assert order.tax_amount is None

    def test_removing_a_line_keeps_the_subtotal_consistent(self, ctx):
        """Removal end to end: the line goes, the subtotal follows it, and the removed line
        survives in the previous version's snapshot rather than being lost."""
        with Session(engine) as db:
            order = self._order(db, ctx, lines=(("line_id", 1), ("extra_line_id", 1)))
            assert order.subtotal == Decimal("1250.00")
            removable = [l for l in order.lines if l.service_code == "extra"][0]
            # A removal reduces revenue, so it needs the same governed approval as a discount.
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="discount",
                rationale="scope reduced by customer", evidence={"ticket": "T-9"},
                order=order, overridden_gate="pricing")
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            co = crud.create_change_order(
                db, order, changes={"remove_line_ids": [str(removable.id)]},
                price_delta=None, reason="test change", actor=db.get(User, ctx.user_id))
            assert co.price_delta == Decimal("-250.00")

            crud.accept_change_order(db, co, db.get(User, ctx.checker_id))
            db.refresh(order)
            lines = db.scalars(
                select(EventOrderLine).where(EventOrderLine.event_order_id == order.id)).all()
            assert len(lines) == 1
            assert order.subtotal == Decimal("1000.00")
            assert order.subtotal == sum(Decimal(l.line_total) for l in lines)
            # History survives the deletion: version 1's snapshot still carries both lines.
            versions = crud.list_order_versions(db, order.id)
            assert len(versions[0].snapshot["lines"]) == 2

    def test_a_revenue_reduction_needs_an_approved_discount_exception(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx, lines=(("line_id", 1), ("extra_line_id", 1)))
            removable = [l for l in order.lines if l.service_code == "extra"][0]
            changes = {"remove_line_ids": [str(removable.id)]}
            with pytest.raises(ValueError, match="discount"):
                crud.create_change_order(db, order, changes=changes, price_delta=None,
                                          reason="scope change", actor=db.get(User, ctx.user_id))
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="discount",
                rationale="agreed reduction", evidence={"ticket": "T-1"},
                order=order, overridden_gate="pricing")
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            co = crud.create_change_order(db, order, changes=changes, price_delta=None,
                                           reason="scope change", actor=db.get(User, ctx.user_id))
            assert co.price_delta == Decimal("-250.00")

    def test_a_stale_change_order_is_refused(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            first = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]},
                price_delta=None, reason="test change", actor=db.get(User, ctx.user_id))
            second = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]},
                price_delta=None, reason="test change", actor=db.get(User, ctx.user_id))
            crud.accept_change_order(db, first, db.get(User, ctx.checker_id))
            with pytest.raises(ValueError, match="stale"):
                crud.accept_change_order(db, second, db.get(User, ctx.checker_id))

    def test_a_change_order_cannot_use_a_foreign_catalog_line(self, ctx):
        with Session(engine) as db:
            other = CatalogVersion(version_label="other", vertical="worship",
                                    status="published", effective_at=NOW)
            db.add(other)
            db.flush()
            foreign = CatalogLine(catalog_version_id=other.id, service_code="x", name="X",
                                   unit_price=Decimal("5.00"), currency="USD")
            db.add(foreign)
            db.commit()
            order = self._order(db, ctx)
            try:
                with pytest.raises(ValueError, match="different catalog version"):
                    crud.create_change_order(
                        db, order,
                        changes={"add_lines": [{"catalog_line_id": str(foreign.id), "quantity": "1"}]},
                        price_delta=None, reason="test change", actor=db.get(User, ctx.user_id))
            finally:
                db.execute(text("DELETE FROM catalog_lines WHERE id=:i"), {"i": foreign.id})
                db.execute(text("DELETE FROM catalog_versions WHERE id=:i"), {"i": other.id})
                db.commit()

    # ── capacity ─────────────────────────────────────────────────────────────────────

    def test_capacity_transitions_are_audited(self, ctx):
        """Not one reservation transition was audited before — hold, reserve and release all
        moved committed inventory silently."""
        from app.models import AuditLog

        with Session(engine) as db:
            order = self._order(db, ctx)
            reservation = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, reservation, order, actor=db.get(User, ctx.user_id))
            crud.release_capacity(db, reservation, "test", actor=db.get(User, ctx.user_id))
            actions = {a.action for a in db.scalars(
                select(AuditLog).where(AuditLog.org_id == ctx.org_id)).all()}
            for expected in ("commercial.capacity.soft_hold", "commercial.capacity.hard_reserve",
                             "commercial.capacity.release"):
                assert expected in actions, f"{expected} left no audit trail"

    def test_a_lapsed_soft_hold_is_swept_and_returns_inventory(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            reservation = self._hold(db, ctx, order)
            reservation.soft_hold_expires_at = NOW - timedelta(minutes=5)
            db.commit()
            assert crud.expire_stale_soft_holds(db) >= 1
            db.refresh(reservation)
            assert reservation.state == "expired"
            util = crud.pool_utilisation(db, db.get(CapacityPool, ctx.pool_id))
            assert util["available_capacity"] == util["total_capacity"]

    def test_an_expired_hold_cannot_be_hard_reserved(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            reservation = self._hold(db, ctx, order)
            reservation.soft_hold_expires_at = NOW - timedelta(minutes=5)
            db.commit()
            with pytest.raises(ValueError, match="expired"):
                crud.hard_reserve_capacity(db, reservation, order,
                                            actor=db.get(User, ctx.user_id))

    # ── reschedule ───────────────────────────────────────────────────────────────────

    def test_reschedule_preserves_the_original_window(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            original_start = ev.start_time
            new_start = NOW + timedelta(days=45)
            result = crud.reschedule_event(
                db, ev, order, db.get(User, ctx.user_id), new_start=new_start,
                new_end=new_start + timedelta(hours=2), reason="venue clash")
            record = result["reschedule"]
            assert record.previous_start_time == original_start
            assert record.new_start_time == new_start
            db.refresh(ev)
            assert ev.start_time == new_start

    def test_reschedule_releases_capacity_and_says_so(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            reservation = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, reservation, order, actor=db.get(User, ctx.user_id))
            result = crud.reschedule_event(
                db, db.get(Event, ctx.event_id), order, db.get(User, ctx.user_id),
                new_start=NOW + timedelta(days=60), new_end=NOW + timedelta(days=60, hours=2),
                reason="moved")
            db.refresh(reservation)
            assert reservation.state == "released"
            assert reservation.release_reason == "event_rescheduled"
            assert result["capacity_requires_rehold"] is True
            assert str(reservation.id) in (result["reschedule"].released_reservations or {})

    def test_a_live_event_cannot_be_rescheduled(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            ev.status = "live"
            db.commit()
            with pytest.raises(ValueError, match="delivering now"):
                crud.reschedule_event(db, ev, order, db.get(User, ctx.user_id),
                                       new_start=NOW + timedelta(days=60), new_end=None,
                                       reason="too late")

    def test_reschedule_history_is_append_only(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            for days in (40, 50):
                crud.reschedule_event(
                    db, db.get(Event, ctx.event_id), order, db.get(User, ctx.user_id),
                    new_start=NOW + timedelta(days=days),
                    new_end=NOW + timedelta(days=days, hours=2), reason=f"move {days}")
            history = crud.list_reschedules(db, ctx.event_id)
            assert len(history) == 2
            assert history[0].new_start_time < history[1].new_start_time

    def test_reschedule_refuses_an_end_before_start(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            with pytest.raises(ValueError, match="after it starts|after start"):
                crud.reschedule_event(
                    db, db.get(Event, ctx.event_id), order, db.get(User, ctx.user_id),
                    new_start=NOW + timedelta(days=40),
                    new_end=NOW + timedelta(days=39), reason="bad window")

    # ── write-off ────────────────────────────────────────────────────────────────────

    def test_a_write_off_needs_an_approved_exception(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="write_off",
                rationale="uncollectable", evidence={"ref": "W-1"}, order=order,
                amount_exposure=Decimal("1000.00"))
            with pytest.raises(ValueError, match="must be approved"):
                crud.execute_write_off(db, exc, db.get(User, ctx.checker_id))

    def test_an_approved_write_off_satisfies_the_balance(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.create_payment_schedule(db, order, milestone="final",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW - timedelta(days=1))
            assert crud.financial_readiness_state(db, order) == "financial_hold"
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="write_off",
                rationale="uncollectable", evidence={"ref": "W-1"}, order=order,
                amount_exposure=Decimal("1000.00"))
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            credit = crud.execute_write_off(db, exc, db.get(User, ctx.checker_id))
            assert credit.type == "fee_waiver" and credit.status == "executed"
            assert crud.financial_readiness_state(db, order) == "satisfied"
            # No cash: a waiver satisfies a balance but is not refundable.
            assert crud.order_settlement(db, order.id)["refundable"] == Decimal(0)

    def test_a_write_off_is_idempotent(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="write_off",
                rationale="uncollectable", evidence={"ref": "W-1"}, order=order,
                amount_exposure=Decimal("100.00"))
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            first = crud.execute_write_off(db, exc, db.get(User, ctx.checker_id))
            second = crud.execute_write_off(db, exc, db.get(User, ctx.checker_id))
            assert first.id == second.id
            assert crud.order_settlement(db, order.id)["credited"] == Decimal("100.00")

    def test_a_write_off_needs_a_positive_amount(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="write_off",
                rationale="no amount", evidence={"ref": "W-2"}, order=order)
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            with pytest.raises(ValueError, match="amount_exposure"):
                crud.execute_write_off(db, exc, db.get(User, ctx.checker_id))

    # ── risk tier / profile enforcement ──────────────────────────────────────────────

    def test_acceptance_refuses_an_unpublished_profile(self, ctx):
        with Session(engine) as db:
            profile = ServiceProfile(version_label="draft-p", risk_tier="r0", name="Draft",
                                      status="draft")
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = crud.create_order(
                db, db.get(Event, ctx.event_id),
                commercial_account=db.get(CommercialAccount, ctx.account_id),
                catalog_version=db.get(CatalogVersion, ctx.catalog_id),
                purchaser_type="organization", purchaser_id=ctx.user_id,
                service_profile=profile, cancellation_policy=None, currency="USD",
                idempotency_key=f"k-{uuid.uuid4().hex[:8]}")
            crud.add_order_line(db, order, db.get(CatalogLine, ctx.line_id))
            crud.submit_order_for_acceptance(db, order)
            with pytest.raises(ValueError, match="not published"):
                crud.accept_order(db, order, db.get(User, ctx.user_id))

    def test_acceptance_refuses_a_tier_mismatched_profile(self, ctx):
        with Session(engine) as db:
            profile = ServiceProfile(version_label="r3-p", risk_tier="r3", name="R3",
                                      status="published", effective_at=NOW)
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = crud.create_order(
                db, db.get(Event, ctx.event_id),
                commercial_account=db.get(CommercialAccount, ctx.account_id),
                catalog_version=db.get(CatalogVersion, ctx.catalog_id),
                purchaser_type="organization", purchaser_id=ctx.user_id,
                service_profile=profile, cancellation_policy=None, currency="USD",
                idempotency_key=f"k-{uuid.uuid4().hex[:8]}")
            order.risk_tier = "r2"          # disagrees with the profile
            crud.add_order_line(db, order, db.get(CatalogLine, ctx.line_id))
            crud.submit_order_for_acceptance(db, order)
            with pytest.raises(ValueError, match="R3|r3"):
                crud.accept_order(db, order, db.get(User, ctx.user_id))

    def test_assured_event_election_requires_an_eligible_profile(self, ctx):
        with Session(engine) as db:
            profile = ServiceProfile(version_label="ne-p", risk_tier="r0", name="NotEligible",
                                      status="published", effective_at=NOW,
                                      assured_event_eligible=False)
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = crud.create_order(
                db, db.get(Event, ctx.event_id),
                commercial_account=db.get(CommercialAccount, ctx.account_id),
                catalog_version=db.get(CatalogVersion, ctx.catalog_id),
                purchaser_type="organization", purchaser_id=ctx.user_id,
                service_profile=profile, cancellation_policy=None, currency="USD",
                idempotency_key=f"k-{uuid.uuid4().hex[:8]}")
            crud.add_order_line(db, order, db.get(CatalogLine, ctx.line_id))
            order.assured_event = True
            crud.submit_order_for_acceptance(db, order)
            with pytest.raises(ValueError, match="not Assured-Event-eligible"):
                crud.accept_order(db, order, db.get(User, ctx.user_id))

    def test_an_r2_commercial_event_without_a_profile_cannot_go_live(self, ctx):
        """R0 self-service legitimately has no profile. From R1 up the profile IS the control
        set, so its absence must read as a missing definition, not as no requirements."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            ev.risk_tier = "r2"
            ev.service_profile_id = None
            db.commit()
            reason = crud.golive_block_reason(db, ev)
            assert reason and "service profile" in reason

    def test_an_r0_commercial_event_without_a_profile_is_unaffected(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            ev = db.get(Event, ctx.event_id)
            ev.risk_tier = "r0"
            ev.service_profile_id = None
            db.commit()
            reason = crud.golive_block_reason(db, ev)
            assert reason is None or "service profile" not in reason

    def test_a_commercial_event_with_an_unaccepted_order_cannot_go_live(self, ctx):
        """The acceptance gate keyed only on an order EXISTING, so a draft order passed it."""
        with Session(engine) as db:
            self._order(db, ctx, accept=False)
            reason = crud.golive_block_reason(db, db.get(Event, ctx.event_id))
            assert reason and "no accepted order" in reason

    # ── reconciliation ───────────────────────────────────────────────────────────────

    def test_an_order_without_an_active_seller_entity_is_flagged(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            account = db.get(CommercialAccount, ctx.account_id)
            account.seller_legal_entity_id = None
            db.commit()
            flagged = {o.id for o in crud.list_orders_missing_seller_entity(db)}
            assert order.id in flagged

    def test_an_order_with_an_active_seller_entity_is_not_flagged(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            flagged = {o.id for o in crud.list_orders_missing_seller_entity(db)}
            assert order.id not in flagged

    # ── Phase 5b: activation gate completeness ───────────────────────────────────────

    def test_activation_is_refused_with_an_outstanding_milestone(self, ctx):
        """An ACTIVE order is a delivery commitment, so money merely `due` (not yet overdue)
        must still block it — the earlier gate only caught `financial_hold`."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.create_payment_schedule(db, order, milestone="deposit",
                                          amount=Decimal("1000.00"),
                                          due_at=NOW + timedelta(days=5))
            crud.reallocate_schedules(db, order.id)
            db.commit()
            with pytest.raises(ValueError, match="outstanding|financial hold"):
                crud.activate_order(db, order, actor=db.get(User, ctx.user_id))

    def test_activation_is_refused_without_required_readiness_checks(self, ctx):
        """Capacity satisfied and money settled, but a mandatory readiness check has not been
        attested — ACTIVE must not be reachable, or activation and go-live would disagree
        about whether the event is deliverable."""
        with Session(engine) as db:
            profile = ServiceProfile(version_label="p-ready", risk_tier="r0",
                                      name="Needs rehearsal", requires_full_rehearsal=True,
                                      status="published", effective_at=NOW)
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = self._order(db, ctx, profile=profile)
            # No capacity requirement on this profile, and no required milestone, so gates 1
            # and 2 pass — the only thing outstanding is the rehearsal attestation.
            assert crud.capacity_confirmed(db, db.get(Event, ctx.event_id))
            with pytest.raises(ValueError, match="readiness"):
                crud.activate_order(db, order, actor=db.get(User, ctx.user_id))

    def test_activation_succeeds_once_the_readiness_check_passes(self, ctx):
        with Session(engine) as db:
            profile = ServiceProfile(version_label="p-ready2", risk_tier="r0",
                                      name="Needs rehearsal", requires_full_rehearsal=True,
                                      status="published", effective_at=NOW)
            db.add(profile)
            db.commit()
            ctx.profile_ids.append(profile.id)
            order = self._order(db, ctx, profile=profile)
            crud.record_readiness_check(db, db.get(Event, ctx.event_id),
                                        check_code="full_rehearsal", status="pass",
                                        actor=db.get(User, ctx.user_id),
                                        evidence_reference="rehearsal-log-1")
            crud.activate_order(db, order, actor=db.get(User, ctx.user_id))
            assert order.status == "active"

    # ── Phase 5b: change-order approval rules and provenance ─────────────────────────

    def test_a_change_order_requires_a_reason(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            changes = {"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]}
            with pytest.raises(ValueError, match="requires a reason"):
                crud.create_change_order(db, order, changes=changes, price_delta=None,
                                          reason="  ", actor=db.get(User, ctx.user_id))

    def test_an_increase_needs_no_exception(self, ctx):
        """doc Section 25: an increase is allowed on an accepted order. Only the customer's
        acceptance of the change order itself gates the extra scope."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            co = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]},
                price_delta=None, reason="customer added a camera",
                actor=db.get(User, ctx.user_id))
            assert co.price_delta == Decimal("250.00")
            assert co.approval_exception_id is None

    def test_a_change_order_records_full_provenance(self, ctx):
        """requester, approver, timestamps, reason, affected lines and amount delta."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            co = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "2"}]},
                price_delta=None, reason="two extra cameras agreed on call",
                actor=db.get(User, ctx.user_id))
            assert co.requested_by == ctx.user_id
            assert co.reason == "two extra cameras agreed on call"
            assert co.price_delta == Decimal("500.00")
            assert co.created_at is not None
            assert co.applied_lines is None            # nothing applied while draft

            crud.accept_change_order(db, co, db.get(User, ctx.checker_id))
            db.refresh(co)
            assert co.approved_by == ctx.checker_id     # a DIFFERENT actor
            assert co.accepted_at is not None and co.effective_at is not None
            assert len(co.applied_lines["added"]) == 1
            assert co.applied_lines["added"][0]["line_total"] == "500.00"
            assert co.applied_lines["removed"] == []

    def test_applied_lines_preserve_a_removed_line(self, ctx):
        """The removed row is deleted from event_order_lines, so applied_lines is where its
        identity and amount survive as this change order's own evidence."""
        with Session(engine) as db:
            order = self._order(db, ctx, lines=(("line_id", 1), ("extra_line_id", 1)))
            removable = [l for l in order.lines if l.service_code == "extra"][0]
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="discount",
                rationale="scope cut", evidence={"ticket": "T-3"}, order=order,
                overridden_gate="pricing")
            crud.approve_commercial_exception(db, exc, db.get(User, ctx.checker_id))
            co = crud.create_change_order(
                db, order, changes={"remove_line_ids": [str(removable.id)]},
                price_delta=None, reason="customer dropped the extra camera",
                actor=db.get(User, ctx.user_id))
            assert co.approval_exception_id == exc.id
            crud.accept_change_order(db, co, db.get(User, ctx.checker_id))
            db.refresh(co)
            assert co.applied_lines["removed"][0]["service_code"] == "extra"
            assert co.applied_lines["removed"][0]["line_total"] == "250.00"

    def test_a_price_override_needs_a_finance_approver(self, ctx):
        """doc Section 25: an override is Finance's decision. An exception approved by an
        actor without finance authority cannot authorise one, even though it is `approved`."""
        with Session(engine) as db:
            order = self._order(db, ctx, lines=(("line_id", 1), ("extra_line_id", 1)))
            removable = [l for l in order.lines if l.service_code == "extra"][0]
            sales = User(org_id=ctx.org_id, full_name="Sales Person",
                         email=f"sales-{uuid.uuid4().hex[:8]}@t.test",
                         username=f"sl{uuid.uuid4().hex[:8]}", password_hash="x",
                         role="super_admin", staff_commercial_role="sales")
            db.add(sales)
            db.commit()
            exc = crud.request_commercial_exception(
                db, db.get(User, ctx.user_id), exception_type="price_override",
                rationale="bespoke price", evidence={"ticket": "T-4"}, order=order,
                overridden_gate="pricing")
            crud.approve_commercial_exception(db, exc, sales)   # sales, not finance
            changes = {"remove_line_ids": [str(removable.id)]}
            with pytest.raises(ValueError, match="Finance authority"):
                crud.create_change_order(db, order, changes=changes, price_delta=None,
                                          reason="override", actor=db.get(User, ctx.user_id))

    def test_the_worked_increase_example(self, ctx):
        """doc worked example: subtotal 1000, change +250, final 1250."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            assert order.subtotal == Decimal("1000.00")
            co = crud.create_change_order(
                db, order,
                changes={"add_lines": [{"catalog_line_id": str(ctx.extra_line_id), "quantity": "1"}]},
                price_delta=None, reason="+250", actor=db.get(User, ctx.user_id))
            assert co.price_delta == Decimal("250.00")
            crud.accept_change_order(db, co, db.get(User, ctx.checker_id))
            db.refresh(order)
            assert order.subtotal == Decimal("1250.00")
            assert order.total_amount == Decimal("1250.00")
            assert order.tax_amount is None            # re-determination forced

    # ── Phase 5b: capacity audit vocabulary ──────────────────────────────────────────

    def test_every_capacity_movement_emits_a_canonical_event(self, ctx):
        from app.models import AuditLog, CAPACITY_AUDIT_EVENTS

        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, held, order, actor=db.get(User, ctx.user_id))
            crud.consume_capacity(db, held, actor=db.get(User, ctx.user_id))
            # A second hold, lapsed then swept, to exercise the expiry event.
            lapsed = self._hold(db, ctx, order)
            lapsed.soft_hold_expires_at = NOW - timedelta(minutes=1)
            db.commit()
            crud.expire_stale_soft_holds(db, actor=db.get(User, ctx.user_id))
            third = self._hold(db, ctx, order)
            crud.release_capacity(db, third, "no longer needed",
                                   actor=db.get(User, ctx.user_id))

            rows = db.scalars(
                select(AuditLog).where(AuditLog.org_id == ctx.org_id)).all()
            emitted = {
                (r.meta or {}).get("capacity_event")
                for r in rows if r.action.startswith("commercial.capacity.")
            }
            for name in ("soft_hold_created", "hard_reserved", "consumed",
                         "soft_hold_expired", "released"):
                assert name in emitted, f"{name} never emitted; got {emitted}"
            # Every emitted name is from the canonical vocabulary.
            assert emitted <= set(CAPACITY_AUDIT_EVENTS)

    def test_capacity_audit_carries_actor_reason_and_references(self, ctx):
        from app.models import AuditLog

        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, held, order, actor=db.get(User, ctx.user_id))
            row = db.scalars(
                select(AuditLog)
                .where(AuditLog.action == "commercial.capacity.hard_reserved")
                .order_by(AuditLog.created_at.desc())
            ).first()
            assert row is not None
            assert row.actor_id == ctx.user_id            # actor
            assert row.created_at is not None             # timestamp
            assert row.meta["event_id"] == str(ctx.event_id)          # event reference
            assert row.meta["event_order_id"] == str(order.id)        # order reference
            assert row.meta["reason"]                                  # reason

    def test_an_unknown_capacity_event_name_is_refused(self, ctx):
        """The vocabulary is validated, so a typo is an error rather than an action string
        nothing will ever query."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            with pytest.raises(ValueError, match="Unknown capacity audit event"):
                crud._capacity_audit(db, capacity_event="soft_hold", reservation=held)

    # ── Phase 5b: lifecycle transition audit ─────────────────────────────────────────

    def test_lifecycle_transitions_record_a_reason(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                               db.get(User, ctx.user_id), reason="customer changed plans")
            transitions = crud.lifecycle_history(db, order_id=order.id)
            cancel = [t for t in transitions if t.to_state == "canceled"]
            assert cancel, "no canceled transition recorded"
            assert cancel[-1].reason == "customer changed plans"
            assert cancel[-1].actor_id == ctx.user_id
            assert cancel[-1].from_state is not None
            assert cancel[-1].created_at is not None

    def test_a_canceled_order_cannot_return_to_ready(self, ctx):
        """CANCELED is terminal. Even with every gate satisfied, the derived state stays
        canceled — there is no path back, and the graph agrees."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            crud.cancel_order(db, db.get(Event, ctx.event_id), order,
                               db.get(User, ctx.user_id), reason="done")
            state = crud.commercial_lifecycle_state(db, db.get(Event, ctx.event_id), order)
            assert state["state"] == "canceled"
            assert not crud.lifecycle_transition_allowed("canceled", "ready")

    # ── Phase 5b: maintenance jobs ───────────────────────────────────────────────────

    def test_maintenance_expires_lapsed_holds(self, ctx):
        from app.services import maintenance

        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            held.soft_hold_expires_at = NOW - timedelta(minutes=1)
            db.commit()
            result = maintenance.expire_capacity_holds(db, actor=db.get(User, ctx.user_id))
            assert result["expired"] >= 1
            db.refresh(held)
            assert held.state == "expired"

    def test_maintenance_releases_a_stale_hard_reservation(self, ctx):
        """A hard reservation whose window has passed on an event that never delivered is
        holding inventory against nothing."""
        from app.services import maintenance

        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, held, order, actor=db.get(User, ctx.user_id))
            held.window_end = NOW - timedelta(hours=1)
            db.commit()
            result = maintenance.release_stale_reservations(db, actor=db.get(User, ctx.user_id))
            assert result["released"] >= 1
            db.refresh(held)
            assert held.state == "released"
            assert held.release_reason == "stale_reservation_window_passed"

    def test_maintenance_keeps_a_reservation_for_a_delivered_event(self, ctx):
        """Evidence of what was committed to a delivery that happened is not housekeeping."""
        from app.services import maintenance

        with Session(engine) as db:
            order = self._order(db, ctx)
            held = self._hold(db, ctx, order)
            crud.hard_reserve_capacity(db, held, order, actor=db.get(User, ctx.user_id))
            held.window_end = NOW - timedelta(hours=1)
            ev = db.get(Event, ctx.event_id)
            ev.status = "ended"
            db.commit()
            maintenance.release_stale_reservations(db, actor=db.get(User, ctx.user_id))
            db.refresh(held)
            assert held.state == "hard_reserved"

    def test_maintenance_skips_unconfigured_windows(self, ctx):
        """No configured threshold means the job reports skipped, never assumes a window."""
        from app.services import maintenance

        with Session(engine) as db:
            result = maintenance.report_stale_payments(db)
            assert "skipped" in result or "window_hours" in result

    def test_maintenance_run_all_reports_every_job(self, ctx):
        from app.services import maintenance

        with Session(engine) as db:
            summary = maintenance.run_all(db, actor=db.get(User, ctx.user_id))
            assert len(summary["jobs"]) == len(maintenance.JOBS)
            assert summary["failed"] == [], summary["failed"]
            assert summary["ran_at"]

    # ── Phase 5c: inbound chargebacks reach the case record and the payment ──────────

    def _ingest_dispute(self, db, *, payment_ref, status, dispute_id="dp_test",
                         event_id=None, withdrawn=False, currency="usd"):
        from app.services import payments_stripe_events as se

        event = _dispute_event(status, dispute_id=dispute_id, intent=payment_ref,
                                withdrawn=withdrawn, currency=currency)
        if event_id:
            event["id"] = event_id
        translated = se.translate(event)
        return crud.ingest_dispute_event(
            db, provider="stripe", provider_event_id=translated.provider_event_id,
            event_type=translated.stripe_event_type, raw_body=b"{}",
            signature_verified=True, dispute=translated.dispute,
            payload=translated.payload, occurred_at=translated.occurred_at)

    def test_a_chargeback_opens_a_case_and_disputes_the_payment(self, ctx):
        """The gap this closes: dispute events were filed as evidence, so a real chargeback
        left the ledger reading `paid` while the money was contested."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            result = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                           status="needs_response",
                                           dispute_id=f"dp_{uuid.uuid4().hex[:8]}")
            assert result["applied"] is True and result["result"]["created"] is True
            db.refresh(payment)
            assert payment.state == "disputed"
            case = db.get(PaymentDispute, uuid.UUID(result["result"]["dispute_id"]))
            assert case.status == "evidence_required"
            assert case.reason_code == "fraudulent"
            assert case.event_order_id == order.id
            assert case.evidence_due_by is not None

    def test_a_lost_dispute_reverses_the_payment(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            ref = f"dp_{uuid.uuid4().hex[:8]}"
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="needs_response", dispute_id=ref)
            result = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                           status="lost", dispute_id=ref, withdrawn=True)
            db.refresh(payment)
            assert payment.state == "reversed"
            case = db.get(PaymentDispute, uuid.UUID(result["result"]["dispute_id"]))
            assert case.status == "lost" and case.resolved_at is not None
            # Money clawed back: it is no longer collected against the order.
            assert crud.order_settlement(db, order.id)["net"] == Decimal(0)

    def test_a_won_dispute_returns_the_payment_to_paid(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            ref = f"dp_{uuid.uuid4().hex[:8]}"
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="needs_response", dispute_id=ref)
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="won", dispute_id=ref)
            db.refresh(payment)
            assert payment.state == "paid"
            assert crud.order_settlement(db, order.id)["net"] == Decimal("1000.00")

    def test_the_same_dispute_event_is_applied_once(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            ref = f"dp_{uuid.uuid4().hex[:8]}"
            same_event_id = f"evt_{uuid.uuid4().hex[:10]}"
            first = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                          status="needs_response", dispute_id=ref,
                                          event_id=same_event_id)
            second = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                           status="needs_response", dispute_id=ref,
                                           event_id=same_event_id)
            assert first["applied"] is True
            assert second["duplicate"] is True and second["applied"] is False
            cases = db.scalars(
                select(PaymentDispute).where(PaymentDispute.event_order_id == order.id)).all()
            assert len(cases) == 1

    def test_a_dispute_update_advances_the_same_case(self, ctx):
        """A second Stripe event for the same dispute must not open a second case."""
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            ref = f"dp_{uuid.uuid4().hex[:8]}"
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="needs_response", dispute_id=ref)
            result = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                           status="under_review", dispute_id=ref)
            assert result["result"]["created"] is False
            cases = db.scalars(
                select(PaymentDispute).where(PaymentDispute.event_order_id == order.id)).all()
            assert len(cases) == 1 and cases[0].status == "evidence_submitted"

    def test_a_resolved_case_is_not_reopened_by_a_redelivered_event(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            ref = f"dp_{uuid.uuid4().hex[:8]}"
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="needs_response", dispute_id=ref)
            self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                  status="won", dispute_id=ref)
            late = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                         status="needs_response", dispute_id=ref,
                                         event_id=f"evt_late_{ref}")
            assert late["applied"] is False
            assert late["result"]["reason"] == "case_already_resolved"
            db.refresh(payment)
            assert payment.state == "paid"

    def test_a_dispute_for_an_unknown_payment_becomes_unmatched_money(self, ctx):
        """Never guessed onto an order (doc P5)."""
        from app.models import UnmatchedSettlement

        with Session(engine) as db:
            result = self._ingest_dispute(db, payment_ref=f"pi_ghost_{uuid.uuid4().hex[:8]}",
                                           status="needs_response",
                                           dispute_id=f"dp_{uuid.uuid4().hex[:8]}")
            assert result["applied"] is False
            assert result["result"]["reason"] == "dispute_payment_unmatched"
            settlement = db.get(UnmatchedSettlement,
                                 uuid.UUID(result["result"]["unmatched_settlement_id"]))
            assert settlement.status == "open"
            db.execute(text("DELETE FROM unmatched_settlements WHERE id=:i"),
                        {"i": settlement.id})
            db.execute(text("DELETE FROM provider_events WHERE id=:i"),
                        {"i": uuid.UUID(result["provider_event_id"])})
            db.commit()

    def test_a_currency_mismatched_dispute_is_refused(self, ctx):
        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")   # USD
            result = self._ingest_dispute(db, payment_ref=payment.provider_payment_ref,
                                           status="needs_response", currency="eur",
                                           dispute_id=f"dp_{uuid.uuid4().hex[:8]}")
            assert result["processing_status"] == "rejected"
            assert "currency mismatch" in result["result"]["reason"]
            db.refresh(payment)
            assert payment.state == "paid"          # untouched

    def test_an_unsigned_dispute_event_moves_nothing(self, ctx):
        from app.services import payments_stripe_events as se

        with Session(engine) as db:
            order = self._order(db, ctx)
            payment = self._pay(db, ctx, order, "1000.00", provider="stripe")
            event = _dispute_event("needs_response", dispute_id=f"dp_{uuid.uuid4().hex[:8]}",
                                    intent=payment.provider_payment_ref)
            translated = se.translate(event)
            result = crud.ingest_dispute_event(
                db, provider="stripe", provider_event_id=translated.provider_event_id,
                event_type=translated.stripe_event_type, raw_body=b"{}",
                signature_verified=False, dispute=translated.dispute)
            assert result["processing_status"] == "rejected"
            db.refresh(payment)
            assert payment.state == "paid"

    # ── Phase 5c: replay retention ───────────────────────────────────────────────────

    def test_a_lapsed_replay_is_expired_by_the_sweep(self, ctx):
        with Session(engine) as db:
            ent = crud.create_replay_entitlement(
                db, db.get(Event, ctx.event_id), scope="audience",
                expires_at=NOW - timedelta(hours=1))
            ent.publish_state = "published"
            db.commit()
            assert crud.expire_lapsed_replay_entitlements(db) >= 1
            db.refresh(ent)
            assert ent.publish_state == "expired"
            assert crud.replay_access_expired(ent)

    def test_an_unexpired_replay_is_left_alone(self, ctx):
        with Session(engine) as db:
            ent = crud.create_replay_entitlement(
                db, db.get(Event, ctx.event_id), scope="audience",
                expires_at=NOW + timedelta(days=7))
            ent.publish_state = "published"
            db.commit()
            crud.expire_lapsed_replay_entitlements(db)
            db.refresh(ent)
            assert ent.publish_state == "published"
