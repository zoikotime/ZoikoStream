"""ORG-011 / ORG-012 — data residency and notification preferences (ZST-EC-001).

Same harness as the earlier suites: Resend is intercepted at `httpx.post`, and a `_deny`
baseline fails any test whose send escapes its capture instead of reaching the provider.

ORG-011's tests assert that nothing is sent and nothing is claimed. There is no residency
subsystem — one DATABASE_URL, one unregioned bucket, and an `Organization.region` free-text
field used only for display — so the only correct behaviour is silence, and these tests are
what stop a later change from shipping a "your data was migrated" claim off that field.

Run with `python test_notification_prefs.py` (or pytest).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AccountRecovery,
    AuditLog,
    IdentityChallenge,
    Invitation,
    NotificationPreferenceEvent,
    Organization,
    OrgMembershipEvent,
    SignInEvent,
    User,
)
from app.security import hash_password
from app.services import notifications as notif_svc

PASSWORD = "correct-horse-battery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"

ORG_012 = email_mod.ORG_012_SUBJECT
ORG_002 = email_mod.ORG_002_SUBJECT
IDN_003 = email_mod.IDN_003_SUBJECT


# ── harness ─────────────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]

    def to(self, subject):
        return sorted(c["payload"]["to"][0]
                      for c in self.calls if c["payload"]["subject"] == subject)


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _assert_no_leak():
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


def _reset_limits():
    ratelimit._HITS.clear()


def _new_email(tag="prefs"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class Fixture:
    def __init__(self, tz="Asia/Kolkata"):
        self.emails: list[str] = []
        db = SessionLocal()
        try:
            org = Organization(name=f"Prefs Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone=tz, region="US East")
            db.add(org)
            db.flush()
            self.org_id, self.org_name = org.id, org.name
            self.owner_email = _new_email("owner")
            self.owner_id = self._add(db, "org_admin", self.owner_email, "Owner Person")
            self.admin_email = _new_email("admin")
            self.admin_id = self._add(db, "org_admin", self.admin_email, "Admin Person")
            org.owner_user_id = self.owner_id
            db.commit()
        finally:
            db.close()

    def _add(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        self.emails.append(email)
        return user.id

    def token(self, email=None):
        client = TestClient(m.app)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.post("/api/auth/login",
                            json={"identifier": email or self.admin_email,
                                  "password": PASSWORD},
                            headers={"User-Agent": UA})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def set_prefs(self, **prefs):
        db = SessionLocal()
        try:
            org = db.get(Organization, self.org_id)
            org.notifications = {**(org.notifications or {}), **prefs}
            db.commit()
        finally:
            db.close()

    def events(self):
        db = SessionLocal()
        try:
            return db.query(NotificationPreferenceEvent).filter(
                NotificationPreferenceEvent.org_id == self.org_id).all()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            db.query(NotificationPreferenceEvent).filter(
                NotificationPreferenceEvent.org_id == self.org_id).delete()
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(OrgMembershipEvent).filter(
                OrgMembershipEvent.org_id == self.org_id).delete()
            db.query(Invitation).filter(Invitation.org_id == self.org_id).delete()
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            for user in db.query(User).filter(User.org_id == self.org_id).all():
                for model in (SignInEvent, IdentityChallenge, AccountRecovery):
                    db.query(model).filter(model.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                db.delete(org)
                db.commit()
        finally:
            db.close()


# ══ ORG-011 — proof of absence ══════════════════════════════════════════════════

def test_011_no_residency_subsystem_exists():
    """3 — a single DATABASE_URL must not be presented as multi-region support."""
    import pathlib
    hits = []
    for path in pathlib.Path("app").rglob("*.py"):
        code = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                         if not l.lstrip().startswith("#"))
        for marker in ("residency_policy", "data_residency", "migration_job",
                       "storage_region", "backup_location", "residual_location",
                       "REPLICA_DATABASE_URL", "data_class"):
            if marker in code:
                hits.append(f"{path}:{marker}")
    assert not hits, f"residency infrastructure appeared; ORG-011 must be revisited: {hits}"

    from app.config import settings as cfg
    # One database, one bucket, and the bucket carries no region at all.
    assert hasattr(cfg, "DATABASE_URL")
    assert not any(n.startswith("REPLICA_") or n.endswith("_REGION")
                   for n in type(cfg).model_fields), \
        "a second storage region appeared; ORG-011 must be revisited"


def test_011_no_residency_templates_exist():
    """2 — no email may claim a migration occurred."""
    code = "\n".join(l for l in open(email_mod.__file__, encoding="utf-8").read().splitlines()
                     if not l.lstrip().startswith("#"))
    for claim in ("data-residency change scheduled", "data-residency change is in progress",
                  "data-residency change completed", "data-residency change was rolled back",
                  "residual data location", "your data was migrated",
                  "backups were relocated"):
        assert claim.lower() not in code.lower(), \
            f"{claim!r} must not exist without a real residency migration subsystem"


def test_011_region_field_is_cosmetic_and_sends_nothing():
    """1 — no cosmetic region field can trigger ORG-011."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        # Changing the org's region through the super-admin path must not produce mail
        # claiming anything moved. (It is a display label for incident blast radius.)
        db = SessionLocal()
        try:
            staff_org = Organization(name=f"Staff {uuid.uuid4().hex[:6]}", status="active")
            db.add(staff_org)
            db.flush()
            staff_email = _new_email("staff")
            staff = User(org_id=staff_org.id, full_name="Staff", role="super_admin",
                         is_active=True, email=staff_email.lower(),
                         username=f"u{uuid.uuid4().hex[:10]}",
                         password_hash=hash_password(PASSWORD), email_verified=True,
                         email_verified_at=datetime.now(timezone.utc))
            db.add(staff)
            db.commit()
            staff_org_id = staff_org.id
        finally:
            db.close()

        headers = fx.token(staff_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch(f"/api/admin/organizations/{fx.org_id}",
                             json={"region": "EU West (Ireland)"}, headers=headers)
        assert r.status_code == 200, r.text
        assert cap.calls == [], f"a region label change must send nothing; got {cap.subjects}"

        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).region == "EU West (Ireland)", \
                "the label changed, which is all it is"
            for user in db.query(User).filter(User.org_id == staff_org_id).all():
                db.query(SignInEvent).filter(SignInEvent.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            db.delete(db.get(Organization, staff_org_id))
            db.commit()
        finally:
            db.close()
    finally:
        fx.cleanup()


# ══ ORG-012 classification and enforcement ══════════════════════════════════════

def test_012_class_map_is_authoritative_and_fails_closed():
    """Message class comes from the register, never from a UI setting name."""
    for family in ("IDN-001", "IDN-003", "IDN-004", "IDN-005", "IDN-006", "IDN-007",
                   "IDN-008", "ORG-003", "ORG-004", "ORG-007", "ORG-008", "ORG-009",
                   "ORG-010"):
        assert notif_svc.message_class(family) == notif_svc.CLASS_A, f"{family} must be A"
        assert notif_svc.is_mandatory(family), f"{family} must be mandatory"
    # An unclassified family fails closed as Class A rather than silently becoming optional.
    assert notif_svc.message_class("XXX-999") == notif_svc.CLASS_A
    assert notif_svc.is_mandatory("XXX-999")


def test_012_17_18_class_a_ignores_preference_suppression():
    """17 (Class A ignores preferences), 18 (security_alerts=false cannot disable)."""
    fx = Fixture()
    try:
        fx.set_prefs(security_alerts=False, billing=False)
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            # Even with the legacy flag stored false, every mandatory family still sends.
            for family in ("IDN-003", "IDN-004", "IDN-005", "IDN-008", "ORG-003",
                           "ORG-004", "ORG-009", "ORG-010", "COM-001", "ORG-001"):
                assert notif_svc.should_send_operational_notification(
                    family=family, org=org) is True, f"{family} must not be suppressible"
            # And the normalizer pins it back to true on read.
            assert notif_svc.effective(org)["security_alerts"] is True
            assert notif_svc.effective(org)["billing"] is True
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_012_19_configurable_families_consult_preferences():
    """19 — Class B/C configurable notifications actually read the committed preferences."""
    fx = Fixture()
    try:
        db = SessionLocal()
        try:
            org = db.get(Organization, fx.org_id)
            fx.set_prefs(event_scheduled=True, member_joined=True)
            db.refresh(org)
            assert notif_svc.should_send_operational_notification(family="EVT-001", org=org)
            assert notif_svc.should_send_operational_notification(family="ORG-002", org=org)

            fx.set_prefs(event_scheduled=False, member_joined=False)
            db.refresh(org)
            assert not notif_svc.should_send_operational_notification(family="EVT-001",
                                                                     org=org)
            assert not notif_svc.should_send_operational_notification(family="ORG-002",
                                                                     org=org)
        finally:
            db.close()
    finally:
        fx.cleanup()


def test_012_20_marketing_stays_separate():
    """20 — marketing preferences are a separate domain, never mixed with operational."""
    assert notif_svc.MARKETING_PREFERENCE_KEYS == frozenset(), \
        "no marketing preference may live in the operational catalog"
    for entry in notif_svc.CATALOG:
        assert entry["key"] not in notif_svc.MARKETING_PREFERENCE_KEYS
        assert entry["message_class"] in ("A", "B", "C")


def test_012_org_002_suppression_is_real_end_to_end():
    """The preference genuinely stops a real send path, not just the helper."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        fx.set_prefs(member_joined=False)

        invitee = _new_email("invitee")
        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            r = client.post("/api/organization/invitations",
                            json={"email": invitee, "role": "viewer"}, headers=headers)
        assert r.status_code == 201, r.text
        raw = r.json()["invite_token"]

        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            a = client.post("/api/organization/invitations/accept",
                            json={"token": raw, "full_name": "Quiet Joiner",
                                  "password": "brand-new-secret"})
        assert a.status_code == 200, a.text
        subject = ORG_002.format(member="Quiet Joiner", org=fx.org_name)
        assert subject not in cap.subjects, \
            f"member_joined=false must suppress ORG-002; got {cap.subjects}"

        db = SessionLocal()
        try:
            joiner = db.query(User).filter(User.email == invitee.lower()).one()
            assert joiner is not None, "the membership still happened"
            for model in (SignInEvent, IdentityChallenge, AccountRecovery):
                db.query(model).filter(model.user_id == joiner.id).delete()
            db.delete(joiner)
            db.commit()
        finally:
            db.close()
        fx.emails.append(invitee)
    finally:
        fx.cleanup()


# ══ ORG-012 the confirmation itself ═════════════════════════════════════════════

def test_012_change_persists_records_snapshot_and_sends_one_email():
    """1 (persists), 4 (one event), 5/6 (snapshots), 7 (actor), 8 (scope), 9 (tz),
    10 (owner of the preference), 12 (dedup), 13 (subject), 14/15 (parts), 16 (readable)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch("/api/organization/notifications",
                             json={"member_joined": False, "event_scheduled": False},
                             headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["member_joined"] is False and body["event_scheduled"] is False

        db = SessionLocal()
        try:
            stored = db.get(Organization, fx.org_id).notifications
            assert stored["member_joined"] is False, "preference persisted"
        finally:
            db.close()

        events = fx.events()
        assert len(events) == 1, f"exactly one event, got {len(events)}"
        ev = events[0]
        assert ev.previous_preferences["member_joined"] is True, "previous snapshot"
        assert ev.current_preferences["member_joined"] is False, "current snapshot"
        assert ev.actor_email == fx.admin_email.lower(), "actor recorded"
        assert ev.preference_owner_email == fx.admin_email.lower(), "owner recorded"
        assert ev.scope == "organization", "scope recorded"
        assert ev.effective_at.tzinfo is not None, "effective_at is timezone-aware"
        assert ev.notified_at is not None

        payload = cap.of(ORG_012)
        assert payload["subject"] == "Your Zoiko Steam notification preferences changed"
        assert payload["from"].startswith("Zoiko Steam <"), "ORG-012 is Class C"
        assert not payload["from"].startswith("Zoiko Steam Security")
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "Member joined: On -> Off" in text, f"readable summary missing:\n{text}"
        assert "Event scheduled: On -> Off" in text
        assert "IST" in text, "timezone-aware effective timestamp"
        assert fx.org_name in text, "scope named"
        assert ("Mandatory security, legal, access, and contract communications cannot be "
                "disabled.") in text
        # Never raw JSON.
        for raw in ("{'member_joined'", '{"member_joined"', "True,", "false}"):
            assert raw not in text, f"raw JSON {raw!r} leaked into the body"

        # 12 — recipients deduplicate. The actor here is not the owner, so both are told.
        got = cap.to(ORG_012)
        assert len(got) == len(set(got)), "recipients deduplicate"
        assert fx.admin_email.lower() in got, "preference owner is always told"
        assert fx.owner_email.lower() in got, "org-wide routing change reaches the owner"
    finally:
        fx.cleanup()


def test_012_11_owner_is_not_double_mailed_when_owner_is_the_actor():
    """11 — the Organization Owner is notified where required, once."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.owner_email)          # owner IS the actor here
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch("/api/organization/notifications",
                             json={"member_joined": False}, headers=headers)
        assert r.status_code == 200, r.text
        got = cap.to(ORG_012)
        assert got == [fx.owner_email.lower()], \
            f"one message when owner and actor are the same person; got {got}"
    finally:
        fx.cleanup()


def test_012_2_3_invalid_and_noop_send_nothing():
    """2 (invalid sends nothing), 3 (no-op sends nothing), 24 (no duplicate)."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)

        _reset_limits()
        cap0, ctx0 = _capture()
        with ctx0:
            bad = client.patch("/api/organization/notifications",
                               json={"member_joined": "not-a-boolean"}, headers=headers)
        assert bad.status_code == 422, bad.text
        assert cap0.calls == [], "a rejected update must send nothing"
        assert fx.events() == [], "and record no event"

        # Real change.
        _reset_limits()
        cap1, ctx1 = _capture()
        with ctx1:
            client.patch("/api/organization/notifications",
                         json={"member_joined": False}, headers=headers)
        assert ORG_012 in cap1.subjects
        assert len(fx.events()) == 1

        # Same values again — a no-op.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            same = client.patch("/api/organization/notifications",
                                json={"member_joined": False}, headers=headers)
        assert same.status_code == 200, same.text
        assert cap2.calls == [], f"a no-op must send nothing; got {cap2.subjects}"
        assert len(fx.events()) == 1, "and must not record a second event"
    finally:
        fx.cleanup()


def test_012_18_22_security_alerts_false_is_normalized_not_honoured():
    """18 and 22 — the backend enforces mandatory settings independently of the UI."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            r = client.patch("/api/organization/notifications",
                             json={"security_alerts": False, "billing": False},
                             headers=headers)
        assert r.status_code == 200, "an old client sending false must not break"
        body = r.json()
        assert body["security_alerts"] is True, "pinned back on"
        assert body["billing"] is True, "contract communications stay mandatory"
        assert cap.calls == [], "normalizing to a no-op must not send a confirmation"

        db = SessionLocal()
        try:
            stored = db.get(Organization, fx.org_id).notifications or {}
            assert stored.get("security_alerts") is not False, \
                "false must never be persisted for a mandatory key"
            org = db.get(Organization, fx.org_id)
            assert notif_svc.should_send_operational_notification(family="IDN-003", org=org)
        finally:
            db.close()

        # And a mandatory Class A email really does still go out.
        _reset_limits()
        cap2, ctx2 = _capture()
        with ctx2:
            login = client.post("/api/auth/login",
                                json={"identifier": fx.admin_email, "password": PASSWORD},
                                headers={"User-Agent":
                                         "Mozilla/5.0 (X11; Linux x86_64) Firefox/121"})
        assert login.status_code == 200, login.text
        assert IDN_003 in cap2.subjects, \
            f"Class A must still send with security_alerts stored false; got {cap2.subjects}"
    finally:
        fx.cleanup()


def test_012_21_catalog_describes_controls_truthfully():
    """21 — the UI's source of truth marks mandatory and unavailable controls."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        r = client.get("/api/organization/notifications/catalog", headers=headers)
        assert r.status_code == 200, r.text
        by_key = {c["key"]: c for c in r.json()}

        assert by_key["security_alerts"]["mandatory"] is True
        assert by_key["security_alerts"]["configurable"] is False
        assert by_key["security_alerts"]["message_class"] == "A"
        assert by_key["security_alerts"]["value"] is True
        assert "cannot be disabled" in by_key["security_alerts"]["description"]
        assert by_key["billing"]["mandatory"] is True

        # Controls with no send path must be reported unavailable, not offered as switches.
        # `recording_ready` used to be one of them and is not any more: MED-008 gave it a
        # real send path (services/recording_comms.notify_finalized), so it now has to
        # behave like a working switch. The invariant being tested is the CORRESPONDENCE
        # between `available` and a send path actually existing — not a fixed list, which
        # would need editing every time a family ships.
        for key in ("event_starting", "weekly_summary", "mentions"):
            assert by_key[key]["available"] is False, f"{key} has no send path"
            assert by_key[key]["configurable"] is False
            assert by_key[key]["value"] is False

        for key in ("event_scheduled", "member_joined", "recording_ready"):
            assert by_key[key]["configurable"] is True and by_key[key]["available"] is True

        # An offered control must name the family it governs. Checked against the server-side
        # registry rather than the API response, which deliberately does not expose the
        # internal family code — that is what makes the availability flag meaningful rather
        # than decorative.
        from app.services import notifications as notif

        for key, entry in by_key.items():
            if entry["available"] and entry["configurable"]:
                assert notif.CATALOG_BY_KEY[key]["family"], (
                    f"{key} is offered as a working switch but governs no family")

        for entry in by_key.values():
            assert entry["scope"] and entry["channel"], "scope and channel are stated"
    finally:
        fx.cleanup()


def test_012_23_outage_does_not_undo_the_save():
    """23 — a Resend failure leaves committed preferences saved."""
    fx = Fixture()
    client = TestClient(m.app)
    try:
        headers = fx.token(fx.admin_email)
        _reset_limits()
        cap, ctx = _capture(fail=True)
        with ctx:
            r = client.patch("/api/organization/notifications",
                             json={"member_joined": False}, headers=headers)
        assert r.status_code == 200, "a mail outage must not fail the save"
        assert cap.calls, "a send was attempted through Resend"
        db = SessionLocal()
        try:
            assert db.get(Organization, fx.org_id).notifications["member_joined"] is False
        finally:
            db.close()
        assert len(fx.events()) == 1, "the snapshot event still stands"
    finally:
        fx.cleanup()


def test_012_25_previously_implemented_security_emails_remain_mandatory():
    """25 — nothing in this change made an earlier IDN/ORG security family suppressible."""
    for family, fn in (("IDN-003", email_mod.send_new_sign_in_email),
                       ("IDN-004", email_mod.send_suspicious_sign_in_email),
                       ("IDN-005", email_mod.send_credential_changed_email),
                       ("IDN-008", email_mod.send_account_restricted_email),
                       ("ORG-003", email_mod.send_access_changed_email),
                       ("ORG-004", email_mod.send_membership_removed_email),
                       ("ORG-009", email_mod.send_support_access_requested_email),
                       ("ORG-010", email_mod.send_organization_restricted_email)):
        assert callable(fn), f"{family} sender missing"
        assert notif_svc.is_mandatory(family), f"{family} became suppressible"
    # No Class A sender may consult a preference. The helper is the only reader, and it
    # short-circuits before the lookup for these families.
    src = open(notif_svc.__file__, encoding="utf-8").read()
    assert "if is_mandatory(family):\n        return True" in src, \
        "the mandatory short-circuit must precede any preference lookup"


try:                                    # pytest only; the plain runner checks inline
    import pytest

    @pytest.fixture(autouse=True)
    def _no_unmocked_sends():
        yield
        _assert_no_leak()
except ImportError:                     # pragma: no cover
    pass


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                _assert_no_leak()
                passed += 1
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001 — a self-check runner reports, not raises
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
