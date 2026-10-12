"""Audience attendance: what is measured, what is estimated, and what is simply absent.

The Audience page renders "—" for several figures during an active session, and the question
these tests answer is which of those are DEFECTS and which are the product rule.

They are the rule, and the rule is a privacy decision:

  show_rate            real, but only for PRIVATE events. `EventRegistration.claimed_at` is
                       set only when an invited viewer opens a private event's watch page, so
                       a public event produces no "registered but did not show" signal at all.
                       None when no private registration exists in the window — NOT 0, which
                       would assert that invited people were measured and none turned up.
  avg_watch_minutes    None when nothing was ever sampled; a measured 0.0 is a different
                       fact and must survive as 0.0.
  unique_attendees     an ESTIMATE, flagged as one, because no table records a specific
                       person's watch duration once a room ends.

Geography, device/player mix and blocked join attempts are not covered here because the
backend does not collect them at all — they are hardcoded as unavailable in the console's
"Not measured" panel, with a stated reason each. Adding them would mean adding per-viewer
tracking this platform deliberately does not do.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker


@compiles(PGUUID, "sqlite")
def _pg_uuid_on_sqlite(type_, compiler, **kw):
    return "CHAR(36)"


from app.db import Base
import app.models  # noqa: F401
from app.models.event import Event, EventRegistration
from app.models.live import AnalyticsSnapshot, BroadcastSession
from app.models.organization import Organization
from app.services import org as org_svc

UTC = timezone.utc


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'audience.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.services import bus
    monkeypatch.setattr(bus, "summary_get_sync", lambda _e: None)
    monkeypatch.setattr(bus, "session_get_all_sync", lambda _e: [])
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def org(db):
    row = Organization(id=uuid.uuid4(), name="Acme", slug="acme")
    db.add(row)
    db.commit()
    return row


def _now():
    return datetime.now(UTC)


def make_event(db, org, title, *, visibility="public", start=None):
    start = start or (_now() - timedelta(days=2))
    ev = Event(id=uuid.uuid4(), org_id=org.id, created_by=uuid.uuid4(), title=title,
               start_time=start, end_time=start + timedelta(hours=1),
               status="ended", visibility=visibility)
    db.add(ev)
    return ev


def register(db, ev, email, *, claimed):
    db.add(EventRegistration(id=uuid.uuid4(), event_id=ev.id, name=email.split("@")[0],
                             email=email, claimed_at=_now() if claimed else None))


def sample(db, ev, *, ticks, viewers):
    """`ticks` sampler rows at `viewers` concurrent — the only audience signal that persists."""
    start = ev.start_time
    db.add(BroadcastSession(id=uuid.uuid4(), event_id=ev.id, org_id=ev.org_id, status="ended",
                            started_at=start, ended_at=start + timedelta(minutes=30),
                            peak_viewers=viewers))
    for i in range(ticks):
        db.add(AnalyticsSnapshot(id=uuid.uuid4(), event_id=ev.id, org_id=ev.org_id,
                                 viewers=viewers, participants=viewers, on_stage=1,
                                 messages=0, questions=0, reactions=0, hands=0,
                                 created_at=start + timedelta(seconds=15 * i)))


def attendance(db, org):
    return org_svc.audience_attendance(db, org, range_key="30d")


# ── show rate: absent is not zero ───────────────────────────────────────────────────────

def test_a_public_event_reports_no_show_rate_at_all(db, org):
    """The reported screenshot. A public event has no claim signal, so the honest answer is
    "not applicable" — and 0% would libel the audience by claiming nobody turned up."""
    ev = make_event(db, org, "Public launch", visibility="public")
    register(db, ev, "someone@example.com", claimed=False)
    sample(db, ev, ticks=20, viewers=3)
    db.commit()

    out = attendance(db, org)
    assert out["show_rate"] is None
    assert out["show_rate"] != 0


def test_a_private_event_with_claims_reports_a_real_percentage(db, org):
    ev = make_event(db, org, "Invite only", visibility="private")
    register(db, ev, "a@example.com", claimed=True)
    register(db, ev, "b@example.com", claimed=True)
    register(db, ev, "c@example.com", claimed=False)
    register(db, ev, "d@example.com", claimed=False)
    db.commit()

    assert attendance(db, org)["show_rate"] == 50


def test_a_private_event_nobody_attended_reports_a_measured_zero(db, org):
    """Here 0 IS the measurement: invitations were claimed by nobody. It must not collapse
    into the same "—" that means "we never had this signal"."""
    ev = make_event(db, org, "Invite only", visibility="private")
    register(db, ev, "a@example.com", claimed=False)
    register(db, ev, "b@example.com", claimed=False)
    db.commit()

    assert attendance(db, org)["show_rate"] == 0


def test_public_registrations_never_dilute_a_private_rate(db, org):
    """A public event's registrations carry no claim signal, so counting them would drag a
    real private rate downwards and look like a drop in attendance."""
    private = make_event(db, org, "Invite only", visibility="private")
    register(db, private, "a@example.com", claimed=True)
    register(db, private, "b@example.com", claimed=False)

    public = make_event(db, org, "Open", visibility="public")
    for i in range(10):
        register(db, public, f"p{i}@example.com", claimed=False)
    db.commit()

    assert attendance(db, org)["show_rate"] == 50


def test_the_basis_is_stated_so_the_console_can_caveat_it(db, org):
    make_event(db, org, "Open", visibility="public")
    db.commit()
    assert attendance(db, org)["show_rate_basis"] == "private_invited_events_only"


# ── watch time: never-sampled is not zero ───────────────────────────────────────────────

def test_an_unsampled_window_reports_no_average_watch_time(db, org):
    ev = make_event(db, org, "Never sampled", visibility="public")
    register(db, ev, "a@example.com", claimed=False)
    db.commit()
    assert attendance(db, org)["avg_watch_minutes"] is None


def test_a_sampled_window_reports_a_real_average(db, org):
    ev = make_event(db, org, "Sampled", visibility="public")
    sample(db, ev, ticks=40, viewers=10)
    db.commit()

    out = attendance(db, org)
    assert out["avg_watch_minutes"] is not None
    assert out["avg_watch_minutes"] > 0


def test_estimated_figures_are_flagged_as_estimates(db, org):
    """The console keys its "estimated from sampled concurrent viewers" caveat off these."""
    ev = make_event(db, org, "Sampled", visibility="public")
    sample(db, ev, ticks=20, viewers=4)
    db.commit()

    out = attendance(db, org)
    assert out["unique_attendees_estimated"] is True
    assert out["avg_watch_minutes_estimated"] is True


def test_returning_attendees_is_a_real_count_and_can_be_zero(db, org):
    """One claimed email at one event is not a returning attendee. 0 is measured here."""
    ev = make_event(db, org, "Invite only", visibility="private")
    register(db, ev, "a@example.com", claimed=True)
    db.commit()
    assert attendance(db, org)["returning"] == 0


def test_an_email_claimed_at_two_events_counts_as_returning(db, org):
    first = make_event(db, org, "One", visibility="private")
    second = make_event(db, org, "Two", visibility="private")
    register(db, first, "repeat@example.com", claimed=True)
    register(db, second, "repeat@example.com", claimed=True)
    db.commit()
    assert attendance(db, org)["returning"] == 1


# ── isolation and windowing ─────────────────────────────────────────────────────────────

def test_one_organization_never_sees_another_organizations_audience(db, org):
    rival = Organization(id=uuid.uuid4(), name="Rival", slug="rival")
    db.add(rival)

    mine = make_event(db, org, "Mine", visibility="private")
    register(db, mine, "a@example.com", claimed=True)
    register(db, mine, "b@example.com", claimed=False)

    theirs = make_event(db, rival, "Theirs", visibility="private")
    for i in range(20):
        register(db, theirs, f"t{i}@example.com", claimed=True)
    db.commit()

    assert attendance(db, org)["show_rate"] == 50
    # ...and symmetrically, so this cannot pass by returning nothing at all.
    assert attendance(db, rival)["show_rate"] == 100


def test_an_event_outside_the_window_is_excluded(db, org):
    old = make_event(db, org, "Old", visibility="private",
                     start=_now() - timedelta(days=60))
    register(db, old, "a@example.com", claimed=True)
    db.commit()

    assert org_svc.audience_attendance(db, org, range_key="30d")["show_rate"] is None
    assert org_svc.audience_attendance(db, org, range_key="90d")["show_rate"] == 100
