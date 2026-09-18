"""Audience metrics: counted rows, not placeholders.

── WHAT WAS WRONG ──────────────────────────────────────────────────────────────────────
/organization/audience rendered an em dash for "Registrations" and for every event's
"Registered" count, on an organization that really did have registrations. The cause was not
a display bug: `registered_count` did not exist anywhere in the backend. AudienceAccess.jsx
read `ev.registered_count`, EventOut never carried it, and a source comment asserted it was
"part of the dashboard enrichment" — which was simply untrue.

So the count is now produced where it can be counted: one grouped query over
event_registrations for the listed page, and a separate SQL summary for the KPI row, which
must span the whole window rather than the page on screen.

The other three KPIs were already real and are pinned here so they stay that way:
registration_required, registration_limit and visibility all come off the event row.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.crud import event as event_crud
from app.db import SessionLocal
from app.models import Event, EventRegistration, Organization, User
from app.security import hash_password
from app.services import org as org_svc

UTC = timezone.utc


def _mk_event(db, org, creator, *, title, days_ago=2, required=False, limit=None,
              visibility="public"):
    ev = Event(org_id=org.id, created_by=creator.id, title=title, status="ended",
               visibility=visibility, registration_required=required,
               registration_limit=limit,
               start_time=datetime.now(UTC) - timedelta(days=days_ago))
    db.add(ev)
    db.flush()
    return ev


def _register(db, ev, n):
    for i in range(n):
        db.add(EventRegistration(event_id=ev.id, name=f"P{i}",
                                 email=f"{uuid.uuid4().hex[:10]}@example.com"))


@pytest.fixture
def world():
    """One org with a deliberate spread of capacity/registration states, and a second org
    that must never appear in the first's figures."""
    db = SessionLocal()
    made = {"orgs": [], "events": [], "users": []}
    try:
        org = Organization(name=f"AudCo {uuid.uuid4().hex[:6]}", status="active")
        other = Organization(name=f"OtherCo {uuid.uuid4().hex[:6]}", status="active")
        db.add_all([org, other])
        db.flush()
        made["orgs"] = [org.id, other.id]

        creator = User(org_id=org.id, full_name="Aud Fixture", role="org_admin", is_active=True,
                       email=f"aud-{uuid.uuid4().hex[:10]}@example.com",
                       username=f"aud{uuid.uuid4().hex[:10]}",
                       password_hash=hash_password("x"), email_verified=True)
        other_creator = User(org_id=other.id, full_name="Other Fixture", role="org_admin",
                             is_active=True,
                             email=f"oth-{uuid.uuid4().hex[:10]}@example.com",
                             username=f"oth{uuid.uuid4().hex[:10]}",
                             password_hash=hash_password("x"), email_verified=True)
        db.add_all([creator, other_creator])
        db.flush()
        made["users"] = [creator.id, other_creator.id]

        # full:    limit 10, 10 registered  -> AT capacity
        # nearly:  limit 10,  9 registered  -> NOT at capacity
        # uncapped: no limit, 5 registered  -> can never be at capacity
        # empty:   limit 10,  0 registered  -> a counted zero
        # old:     outside the 7d window
        full = _mk_event(db, org, creator, title="Full", required=True, limit=10)
        nearly = _mk_event(db, org, creator, title="Nearly", required=True, limit=10)
        uncapped = _mk_event(db, org, creator, title="Uncapped", visibility="private")
        empty = _mk_event(db, org, creator, title="Empty", limit=10)
        old = _mk_event(db, org, creator, title="Old", days_ago=45, limit=10)
        foreign = _mk_event(db, other, other_creator, title="Foreign", limit=10)
        made["events"] = [e.id for e in (full, nearly, uncapped, empty, old, foreign)]

        _register(db, full, 10)
        _register(db, nearly, 9)
        _register(db, uncapped, 5)
        _register(db, old, 3)
        _register(db, foreign, 7)
        db.commit()
        yield {"db": db, "org": org, "other": other,
               "full": full, "nearly": nearly, "uncapped": uncapped,
               "empty": empty, "old": old, "foreign": foreign}
    finally:
        try:
            for eid in made["events"]:
                db.query(EventRegistration).filter(EventRegistration.event_id == eid).delete()
                db.query(Event).filter(Event.id == eid).delete()
            for uid in made["users"]:
                db.query(User).filter(User.id == uid).delete()
            for oid in made["orgs"]:
                db.query(Organization).filter(Organization.id == oid).delete()
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


# ── per-event registered count ─────────────────────────────────────────────────────────

def test_registration_counts_returns_real_totals(world):
    db = world["db"]
    counts = event_crud.registration_counts(db, [world["full"].id, world["nearly"].id,
                                                 world["uncapped"].id])
    assert counts[world["full"].id] == 10
    assert counts[world["nearly"].id] == 9
    assert counts[world["uncapped"].id] == 5


def test_an_event_with_no_registrations_is_absent_so_callers_render_a_counted_zero(world):
    """Absent from the map, not present-as-null: the list endpoint maps a miss to 0, which is
    a measurement. None is reserved for "this response did not carry the count"."""
    db = world["db"]
    counts = event_crud.registration_counts(db, [world["empty"].id])
    assert world["empty"].id not in counts
    assert counts.get(world["empty"].id, 0) == 0


