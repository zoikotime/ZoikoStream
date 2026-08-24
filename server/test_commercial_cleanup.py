"""Self-checks for the fail-closed money paths added by the hard-coded-value cleanup
(ZST-LE-COM-001 L4 tax determination, C4 capacity qualification).

Pure logic, no DB, no network — every guard under test raises before its function touches
the session, so a no-op stub session is enough. Run: `python test_commercial_cleanup.py`
(or pytest). Deliberately narrow: this covers only what the cleanup introduced, not the
wider commercial engine, which still has no coverage (see the audit).
"""
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.crud import commercial as crud
from app.models import CommercialAccount, SellerLegalEntity


class _StubSession:
    """Enough Session surface for these paths: audit()/issue_invoice add rows, scalar()
    answers the lookups, commit/refresh are no-ops. Real select() statements are built and
    simply ignored — nothing here inspects them.

    `execute` serves Phase 2's atomic invoice-number allocation (a raw ON CONFLICT ...
    RETURNING), and `get`/`scalar` serve its seller-entity resolution: issue_invoice now
    requires a registered, ACTIVE SellerLegalEntity (doc L1), so a stub that returns nothing
    correctly blocks issuance."""

    def __init__(self, scalar_result=None, get_result=None, next_invoice_number=1):
        self.added = []
        self._scalar_result = scalar_result
        self._get_result = get_result
        self._next_invoice_number = next_invoice_number

    def add(self, obj):
        self.added.append(obj)

    def scalar(self, stmt=None):
        return self._scalar_result

    def get(self, model, pk):
        return self._get_result

    def execute(self, stmt, params=None):
        return SimpleNamespace(scalar_one=lambda: self._next_invoice_number)

    def commit(self):
        pass

    def refresh(self, obj):
        pass


# An invoiceable seller: registered and active (Phase 2 requirement, doc L1).
_ACTIVE_SELLER = SellerLegalEntity(code="test_entity", legal_name="Test Entity Ltd",
                                   country="GB", status="active")


def _invoiceable_session(**kw):
    """A session whose seller-entity resolution succeeds, so a test can reach the tax gate."""
    account = CommercialAccount(id=uuid.uuid4(), org_id=uuid.uuid4(),
                                seller_legal_entity_id=_ACTIVE_SELLER.code)
    return _StubSession(get_result=account, scalar_result=_ACTIVE_SELLER, **kw)


def _order(**over):
    """A draft order with lines totalling 100.00 and NO tax determination by default."""
    fields = dict(
        id="00000000-0000-0000-0000-0000000000aa", status="draft",
        commercial_account_id=uuid.uuid4(),
        subtotal=Decimal("100.00"), total_amount=Decimal("100.00"),
        tax_amount=None, tax_treatment=None, tax_jurisdiction=None, tax_source=None,
        tax_rule_version=None, tax_effective_at=None, tax_determined_at=None,
        tax_determined_by=None, tax_exemption_reason=None, currency="USD",
    )
    fields.update(over)  # merge, not duplicate-kwarg
    return SimpleNamespace(**fields)


_ACTOR = SimpleNamespace(id="00000000-0000-0000-0000-0000000000bb", email="finance@zoikostream.com")

_VALID = dict(tax_amount=Decimal("20.00"), treatment="standard_rate",
              jurisdiction="GB", source="finance_manual_determination")


# ── Tax: invoice issuance fails closed on an undetermined basis (doc L4) ────────────────

def test_issue_invoice_blocked_without_a_tax_determination():
    """The regression this cleanup exists to prevent: tax_amount used to default to 0 and
    was never computed, so every invoice silently carried zero tax."""
    with pytest.raises(ValueError, match="no tax determination"):
        crud.issue_invoice(_StubSession(), _order())


def test_zero_tax_is_still_invoiceable_when_explicitly_determined():
    """A zero-rated/exempt supply is a real outcome — the cleanup must not make legitimate
    zero tax impossible, only unreachable BY DEFAULT.

    Uses an invoiceable session because Phase 2 additionally requires a registered, ACTIVE
    seller legal entity before any invoice may be issued (doc L1)."""
    order = _order(tax_amount=Decimal("0.00"), tax_treatment="zero_rated",
                   tax_jurisdiction="GB", tax_source="finance_manual_determination",
                   tax_exemption_reason="Zero-rated supply")
    invoice = crud.issue_invoice(_invoiceable_session(), order)
    assert invoice.tax_amount == Decimal("0.00")
    assert invoice.total_amount == Decimal("100.00")
    # The tax facts are SNAPSHOTTED onto the invoice, not read through the order FK, so a
    # later re-determination cannot rewrite an already-issued document.
    assert invoice.tax_treatment == "zero_rated"
    assert invoice.tax_jurisdiction == "GB"


