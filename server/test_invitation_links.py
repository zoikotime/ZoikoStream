"""Secure private invitation links — hardening of the EXISTING invite and access-link flows.

ZST-SPEC-VAP-001 §5.1 (services/invitation_links.py):
   1. the URL path carries no PII (only the event's random id);
   2. the secret has at least 128 bits;
   3. the plaintext secret is not stored;
   4. the secret sits in the fragment, which an HTTP GET does not carry;
   5. the secret travels only in the explicit validation POST — afterwards the existing
      credential is used;
   7. an invalid secret is refused; 8. a secret for event A does nothing on event B;
   9. the existing registration/watch flow follows; 11. chat/reactions/Q&A still work.
(6 — stripping the fragment — and 10 — the dashboard unchanged — are client tests:
client/src/pages/watch/invitationLink.test.jsx.)
Plus: revoke/rotate refuse old passes, two browsers on one link get two identities, the
legacy ?link= / ?reg= links still work, logs never carry a credential, no-referrer is set.
"""
import base64
import hashlib
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from jose import jwt as jose_jwt
from sqlalchemy import select
from starlette.testclient import TestClient

import app.main as m
from app import log_redaction
from app.db import SessionLocal
from app.models import (AuditLog, Event, EventAccessLink, EventAssignment, EventRegistration,
                        LiveActivity, LiveMessage, LiveQuestion, Organization, User)
from app.routers import events as events_router
from app.security import create_access_token, hash_password
from app.services import bus, invitation_links

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _in_process_bus(monkeypatch):
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")
    yield


@pytest.fixture
def world():
    db = SessionLocal()
    org = Organization(name=f"Links {uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    admin = User(org_id=org.id, full_name="Vihari Rao", role="org_admin", is_active=True,
                 email=f"il-{uuid.uuid4().hex[:10]}@example.com", username=f"il{uuid.uuid4().hex[:10]}",
                 password_hash=hash_password("x"), email_verified=True)
    db.add(admin)
    db.flush()
    now = datetime.now(UTC)

    def event(title, visibility="private"):
        ev = Event(org_id=org.id, created_by=admin.id, title=title, category="Funeral / Memorial",
                   status="live", visibility=visibility, chat_enabled=True, qa_enabled=True,
                   polls_enabled=True, start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1))
        db.add(ev)
        db.flush()
        return ev

    a = event("In loving memory of Margaret Okafor")
    b = event("Another private service")
    pub = event("Open service", visibility="public")
    db.commit()
    ns = type("W", (), {"db": db, "org": org, "admin": admin, "a": a, "b": b, "pub": pub})()
    try:
        yield ns
    finally:
        db.rollback()
        ids = [a.id, b.id, pub.id]
        for model in (LiveActivity, LiveMessage, LiveQuestion, EventRegistration, EventAccessLink, EventAssignment):
            db.query(model).filter(model.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.org_id == org.id).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)
        db.query(User).filter(User.org_id == org.id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id == org.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def host(world):
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(world.admin, remember=False)}"
    return c


def issue_link(world, ev, label="Family"):
    r = host(world).post(f"/api/events/{ev.id}/access-links", json={"label": label})
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    return url, url.split("#link=", 1)[1], r.json()["id"]


def invite(world, ev, monkeypatch, name="Cousin Ray", email="ray.okafor@example.com"):
    """Invite a viewer; returns the URL that was put in their email."""
    sent = []
    monkeypatch.setattr(events_router, "send_viewer_invite_email", lambda *a, **k: sent.append(a))
    r = host(world).post(f"/api/events/{ev.id}/invite-viewers", json={"invites": [{"name": name, "email": email}]})
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    return sent[0][3]


def redeem(ev, kind, secret, client=None):
    return (client or TestClient(m.app)).post(f"/api/events/{ev.id}/invitation", json={"kind": kind, "secret": secret})


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ── 1-4: shape, entropy, storage, fragment ───────────────────────────────────────────────

def test_1_the_url_path_carries_no_personal_data(world, monkeypatch):
    link_url, _, _ = issue_link(world, world.a)
    invite_url = invite(world, world.a, monkeypatch)
    for url in (link_url, invite_url):
        parsed = httpx.URL(url)
        assert parsed.path == f"/events/{world.a.id}/watch"           # the random event id only
        assert parsed.query == b""                                      # nothing in the query
        whole = url.lower()
        for pii in ("ray", "okafor", "margaret", "example.com", "vihari", "@"):
            assert pii not in whole, (pii, url)


def test_1b_the_invitation_secret_does_not_encode_the_email(world, monkeypatch):
    url = invite(world, world.a, monkeypatch)
    raw = unb64(url.split("#invite=", 1)[1])
    assert b"ray" not in raw.lower() and b"example" not in raw.lower()


