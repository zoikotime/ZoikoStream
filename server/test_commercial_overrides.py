"""Commercial overrides — ZST-COM-PLAN-001 Section 19 ("Commercial Overrides, Pilots &
Exceptional Access") and Section 14 ("Minimum Engineering Data Model").

Section 19's heading states the purpose: "Exceptions must be explicit enough that they cannot
become permanent shadow plans." Section 20 states the prohibition this closes: "No employee
may grant a paid feature through a feature flag, database edit or support impersonation. Use
an approved entitlement override record."

Nothing here asserts a value the document does not define: no price, no discount, no trial
length. `complimentary_access` exists as a TYPE because Section 19 names it; what a zero
charge costs is a Finance decision in ZST-COM-PRICE-001, which is not part of this record.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import admin as admin_crud
from app.db import engine
from app.models import (
    COMMERCIAL_OVERRIDE_TARGETS,
    COMMERCIAL_OVERRIDE_TYPES,
    AuditLog,
    CommercialOverride,
    Organization,
    User,
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

FUTURE = datetime.now(timezone.utc) + timedelta(days=7)
PAST = datetime.now(timezone.utc) - timedelta(days=1)

_MAKER = SimpleNamespace(id=uuid.uuid4(), email="ops@zoikostream.com")
_CHECKER = SimpleNamespace(id=uuid.uuid4(), email="finance@zoikostream.com")
_ORG = SimpleNamespace(id=uuid.uuid4())


class _Stub:
    """Enough Session surface for the validation paths: they add rows and never query."""

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


def _valid(**over):
    fields = dict(override_type="manual_override", target_type="feature",
                  target_key="feature.webhooks.live", reason="Board-approved pilot extension",
                  expires_at=FUTURE)
    fields.update(over)
    return fields


# ── Section 19 vocabulary ─────────────────────────────────────────────────────────────

def test_all_seven_documented_exception_types_exist():
    """Section 19's table names exactly these seven controlled exception classes."""
    assert set(COMMERCIAL_OVERRIDE_TYPES) == {
        "pilot_poc", "complimentary_access", "sales_demo", "incident_continuity",
        "contract_exception", "partner_bundle", "manual_override",
    }


def test_override_targets_match_the_documented_entitlement_shapes():
    """Section 14 calls the scope "feature/limit override"; `plan` is the administrative
    assignment Section 20 requires to travel through an override record."""
    assert set(COMMERCIAL_OVERRIDE_TARGETS) == {"feature", "limit", "plan"}


@pytest.mark.parametrize("override_type", COMMERCIAL_OVERRIDE_TYPES)
def test_every_documented_type_is_accepted(override_type):
    admin_crud.request_commercial_override(
        _Stub(), org=_ORG, actor=_MAKER, **_valid(override_type=override_type))


def test_an_undocumented_type_is_refused():
    """An exception class Section 19 does not name is a business rule this code may not add."""
    with pytest.raises(ValueError, match="Unknown override type"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(override_type="free_forever"))


def test_an_undocumented_target_is_refused():
    with pytest.raises(ValueError, match="Unknown override target"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(target_type="everything"))


# ── Section 19: "reasoned" ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", ["", "   ", None])
def test_an_unexplained_override_is_refused(reason):
    with pytest.raises(ValueError, match="requires a reason"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(reason=reason))


def test_an_override_must_name_what_it_overrides():
    with pytest.raises(ValueError, match="must name what it overrides"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(target_key="  "))


# ── Section 19: "time-bound ... automatic expiry" ─────────────────────────────────────

def test_an_override_without_an_expiry_is_refused():
    """THE control. A perpetual exception is the permanent shadow plan Section 19 forbids."""
    with pytest.raises(ValueError, match="must expire"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(expires_at=None))


def test_an_already_expired_override_cannot_be_created():
    with pytest.raises(ValueError, match="expiry must be in the future"):
        admin_crud.request_commercial_override(
            _Stub(), org=_ORG, actor=_MAKER, **_valid(expires_at=PAST))


def test_expiry_is_not_nullable_at_the_schema_level():
    """Enforced by the column, not only by the CRUD guard, so a direct insert cannot create a
    perpetual override either."""
    assert CommercialOverride.__table__.c.expires_at.nullable is False


# ── Creation posture ──────────────────────────────────────────────────────────────────

def test_an_override_is_created_requested_never_pre_approved():
    session = _Stub()
    override = admin_crud.request_commercial_override(
        session, org=_ORG, actor=_MAKER, **_valid())
    assert override.status == "requested"
    assert override.requested_by == _MAKER.id
    assert override.approver_id is None


def test_creation_is_audited_with_the_reason_and_expiry():
    session = _Stub()
    admin_crud.request_commercial_override(session, org=_ORG, actor=_MAKER, **_valid())
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.override.requested"
    assert entry.meta["reason"] == "Board-approved pilot extension"
    assert entry.meta["expires_at"] == FUTURE.isoformat()


# ── Section 19: "independently approved" ──────────────────────────────────────────────

def _pending(requested_by=_MAKER.id, expires_at=FUTURE, status="requested"):
    return CommercialOverride(
        id=uuid.uuid4(), org_id=_ORG.id, override_type="manual_override",
        target_type="feature", target_key="feature.webhooks.live", reason="r",
        requested_by=requested_by, status=status, expires_at=expires_at)


def test_the_requester_cannot_approve_their_own_override():
    with pytest.raises(ValueError, match="Maker-checker violation"):
        admin_crud.approve_commercial_override(_Stub(), _pending(), _MAKER)


