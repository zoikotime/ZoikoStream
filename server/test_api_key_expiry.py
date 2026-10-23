"""API-key expiry: one strict rule, warnings that say how long is left, and reminders that are
sent once per threshold and retried only when nothing was delivered.

    crud.admin.key_status / key_lifecycle / key_summary   the canonical rule (UTC instants)
    GET /organization/developer                            rows + counts from that rule
    services/credential_lifecycle.sweep                    30/14/7/3/1-day reminders + expired

Pinned along the way: an expired key is never extended, never reactivated, never "active" in
the hour before the sweep records it; no email ever carries the secret, the prefix or the
verifier; and no endpoint authenticates an API key at all (DEV-005), so an expired key — like
every key — is refused.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app.crud import admin as admin_crud
from app.db import SessionLocal
from app.models import AuditLog, Organization, User
from app.security import create_access_token, hash_password
from app.services import bus
from app.services import credential_lifecycle as lifecycle

UTC = timezone.utc
DAY = timedelta(days=1)
H = timedelta(hours=1)
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


# ── outbound mail is always captured; the local .env may hold a live provider key ────────

class Mail:
    def __init__(self):
        self.calls, self.fail = [], False

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append(json or {})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated provider outage")
        return type("R", (), {"status_code": 200, "text": "{}", "raise_for_status": lambda s: None})()

    def subjects(self, prefix=""):
        return [c["subject"] for c in self.calls if c["subject"].startswith(prefix)]

    def to(self, prefix=""):
        return sorted(c["to"][0] for c in self.calls if c["subject"].startswith(prefix))


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    monkeypatch.setattr(email_mod.settings, "RESEND_API_KEY", "test-provider-key")
    box = Mail()
    with patch.object(email_mod.httpx, "post", box):
        yield box


class World:
    """One organization with an owner (org_admin), a second admin who mints keys, and a
    viewer who must never be mailed about credentials."""

    def __init__(self, label="A"):
        db = SessionLocal()
        try:
            org = Organization(name=f"Keys {label} {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org = org.id
            self.owner = self._user(db, "org_admin", "owner")
            self.dev = self._user(db, "org_admin", "dev")
            self.viewer = self._user(db, "viewer", "viewer")
            org.owner_user_id = self.owner[0]
            db.commit()
        finally:
            db.close()

    def _user(self, db, role, tag):
        email = f"{tag}-{uuid.uuid4().hex[:10]}@example.com"
        u = User(org_id=self.org, full_name=tag.title(), role=role, is_active=True, email=email,
                 username=f"k{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"),
                 email_verified=True)
        db.add(u)
        db.flush()
        return u.id, email

    def key(self, expires_in: timedelta | None, *, label="testing", revoked=False, created_by="dev"):
        """A key minted the normal way, then given an exact expiry relative to NOW."""
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org)
            creator = getattr(self, created_by)[0] if created_by else None
            created = admin_crud.create_api_key(db, org, label, created_by=creator)
            changes = {"expires_at": (NOW + expires_in).isoformat() if expires_in is not None else None,
                       "created_at": (NOW - 40 * DAY).isoformat()}
            admin_crud._mutate_key(db, org, created.id, changes)
            if revoked:
                admin_crud.revoke_api_key(db, org, created.id)
            return created
        finally:
            db.close()

    def record(self, key_id):
        db = SessionLocal()
        try:
            return admin_crud.get_api_key_record(db.get(Organization, self.org), key_id)
        finally:
            db.close()

    def set(self, key_id, **changes):
        db = SessionLocal()
        try:
            admin_crud._mutate_key(db, db.get(Organization, self.org), key_id, changes)
        finally:
            db.close()

    def remind(self, key_id, now=NOW):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org)
            return lifecycle.notify_expiring(db, org=org, record=admin_crud.get_api_key_record(org, key_id),
                                             now=now)
        finally:
            db.close()

    def client(self, who="owner"):
        db = SessionLocal()
        try:
            token = create_access_token(db.get(User, getattr(self, who)[0]), remember=False, db=db)
        finally:
            db.close()
        c = TestClient(m.app)
        c.headers["Authorization"] = f"Bearer {token}"
        return c

    def cleanup(self):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org)
            org.owner_user_id = None
            db.commit()
            db.query(AuditLog).filter(AuditLog.org_id == self.org).delete(synchronize_session=False)
            db.query(User).filter(User.org_id == self.org).delete(synchronize_session=False)
            db.query(Organization).filter(Organization.id == self.org).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()


@pytest.fixture
def w():
    world = World("A")
    try:
        yield world
    finally:
        world.cleanup()


@pytest.fixture
def other():
    world = World("B")
    try:
        yield world
    finally:
        world.cleanup()


def life(expires_in, now=NOW, **record):
    rec = {"id": "k", "label": "k", "prefix": "zk_live_xxxx", "created_at": (now - DAY).isoformat(),
           "expires_at": (now + expires_in).isoformat() if expires_in is not None else None, **record}
    return admin_crud.key_lifecycle(rec, now)


# ── 1–8, 24: the rule ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("left, status, warning", [
    (30 * DAY + timedelta(seconds=1), "active", None),          # > 30 days
    (30 * DAY, "expiring_soon", "notice"),                       # exactly 30 days
    (7 * DAY + timedelta(seconds=1), "expiring_soon", "notice"),
    (7 * DAY, "expiring_soon", "urgent"),                        # 7 days
    (DAY + timedelta(seconds=1), "expiring_soon", "urgent"),
    (DAY, "expiring_soon", "imminent"),                          # 1 day
    (4 * H, "expiring_soon", "imminent"),                        # under 24 hours
    (timedelta(seconds=1), "expiring_soon", "imminent"),
])
def test_a_usable_key_reads_by_exact_time_left(left, status, warning):
    out = life(left)
    assert (out["status"], out["expiry_warning"], out["usable"]) == (status, warning, True)
    assert out["seconds_until_expiry"] == int(left.total_seconds())


def test_the_exact_expiry_instant_is_already_expired():
    out = life(timedelta(0))
    assert out["status"] == "expired" and out["usable"] is False and out["expiry_warning"] is None


def test_an_expired_key_reads_expired_with_how_long_ago():
    out = life(-21 * DAY)
    assert out["status"] == "expired" and out["usable"] is False
    assert out["seconds_until_expiry"] == -21 * 86400


def test_a_revoked_key_is_revoked_whatever_its_expiry():
    assert life(60 * DAY, revoked=True)["status"] == "revoked"
    assert life(-DAY, revoked=True, status="revoked")["usable"] is False


def test_a_lapsed_key_still_stored_as_active_is_expired_before_the_sweep_records_it():
    # The sweep runs hourly; in between, the stored status still says "active".
    out = life(-timedelta(minutes=5), status="active")
    assert out["status"] == "expired" and out["usable"] is False


def test_an_expired_key_is_never_reactivated_by_a_later_expiry():
    assert life(90 * DAY, status="expired")["status"] == "expired"


def test_a_non_expiring_key_is_active_with_no_countdown():
    out = life(None)
    assert out == {"status": "active", "usable": True, "seconds_until_expiry": None, "expiry_warning": None}


def test_instants_compare_in_utc_whatever_offset_was_stored():
    midnight = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
    for stored in ("2026-10-16T05:30:00+05:30", "2026-10-16T00:00:00Z", "2026-10-16T00:00:00"):
        rec = {"id": "k", "expires_at": stored}
        assert admin_crud.key_status(rec, midnight) == "expired", stored          # now == expiry
        assert admin_crud.key_status(rec, midnight - timedelta(seconds=1)) == "active", stored


# ── 9, 17: authentication ───────────────────────────────────────────────────────────────

def test_an_expired_key_is_refused_by_the_api(w):
    expired = w.key(-DAY)
    fresh = w.key(60 * DAY)
    for raw in (expired.key, fresh.key):
        r = TestClient(m.app).get("/api/organization/developer", headers={"Authorization": f"Bearer {raw}"})
        # No endpoint authenticates an API key (DEV-005): every key — expired or not — is refused.
        assert r.status_code == 401


# ── 10: the console's rows and counts ───────────────────────────────────────────────────

def test_rows_and_counts_come_from_the_same_rule(w, other):
    ids = {
        "far": w.key(60 * DAY, label="far").id,
        "soon": w.key(20 * DAY, label="soon").id,
        "urgent": w.key(5 * DAY, label="urgent").id,
        "expired": w.key(-21 * DAY, label="expired").id,
        "revoked": w.key(60 * DAY, label="revoked", revoked=True).id,
        "forever": w.key(None, label="forever").id,
    }
    other.key(5 * DAY, label="theirs")
    # NOW is fixed in the past of the real clock only in name: shift every key onto the real
    # clock so the route (which reads the real time) sees the same distances.
    shift = datetime.now(UTC) - NOW
    for kid in ids.values():
        rec = w.record(kid)
        if rec.get("expires_at"):
            w.set(kid, expires_at=(admin_crud._parse_ts(rec["expires_at"]) + shift).isoformat())
    body = w.client().get("/api/organization/developer").json()
    rows = {k["label"]: k for k in body["api_keys"]}
    assert set(rows) == {"far", "soon", "urgent", "expired", "revoked", "forever"}     # not B's
    assert {name: rows[name]["status"] for name in rows} == {
        "far": "active", "soon": "expiring_soon", "urgent": "expiring_soon",
        "expired": "expired", "revoked": "revoked", "forever": "active"}
    assert rows["urgent"]["expiry_warning"] == "urgent" and rows["soon"]["expiry_warning"] == "notice"
    assert body["key_summary"] == {"total": 6, "active": 4, "expiring_30d": 2, "expiring_7d": 1,
                                   "expired": 1, "revoked": 1, "non_expiring": 1}
    for row in body["api_keys"]:
        assert "key_hash" not in row and "key" not in row and "created_by" not in row


# ── 14–18: one reminder per threshold ───────────────────────────────────────────────────

@pytest.mark.parametrize("days, subject", [
    (30, "API key expires in 30 days"),
    (14, "API key expires in 14 days"),
    (7, "API key expires in 7 days"),
    (3, "API key expires in 3 days"),
    (1, "API key expires in 23 hours"),
])
def test_each_threshold_sends_its_reminder(w, mail, days, subject):
    # Just inside the window, as an hourly sweep finds it.
    key = w.key(days * DAY - 30 * timedelta(minutes=1))
    assert w.remind(key.id) is True
    assert mail.subjects("API key expires") == [subject] * len(mail.calls)
    assert w.record(key.id)[lifecycle.reminder_marker(days)]


def test_recipients_are_the_admins_and_creator_never_a_viewer(w, mail):
    key = w.key(7 * DAY - H)
    w.remind(key.id)
    assert mail.to() == sorted([w.owner[1].lower(), w.dev[1].lower()])
    assert w.viewer[1].lower() not in mail.to()


def test_the_email_says_what_expires_when_where_and_never_the_secret(w, mail):
    key = w.key(7 * DAY - H)
    w.remind(key.id)
    msg = mail.calls[0]
    body = msg["text"] + msg["html"] + msg["subject"]
    assert "testing" in body                                       # safe label
    assert "IST" in msg["text"]                                    # exact time, zone stated
    assert "/organization/settings?tab=developer" in msg["text"]   # Developer settings
    rec = w.record(key.id)
    for secret in (key.key, key.key[:12], rec["key_hash"], key.prefix, "zk_live_"):
        assert secret not in body
    assert "not yet enforced" in msg["text"]                       # truthful about access


# ── 19–21: never twice, across workers, retried only when nothing arrived ───────────────

def test_the_same_reminder_is_never_sent_twice(w, mail):
    key = w.key(7 * DAY - H)
    assert w.remind(key.id) is True
    assert w.remind(key.id) is False
    assert w.remind(key.id, now=NOW + 2 * H) is False
    assert len(mail.subjects("API key expires")) == 2               # one each, owner + dev


def test_a_late_first_sight_sends_only_the_current_window_never_the_missed_ones(w, mail):
    key = w.key(5 * DAY)                                            # first seen inside 7 days
    assert w.remind(key.id) is True
    assert set(mail.subjects()) == {"API key expires in 5 days"}
    rec = w.record(key.id)
    assert rec[lifecycle.reminder_marker(30)] and rec[lifecycle.reminder_marker(14)]   # superseded
    assert w.remind(key.id, now=NOW + DAY) is False                 # nothing new until 3 days
    assert w.remind(key.id, now=NOW + 2 * DAY + H) is True          # 3-day window
    assert mail.subjects()[-1] == "API key expires in 3 days"


def test_two_workers_sweeping_at_once_send_one_reminder(w, mail):
    key = w.key(7 * DAY - H)
    barrier, results = threading.Barrier(2), []

    def worker():
        barrier.wait()
        results.append(w.remind(key.id))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True]
    assert len(mail.calls) == 2                                     # owner + dev, once


def test_a_worker_holding_a_stale_copy_cannot_claim_a_reminder_again(w, mail):
    # Two processes each loaded the organization BEFORE either claimed. The second's copy
    # still shows no marker; the claim must re-read the row under its lock, not trust that copy.
    key = w.key(7 * DAY - H)
    first, second = SessionLocal(), SessionLocal()
    try:
        org_1, org_2 = first.get(Organization, w.org), second.get(Organization, w.org)
        assert org_2.api_keys is not None                           # loaded, now stale-to-be
        marker = lifecycle.reminder_marker(7)
        assert admin_crud.claim_key_notification(first, org_1, key.id, marker) is True
        assert admin_crud.claim_key_notification(second, org_2, key.id, marker) is False
    finally:
        first.close()
        second.close()


def test_a_failed_delivery_changes_nothing_and_is_retried(w, mail):
    key = w.key(7 * DAY - H)
    before = w.record(key.id)
    mail.fail = True
    assert w.remind(key.id) is False
    after = w.record(key.id)
    assert after["expires_at"] == before["expires_at"] and admin_crud.key_status(after, NOW) == "active"
    assert not after.get(lifecycle.reminder_marker(7))              # released for a retry
    assert after[f"{lifecycle.reminder_marker(7)}_failures"] == 1
    mail.fail = False
    assert w.remind(key.id, now=NOW + H) is True                    # the next sweep delivers
    assert w.remind(key.id, now=NOW + 2 * H) is False


def test_a_delivery_that_keeps_failing_is_abandoned_not_retried_for_ever(w, mail):
    key = w.key(7 * DAY - H)
    mail.fail = True
    for attempt in range(lifecycle.MAX_SEND_ATTEMPTS):
        assert w.remind(key.id, now=NOW + attempt * H) is False
    calls = len(mail.calls)
    assert w.remind(key.id, now=NOW + 10 * H) is False
    assert len(mail.calls) == calls                                 # no further attempt
    assert admin_crud.key_status(w.record(key.id), NOW) == "active"


# ── the sweep end to end ────────────────────────────────────────────────────────────────

def test_the_sweep_records_a_lapse_and_announces_it_once(w, mail):
    key = w.key(-H)
    db = SessionLocal()
    try:
        lifecycle.sweep(db, now=NOW)
        lifecycle.sweep(db, now=NOW + H)
    finally:
        db.close()
    rec = w.record(key.id)
    assert rec["status"] == "expired" and rec["expires_at"] == (NOW - H).isoformat()   # not extended
    ours = {w.owner[1].lower(), w.dev[1].lower()}
    mine = [c for c in mail.calls if c["to"][0] in ours]
    assert [c["subject"] for c in mine] == ["API key expired"] * 2                  # owner + dev, once
    assert "no access was withdrawn" in mine[0]["text"]


def test_a_long_past_lapse_is_recorded_but_not_mailed_months_late(w, mail):
    key = w.key(-90 * DAY)
    db = SessionLocal()
    try:
        lifecycle.sweep(db, now=NOW)
    finally:
        db.close()
    assert w.record(key.id)["status"] == "expired"
    assert not [c for c in mail.calls if w.owner[1].lower() in c["to"]]


def test_revoked_and_non_expiring_keys_get_no_reminders(w, mail):
    w.key(3 * DAY, revoked=True)
    w.key(None)
    db = SessionLocal()
    try:
        lifecycle.sweep(db, now=NOW)
    finally:
        db.close()
    assert not [c for c in mail.calls if w.owner[1].lower() in c["to"]]


def test_keys_warned_by_the_old_single_warning_are_not_warned_again_at_seven_days(w, mail):
    key = w.key(5 * DAY)
    w.set(key.id, dev_003_warning_notified_at=(NOW - DAY).isoformat())
    assert w.remind(key.id) is False
    assert w.remind(key.id, now=NOW + 2 * DAY + H) is True          # the 3-day one still comes


# ── 23: organization isolation ──────────────────────────────────────────────────────────

def test_a_reminder_reaches_only_its_own_organization(w, other, mail):
    key = w.key(7 * DAY - H)
    w.remind(key.id)
    for address in (other.owner[1], other.dev[1], other.viewer[1]):
        assert address.lower() not in mail.to()


def test_a_creator_outside_the_organization_is_not_mailed(w, other, mail):
    key = w.key(7 * DAY - H)
    w.set(key.id, created_by=str(other.dev[0]))                     # e.g. staff of another org
    w.remind(key.id)
    assert mail.to() == sorted([w.owner[1].lower(), w.dev[1].lower()])
    assert other.dev[1].lower() not in mail.to()