def test_2_secrets_carry_at_least_128_bits(world, monkeypatch):
    _, link_secret, _ = issue_link(world, world.a)
    assert len(unb64(link_secret)) * 8 >= 128                          # 256-bit random token
    invite_secret = invite(world, world.a, monkeypatch).split("#invite=", 1)[1]
    raw = unb64(invite_secret)
    assert len(raw[-32:]) * 8 >= 128                                    # 256-bit MAC


def test_3_the_plaintext_secret_is_not_stored(world, monkeypatch):
    _, link_secret, link_id = issue_link(world, world.a)
    world.db.expire_all()
    row = world.db.get(EventAccessLink, uuid.UUID(link_id))
    assert row.token_hash == hashlib.sha256(link_secret.encode()).hexdigest()
    assert link_secret not in repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
    invite_secret = invite(world, world.a, monkeypatch).split("#invite=", 1)[1]
    reg = world.db.scalar(select(EventRegistration).where(EventRegistration.event_id == world.a.id))
    assert invite_secret not in repr({c.name: getattr(reg, c.name) for c in reg.__table__.columns})


def test_4_an_http_get_of_the_link_carries_no_secret(world, monkeypatch):
    url, secret, _ = issue_link(world, world.a)
    # RFC 9110/3986: a user agent never sends the fragment. httpx builds the request target
    # the same way a browser does — path and query only.
    request = httpx.Request("GET", url)
    assert request.url.raw_path.decode() == f"/events/{world.a.id}/watch"
    assert secret not in request.url.raw_path.decode()
    assert secret not in str(request.headers)


# ── 5, 7, 8, 9: the exchange ─────────────────────────────────────────────────────────────

def test_5_the_secret_is_exchanged_once_for_the_existing_credential(world, monkeypatch):
    _, link_secret, _ = issue_link(world, world.a)
    r = redeem(world.a, "link", link_secret)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    body = r.json()
    assert body["credential"] == "link" and body["token"] != link_secret
    assert link_secret not in body["token"]
    # From here on the page uses the pass, never the secret.
    w = TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": body["token"]})
    assert w.status_code == 200 and w.json()["livekit_token"]

    invite_secret = invite(world, world.a, monkeypatch).split("#invite=", 1)[1]
    r = redeem(world.a, "invite", invite_secret)
    assert r.status_code == 200 and r.json()["credential"] == "reg"
    assert r.json()["token"] != invite_secret


def test_7_an_invalid_secret_is_refused_without_saying_why(world):
    _, link_secret, _ = issue_link(world, world.a)
    bad = [("link", "x" * 43), ("invite", "y" * 71), ("link", link_secret[:-2] + "AA"),
           ("invite", link_secret)]
    messages = set()
    for kind, secret in bad:
        r = redeem(world.a, kind, secret)
        assert r.status_code == 404, (kind, r.text)
        messages.add(r.json()["detail"])
    assert len(messages) == 1                                         # one indistinguishable answer
    assert redeem(world.a, "link", "short").status_code == 422          # malformed shape


def test_8_a_secret_for_event_a_does_nothing_on_event_b(world, monkeypatch):
    _, link_secret, _ = issue_link(world, world.a)
    invite_secret = invite(world, world.a, monkeypatch).split("#invite=", 1)[1]
    assert redeem(world.b, "link", link_secret).status_code == 404
    assert redeem(world.b, "invite", invite_secret).status_code == 404
    link_pass = redeem(world.a, "link", link_secret).json()["token"]
    assert TestClient(m.app).get(f"/api/events/{world.b.id}/watch", params={"link": link_pass}).status_code == 403
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": link_pass}).status_code == 200


def test_9_the_existing_registration_flow_follows(world, monkeypatch):
    # A personal invitation to a PRIVATE event: the claim cookie and its one-device rule
    # still apply after the exchange, exactly as they did for a ?reg= link.
    secret = invite(world, world.a, monkeypatch).split("#invite=", 1)[1]
    browser = TestClient(m.app)
    reg_token = redeem(world.a, "invite", secret, client=browser).json()["token"]
    first = browser.get(f"/api/events/{world.a.id}/watch", params={"reg": reg_token})
    assert first.status_code == 200 and first.json()["registered"] is True
    other_device = TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"reg": reg_token})
    assert other_device.status_code == 403 and "another device" in other_device.json()["detail"]
    # A PUBLIC event with no credential still shows the registration gate.
    gated = TestClient(m.app).get(f"/api/events/{world.pub.id}/watch").json()
    assert gated["registration_required"] is True and gated["registered"] is False


# ── passes: revocation, rotation, one identity per browser ───────────────────────────────