def test_a_null_requester_cannot_self_approve():
    """Fails closed, matching the commercial ledger's hardened maker-checker."""
    with pytest.raises(ValueError, match="Maker-checker violation"):
        admin_crud.approve_commercial_override(_Stub(), _pending(requested_by=None), _CHECKER)


def test_a_different_approver_succeeds_and_is_audited():
    session = _Stub()
    override = _pending()
    admin_crud.approve_commercial_override(session, override, _CHECKER)
    assert override.status == "approved"
    assert override.approver_id == _CHECKER.id
    assert override.decided_at is not None
    entry = next(o for o in session.added if isinstance(o, AuditLog))
    assert entry.action == "commercial.override.approved"
    assert entry.meta["requested_by"] == str(_MAKER.id)


def test_an_expired_override_cannot_be_approved():
    with pytest.raises(ValueError, match="already expired"):
        admin_crud.approve_commercial_override(_Stub(), _pending(expires_at=PAST), _CHECKER)


def test_an_already_decided_override_cannot_be_re_approved():
    with pytest.raises(ValueError, match="Cannot approve an override in status"):
        admin_crud.approve_commercial_override(_Stub(), _pending(status="approved"), _CHECKER)


# ── Expiry is enforced on READ, against a real database ───────────────────────────────

@needs_db
class TestOverrideExpiryAgainstPostgres:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"ovr-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            maker = User(org_id=org.id, full_name="Maker", email=f"m-{uuid.uuid4().hex[:8]}@t.test",
                        username=f"m{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            checker = User(org_id=org.id, full_name="Checker", email=f"c-{uuid.uuid4().hex[:8]}@t.test",
                          username=f"c{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add_all([maker, checker])
            db.commit()
            ids = SimpleNamespace(org=org, org_id=org.id, maker=maker, checker=checker)
            yield db, ids
            db.execute(text("DELETE FROM audit_logs WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM commercial_overrides WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM users WHERE org_id=:o"), {"o": org.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()

    def _approved(self, db, ids, *, expires_at):
        override = admin_crud.request_commercial_override(
            db, org=ids.org, actor=ids.maker, override_type="pilot_poc",
            target_type="feature", target_key="feature.webhooks.live",
            reason="Approved pilot", expires_at=expires_at)
        admin_crud.approve_commercial_override(db, override, ids.checker)
        db.commit()
        return override

    def test_an_approved_unexpired_override_is_active(self, ctx):
        db, ids = ctx
        self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert len(admin_crud.active_overrides(db, ids.org_id)) == 1

    def test_a_requested_but_unapproved_override_is_never_active(self, ctx):
        """An override only grants once a second person has approved it."""
        db, ids = ctx
        admin_crud.request_commercial_override(
            db, org=ids.org, actor=ids.maker, override_type="pilot_poc",
            target_type="feature", target_key="feature.webhooks.live",
            reason="Awaiting approval", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        db.commit()
        assert admin_crud.active_overrides(db, ids.org_id) == []

    def test_a_lapsed_override_stops_granting_at_read_time(self, ctx):
        """The heart of Section 19's automatic expiry: access stops the moment the expiry
        passes, WITHOUT waiting for the maintenance sweep to run."""
        db, ids = ctx
        override = self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        # Move the expiry into the past directly — no sweep has run, status is still "approved".
        db.execute(text("UPDATE commercial_overrides SET expires_at = :e WHERE id = :i"),
                  {"e": datetime.now(timezone.utc) - timedelta(minutes=1), "i": override.id})
        db.commit()
        assert db.get(CommercialOverride, override.id).status == "approved"
        assert admin_crud.active_overrides(db, ids.org_id) == [], \
            "a lapsed override must not grant, even before the sweep marks it expired"

    def test_the_sweep_marks_lapsed_overrides_expired_and_is_idempotent(self, ctx):
        db, ids = ctx
        override = self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        db.execute(text("UPDATE commercial_overrides SET expires_at = :e WHERE id = :i"),
                  {"e": datetime.now(timezone.utc) - timedelta(minutes=1), "i": override.id})
        db.commit()

        assert admin_crud.expire_lapsed_overrides(db) == 1
        db.commit()
        assert db.get(CommercialOverride, override.id).status == "expired"
        # Second run finds nothing — the rows already moved out of `approved`.
        assert admin_crud.expire_lapsed_overrides(db) == 0

    def test_expiry_is_audited(self, ctx):
        db, ids = ctx
        override = self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        db.execute(text("UPDATE commercial_overrides SET expires_at = :e WHERE id = :i"),
                  {"e": datetime.now(timezone.utc) - timedelta(minutes=1), "i": override.id})
        db.commit()
        admin_crud.expire_lapsed_overrides(db)
        db.commit()
        assert db.query(AuditLog).filter(
            AuditLog.action == "commercial.override.expired",
            AuditLog.target_id == str(override.id),
        ).count() == 1

    def test_overrides_are_tenant_scoped(self, ctx):
        """An override for one organization must never be visible to another."""
        db, ids = ctx
        self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert admin_crud.active_overrides(db, uuid.uuid4()) == []

    def test_active_overrides_can_be_filtered_by_target_type(self, ctx):
        db, ids = ctx
        self._approved(db, ids, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert len(admin_crud.active_overrides(db, ids.org_id, target_type="feature")) == 1
        assert admin_crud.active_overrides(db, ids.org_id, target_type="plan") == []