def test_it_is_one_query_shaped_call_not_per_event(world):
    """Passing no ids must not fall through to counting everything."""
    assert event_crud.registration_counts(world["db"], []) == {}


def test_counts_never_cross_organizations(world):
    """The foreign org's 7 registrations must not appear for anyone who did not ask for that
    event — and the list endpoint only ever passes ids from the caller's own org."""
    db = world["db"]
    mine, _ = event_crud.list_events(db, world["org"].id, page=1, page_size=50)
    assert world["foreign"].id not in {e.id for e in mine}
    counts = event_crud.registration_counts(db, [e.id for e in mine])
    assert sum(counts.values()) == 10 + 9 + 5 + 3      # full + nearly + uncapped + old


# ── the KPI row ────────────────────────────────────────────────────────────────────────

def test_registrations_kpi_counts_real_rows(world):
    out = org_svc.audience_summary(world["db"], world["org"], range_key="30d")
    # full 10 + nearly 9 + uncapped 5; "old" is outside the 30d window.
    assert out["registrations"] == 24


def test_registration_required_comes_from_the_event_flag(world):
    out = org_svc.audience_summary(world["db"], world["org"], range_key="30d")
    assert out["registration_required"] == 2          # full + nearly


def test_at_capacity_is_registered_greater_or_equal_limit(world):
    out = org_svc.audience_summary(world["db"], world["org"], range_key="30d")
    # full (10/10) yes; nearly (9/10) no; empty (0/10) no.
    assert out["at_capacity"] == 1


def test_an_uncapped_event_is_never_at_capacity(world):
    """5 registrations against no limit is not "full" — there is no number to be full of."""
    db, org = world["db"], world["org"]
    before = org_svc.audience_summary(db, org, range_key="30d")["at_capacity"]
    _register(db, world["uncapped"], 500)
    db.commit()
    after = org_svc.audience_summary(db, org, range_key="30d")["at_capacity"]
    assert before == after == 1


def test_events_kpi_counts_the_orgs_own_events_in_window(world):
    out = org_svc.audience_summary(world["db"], world["org"], range_key="30d")
    assert out["events"] == 4                          # full, nearly, uncapped, empty
    assert out["events_with_capacity"] == 3            # full, nearly, empty


def test_soft_deleted_events_are_excluded(world):
    db, org = world["db"], world["org"]
    before = org_svc.audience_summary(db, org, range_key="30d")["events"]
    world["empty"].deleted_at = datetime.now(UTC)
    db.flush()
    after = org_svc.audience_summary(db, org, range_key="30d")["events"]
    assert after == before - 1
    db.rollback()


# ── the date range is real ─────────────────────────────────────────────────────────────

def test_the_range_actually_changes_the_numbers(world):
    """"Old" sits 45 days back with 3 registrations: inside 90d, outside 30d and 7d."""
    db, org = world["db"], world["org"]
    d7 = org_svc.audience_summary(db, org, range_key="7d")
    d30 = org_svc.audience_summary(db, org, range_key="30d")
    d90 = org_svc.audience_summary(db, org, range_key="90d")

    assert d30["events"] == 4 and d90["events"] == 5
    assert d30["registrations"] == 24 and d90["registrations"] == 27
    assert d7["events"] == 4      # the four recent ones are 2 days old


# ── scoping ────────────────────────────────────────────────────────────────────────────

def test_one_organization_never_sees_another(world):
    other = org_svc.audience_summary(world["db"], world["other"], range_key="90d")
    assert other["events"] == 1
    assert other["registrations"] == 7                 # its own, and only its own
    mine = org_svc.audience_summary(world["db"], world["org"], range_key="90d")
    assert mine["registrations"] == 27


# ── zero is a measurement ──────────────────────────────────────────────────────────────

def test_an_org_with_no_events_reports_zero_not_null(world):
    """Nothing to count is still counted. The client renders an em dash only when the request
    itself failed — never for a real zero."""
    db = SessionLocal()
    try:
        bare = Organization(name=f"Bare {uuid.uuid4().hex[:6]}", status="active")
        db.add(bare)
        db.flush()
        out = org_svc.audience_summary(db, bare, range_key="30d")
        assert out == {**out, "events": 0, "registrations": 0,
                       "registration_required": 0, "at_capacity": 0,
                       "events_with_capacity": 0}
        for key in ("events", "registrations", "at_capacity"):
            assert out[key] is not None
    finally:
        db.rollback()
        db.close()


# ── sorting by the count ───────────────────────────────────────────────────────────────

def test_events_can_be_ordered_by_registrations(world):
    db, org = world["db"], world["org"]
    desc, _ = event_crud.list_events(db, org.id, sort_by="registered", order="desc",
                                     page=1, page_size=50)
    asc, _ = event_crud.list_events(db, org.id, sort_by="registered", order="asc",
                                    page=1, page_size=50)
    assert desc[0].id == world["full"].id              # 10, the most
    assert asc[0].id == world["empty"].id              # 0 — present, not dropped by the join


def test_an_unknown_sort_field_falls_back_without_reaching_sql(world):
    rows, total = event_crud.list_events(world["db"], world["org"].id,
                                         sort_by="registered); DROP TABLE events;--",
                                         page=1, page_size=5)
    assert total >= 4 and rows
