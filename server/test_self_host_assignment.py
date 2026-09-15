"""Assigning yourself as host of an event you just created.

── WHAT THIS PLATFORM ALREADY DID ───────────────────────────────────────────────────────
EventAssignment carries no status and no accepted_at: the row IS the grant, for everyone.
PATCH /events/{id}/hosts writes those rows through crud.set_assignees and they are live the
moment they exist — there is no pending state, no invitation token and no acceptance step
for ANY host, self or otherwise. These tests pin that, because the change below must not be
allowed to grow a second, parallel assignment lifecycle next to it.

── WHAT CHANGED ─────────────────────────────────────────────────────────────────────────
Only the words. A creator who puts themselves on the host list now receives a confirmation
("You're hosting X") instead of the third-person notification written for someone told about
a decision made elsewhere ("You've been assigned"). Same write, same dedup, same timing.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import email as email_mod
from app import ratelimit
from app.db import SessionLocal
from app.main import app
from app.models import Event, EventAssignment, Organization, User
from app.security import hash_password
from app.services import moderation

client = TestClient(app)

PASSWORD = "correct-horse-battery"

SELF_SUBJECT = "You're hosting"
OTHER_SUBJECT = "You've been added as host"


class Captured:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json or {})
        class _Resp:
            status_code = 200
            text = "{}"
            def raise_for_status(self):
                return None
        return _Resp()

    @property
    def subjects(self):
        return [c.get("subject", "") for c in self.calls]

    def to(self, address):
        return [c for c in self.calls if address in (c.get("to") or [])]

    def host_mail_to(self, address):
        """Only the per-assignee HOST family — the confirmation or the assignment notice.

        Deliberately not every message: crud-side, _set_role also calls event_comms.
        notify_team, which sends the LVE-003 "your event team" announcement to the whole
        team. That is a different family with a different audience and predates this work;
        counting it here would make "exactly one host email" fail for a reason that has
        nothing to do with host assignment.
        """
        return [c for c in self.to(address)
                if c.get("subject", "").startswith((SELF_SUBJECT, OTHER_SUBJECT))]


def capture():
    cap = Captured()
    return cap, patch.object(email_mod.httpx, "post", cap)


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    ratelimit._HITS.clear()
    yield


class World:
    """One organization, its creating admin, and a second member to hand a host role to."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"HostCo {uuid.uuid4().hex[:6]}", status="active")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.creator_email, self.creator_id = self._member(db, "org_admin", "vihari")
            self.other_email, self.other_id = self._member(db, "host", "nani")
            self.viewer_email, self.viewer_id = self._member(db, "viewer", "watcher")
            org.owner_user_id = self.creator_id
            db.commit()
        finally:
            db.close()

    def _member(self, db, role, tag):
        email = f"{tag}-{uuid.uuid4().hex[:10]}@example.com"
        user = User(org_id=self.org_id, full_name=tag.title(), role=role, is_active=True,
                    email=email, username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True)
        db.add(user)
        db.flush()
        return email, user.id

    def token(self, email):
        ratelimit._HITS.clear()
        with capture()[1]:
            r = client.post("/api/auth/login", json={"identifier": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    def headers(self, email):
        return {"Authorization": f"Bearer {self.token(email)}"}

    def cleanup(self):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
                db.commit()
            for ev in db.scalars(select(Event).where(Event.org_id == self.org_id)).all():
                db.query(EventAssignment).filter(EventAssignment.event_id == ev.id).delete()
                db.delete(ev)
            db.commit()
            db.query(User).filter(User.org_id == self.org_id).delete()
            db.commit()
            if org is not None:
                db.delete(org)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World()
    try:
        yield world
    finally:
        world.cleanup()


def create_event(w, title="Launch", status="scheduled"):
    with capture()[1]:
        r = client.post("/api/events", json={"title": title, "status": status},
                        headers=w.headers(w.creator_email))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def set_hosts(w, event_id, user_ids):
    """PATCH the host list and return the captured outbound mail."""
    cap, ctx = capture()
    with ctx:
        r = client.patch(f"/api/events/{event_id}/hosts",
                         json={"user_ids": [str(u) for u in user_ids]},
                         headers=w.headers(w.creator_email))
    assert r.status_code == 200, r.text
    return r.json(), cap


def assignments(event_id, role="host"):
    db = SessionLocal()
    try:
        return db.scalars(
            select(EventAssignment).where(EventAssignment.event_id == event_id,
                                          EventAssignment.role == role)
        ).all()
    finally:
        db.close()


# ── Scenario 1: the creator hosts it themselves ────────────────────────────────────────

def test_self_assignment_creates_a_live_host_row(w):
    event_id = create_event(w)
    body, _cap = set_hosts(w, event_id, [w.creator_id])

    rows = assignments(event_id)
    assert len(rows) == 1
    assert rows[0].user_id == w.creator_id
    assert rows[0].role == "host"
    assert [u["id"] for u in body] == [str(w.creator_id)]


def test_self_assignment_needs_no_acceptance(w):
    """There is no pending state to leave and no token to redeem — for anyone. The model
    carries neither, and this asserts that rather than trusting the comment."""
    event_id = create_event(w)
    set_hosts(w, event_id, [w.creator_id])

    row = assignments(event_id)[0]
    for absent in ("status", "accepted_at", "accepted", "invitation_token", "pending"):
        assert not hasattr(row, absent), (
            f"EventAssignment grew {absent!r} — a second host lifecycle is being built")

    # The grant is readable immediately, with no intervening step.
    db = SessionLocal()
    try:
        ctx = moderation.resolve_ctx(uuid.UUID(event_id), db.get(User, w.creator_id))
    finally:
        db.close()
    assert ctx is not None and ctx.can_host is True


def test_self_assignment_sends_exactly_one_confirmation(w):
    event_id = create_event(w)
    _body, cap = set_hosts(w, event_id, [w.creator_id])

    mine = cap.host_mail_to(w.creator_email)
    assert len(mine) == 1, cap.subjects
    assert mine[0]["subject"].startswith(SELF_SUBJECT), cap.subjects
    assert "Launch" in mine[0]["subject"]
    # NOT the third-person assignment copy.
    assert not any(c["subject"].startswith(OTHER_SUBJECT) for c in cap.to(w.creator_email)), cap.subjects


def test_the_confirmation_asks_for_nothing(w):
    event_id = create_event(w)
    _body, cap = set_hosts(w, event_id, [w.creator_id])
    mail = cap.host_mail_to(w.creator_email)[0]
    body = mail["html"] + mail.get("text", "")

    for demand in ("accept", "Accept", "activate", "verify", "confirm your"):
        assert demand not in body, f"self-host mail asked the creator to {demand!r}"
    # It does carry a way back in, and that link carries no credential.
    assert f"/host/dashboard?event={event_id}" in body
    assert "token=" not in body


# ── Scenario 2: somebody else hosts it ─────────────────────────────────────────────────

def test_another_member_keeps_the_existing_methodology(w):
    event_id = create_event(w)
    _body, cap = set_hosts(w, event_id, [w.other_id])

    rows = assignments(event_id)
    assert len(rows) == 1 and rows[0].user_id == w.other_id

    theirs = cap.host_mail_to(w.other_email)
    assert len(theirs) == 1, [c["subject"] for c in theirs]
    assert theirs[0]["subject"].startswith(OTHER_SUBJECT), theirs[0]["subject"]
    # The creator is not on the host list, so nothing host-shaped reaches them.
    assert cap.host_mail_to(w.creator_email) == []


# ── Scenario 3: both ───────────────────────────────────────────────────────────────────

def test_creator_and_another_each_get_exactly_one_right_email(w):
    event_id = create_event(w)
    _body, cap = set_hosts(w, event_id, [w.creator_id, w.other_id])

    assert {r.user_id for r in assignments(event_id)} == {w.creator_id, w.other_id}

    mine = cap.host_mail_to(w.creator_email)
    theirs = cap.host_mail_to(w.other_email)
    assert len(mine) == 1 and mine[0]["subject"].startswith(SELF_SUBJECT), [c["subject"] for c in mine]
    assert len(theirs) == 1 and theirs[0]["subject"].startswith(OTHER_SUBJECT), [c["subject"] for c in theirs]


# ── no duplicates, however many times it is saved ──────────────────────────────────────

def test_resaving_the_same_hosts_writes_and_sends_nothing_new(w):
    event_id = create_event(w)
    set_hosts(w, event_id, [w.creator_id, w.other_id])

    _body, cap = set_hosts(w, event_id, [w.creator_id, w.other_id])
    assert len(assignments(event_id)) == 2, "assignment duplicated on re-save"
    assert cap.host_mail_to(w.creator_email) == []
    assert cap.host_mail_to(w.other_email) == []


def test_a_repeated_user_id_yields_one_row(w):
    event_id = create_event(w)
    _body, cap = set_hosts(w, event_id, [w.creator_id, w.creator_id])
    assert len(assignments(event_id)) == 1
    assert len(cap.host_mail_to(w.creator_email)) == 1


# ── authorization is unchanged, and the redirect grants nothing ────────────────────────

def test_an_unassigned_member_of_another_org_is_refused(w):
    """The console resolves access on arrival. A URL is not a grant."""
    event_id = create_event(w)
    set_hosts(w, event_id, [w.creator_id])

    outsider = World()
    try:
        db = SessionLocal()
        try:
            ctx = moderation.resolve_ctx(uuid.UUID(event_id), db.get(User, outsider.creator_id))
        finally:
            db.close()
        # Different org: the event is not even visible, so the socket is refused outright.
        assert ctx is None
    finally:
        outsider.cleanup()


def test_a_viewer_in_the_org_cannot_host_and_cannot_publish(w):
    event_id = create_event(w)
    set_hosts(w, event_id, [w.creator_id])

    db = SessionLocal()
    try:
        ctx = moderation.resolve_ctx(uuid.UUID(event_id), db.get(User, w.viewer_id))
    finally:
        db.close()
    assert ctx is not None, "same org, so the event resolves"
    # …but hosting is not theirs. The publish token in services/broadcast.py is gated on
    # exactly this flag, so a false here is what keeps canPublish off for a viewer.
    assert ctx.can_host is False


def test_assigning_a_non_member_is_refused(w):
    event_id = create_event(w)
    outsider = World()
    try:
        with capture()[1]:
            r = client.patch(f"/api/events/{event_id}/hosts",
                             json={"user_ids": [str(outsider.creator_id)]},
                             headers=w.headers(w.creator_email))
        assert r.status_code == 400
        assert assignments(event_id) == []
    finally:
        outsider.cleanup()


def test_host_assignment_does_not_change_the_account_role(w):
    """Event role and account role stay separate: being made host of one event must not
    promote anybody on the organization."""
    event_id = create_event(w)
    set_hosts(w, event_id, [w.other_id])

    db = SessionLocal()
    try:
        assert db.get(User, w.other_id).role == "host"      # unchanged
        assert db.get(User, w.creator_id).role == "org_admin"
    finally:
        db.close()