# ── Tax: a determination must be justified, not just a number (doc L6) ──────────────────

@pytest.mark.parametrize("field", ["treatment", "jurisdiction", "source"])
def test_determination_requires_each_justifying_fact(field):
    with pytest.raises(ValueError):
        crud.record_tax_determination(_StubSession(), _order(), _ACTOR, **{**_VALID, field: "   "})


def test_determination_rejects_a_negative_amount():
    with pytest.raises(ValueError, match="0 or more"):
        crud.record_tax_determination(_StubSession(), _order(), _ACTOR,
                                      **{**_VALID, "tax_amount": Decimal("-1.00")})


@pytest.mark.parametrize("status", ["canceled", "terminated", "completed"])
def test_determination_refused_on_a_closed_out_order(status):
    with pytest.raises(ValueError, match=status):
        crud.record_tax_determination(_StubSession(), _order(status=status), _ACTOR, **_VALID)


def test_determination_stores_the_facts_and_recomputes_the_total():
    order = _order()
    crud.record_tax_determination(_StubSession(), order, _ACTOR, **_VALID)
    assert order.tax_amount == Decimal("20.00")
    assert order.total_amount == Decimal("120.00")  # subtotal 100 + tax 20
    assert (order.tax_treatment, order.tax_jurisdiction) == ("standard_rate", "GB")
    assert order.tax_source == "finance_manual_determination"
    assert order.tax_determined_at is not None and order.tax_determined_by == _ACTOR.id
    assert order.tax_exemption_reason is None  # not required for a non-zero determination


def test_scope_change_invalidates_a_stale_determination():
    """A determination is computed against a subtotal; changing the subtotal must force a
    re-determination rather than carrying the old tax basis onto the new total."""
    order = _order(tax_amount=Decimal("20.00"), tax_treatment="standard_rate")
    crud._clear_tax_determination(order)
    assert order.tax_amount is None and order.tax_treatment is None


# ── Capacity: no hard-coded envelope, fails closed when unconfigured (doc C4) ───────────

def test_no_hardcoded_capacity_envelope_constant_remains():
    assert not hasattr(crud, "DEFAULT_CAPACITY_ENVELOPE")


def _envelope_case(monkeypatch, *, expected_audience, envelope, approved):
    monkeypatch.setattr(crud.platform_settings, "audience_capacity_envelope", lambda db: envelope)
    session = _StubSession(scalar_result="reservation-id" if approved else None)
    event = SimpleNamespace(id="00000000-0000-0000-0000-0000000000cc",
                            expected_audience=expected_audience)
    return crud.envelope_capacity_block_reason(session, event)


def test_unconfigured_envelope_blocks_any_stated_audience(monkeypatch):
    """The core fix: with no approved band published, an audience figure cannot be qualified
    against an assumed number — it needs an explicit approval instead."""
    reason = _envelope_case(monkeypatch, expected_audience=10, envelope=None, approved=False)
    assert reason and "no approved platform audience capacity envelope" in reason


def test_unconfigured_envelope_is_cleared_by_an_approved_reservation(monkeypatch):
    assert _envelope_case(monkeypatch, expected_audience=10_000, envelope=None, approved=True) is None


def test_audience_within_an_approved_envelope_passes(monkeypatch):
    assert _envelope_case(monkeypatch, expected_audience=400, envelope=500, approved=False) is None


def test_audience_over_an_approved_envelope_is_blocked(monkeypatch):
    reason = _envelope_case(monkeypatch, expected_audience=900, envelope=500, approved=False)
    assert reason and "exceeds the approved 500-viewer envelope" in reason


def test_no_stated_audience_is_not_a_capacity_failure(monkeypatch):
    """An absent estimate is a missing claim, not missing configuration — profile-driven
    requirements in capacity_confirmed() still apply to it separately."""
    assert _envelope_case(monkeypatch, expected_audience=None, envelope=None, approved=False) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