def test_revoking_or_rotating_the_link_refuses_existing_passes(world):
    _, secret, link_id = issue_link(world, world.a)
    pass_ = redeem(world.a, "link", secret).json()["token"]
    h = host(world)
    assert h.post(f"/api/events/{world.a.id}/access-links/{link_id}/revoke").status_code == 200
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": pass_}).status_code == 403
    rotated = h.post(f"/api/events/{world.a.id}/access-links/{link_id}/rotate", json={}).json()["url"]
    # Rotation un-revokes the row with a NEW secret: the old pass stays dead, the new link works.
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": pass_}).status_code == 403
    new_pass = redeem(world.a, "link", rotated.split("#link=", 1)[1]).json()["token"]
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": new_pass}).status_code == 200


def test_two_people_on_one_link_get_two_identities(world):
    """LiveKit allows one connection per identity: with a shared identity the second viewer
    evicted the first. A pass gives each browser its own."""
    _, secret, link_id = issue_link(world, world.a)
    identities = set()
    for _ in range(2):
        pass_ = redeem(world.a, "link", secret).json()["token"]
        token = TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": pass_}).json()["livekit_token"]
        identities.add(jose_jwt.get_unverified_claims(token)["sub"])
    assert len(identities) == 2
    assert all(i.startswith(f"guest-link-{link_id}-") for i in identities)


def test_a_tampered_pass_is_refused(world):
    _, secret, _ = issue_link(world, world.a)
    pass_ = redeem(world.a, "link", secret).json()["token"]
    raw = bytearray(unb64(pass_[len(invitation_links.PASS_PREFIX):]))
    raw[17] ^= 0x01                                                   # flip a bit of the per-browser value
    forged = invitation_links.PASS_PREFIX + base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode()
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": forged}).status_code == 403


def test_legacy_query_links_still_work(world, monkeypatch):
    # Links already sent before this change carried the raw token in ?link= / ?reg=.
    _, secret, _ = issue_link(world, world.a)
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch", params={"link": secret}).status_code == 200
    from app.security import create_registration_token
    invite(world, world.a, monkeypatch)
    reg = world.db.scalar(select(EventRegistration).where(EventRegistration.event_id == world.a.id))
    assert TestClient(m.app).get(f"/api/events/{world.a.id}/watch",
                                 params={"reg": create_registration_token(reg)}).status_code == 200


# ── 11: the live socket after the exchange ───────────────────────────────────────────────

def test_11_chat_reactions_and_qa_work_on_the_socket_with_a_pass(world):
    _, secret, _ = issue_link(world, world.a)
    pass_ = redeem(world.a, "link", secret).json()["token"]
    with TestClient(m.app).websocket_connect(f"/api/live/events/{world.a.id}/ws?link={pass_}") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["data"]["you"]["identity"].startswith("guest-link-")
        ws.send_json({"action": "chat.send", "payload": {"text": "We miss you"}})
        ws.send_json({"action": "qa.ask", "payload": {"text": "Will there be a replay?"}})
        ws.send_json({"action": "reaction.add", "payload": {"key": "heart"}})
        seen = set()
        for _ in range(20):
            env = ws.receive_json()
            seen.add((env["channel"], env["type"]))
            if {("chat", "message.new"), ("qa", "question.new"), ("reactions", "reaction.burst")} <= seen:
                break
    assert {("chat", "message.new"), ("qa", "question.new"), ("reactions", "reaction.burst")} <= seen


# ── logs and headers ─────────────────────────────────────────────────────────────────────

def test_credentials_never_reach_the_server_logs():
    log_redaction.install()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    access, error = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")
    for lg in (access, error):
        lg.addHandler(handler)
    old = (access.level, error.level, access.propagate, error.propagate)
    access.setLevel(logging.INFO)
    error.setLevel(logging.INFO)
    try:
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET",
                    "/api/events/e/watch?reg=eyJSECRETjwt&link=RAWLINKSECRET&x=1", "1.1", 200)
        error.info('%s - "WebSocket %s" [accepted]', "1.2.3.4:5",
                   "/api/live/events/e/ws?token=LOGINJWT&reg=REGJWT")
    finally:
        for lg in (access, error):
            lg.removeHandler(handler)
        access.setLevel(old[0])
        error.setLevel(old[1])
    out = stream.getvalue()
    for secret in ("eyJSECRETjwt", "RAWLINKSECRET", "LOGINJWT", "REGJWT"):
        assert secret not in out, out
    assert "reg=[redacted]" in out and "x=1" in out


def test_the_viewer_page_is_served_with_no_referrer(world):
    if not m.DIST.is_dir():
        pytest.skip("client/dist is not built")
    r = TestClient(m.app).get(f"/events/{world.a.id}/watch")
    assert r.status_code == 200 and r.headers.get("referrer-policy") == "no-referrer"
    other = TestClient(m.app).get("/pricing")
    assert other.headers.get("referrer-policy") is None                # scoped to the viewer route
