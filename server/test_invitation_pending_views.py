"""Organization invitations: what the inviting admin and the invitee each see, end to end.

Bug 17. An organization membership invitation (POST /organization/invitations) is the one
invitation type with a pending -> accepted lifecycle; the event watch invite, the
contributor invite and host/speaker assignments are separate flows and are not touched here.

The admin's own list was right: acceptance writes `accepted` atomically and the Members page
counts only `pending`. What was wrong is who saw the organization's "N invitations awaiting
acceptance · Manage". The overview's attention list - things an org admin has to act on, each
linking to an admin-only page - was returned to every member, so a new member who had just
accepted was shown the invitations still pending for OTHER people, with an action they
cannot take. It is now empty below org_admin.

Real database and the real API (TestClient on app.main), against TEST_DATABASE_URL (see
conftest.py). Every invitation email is intercepted by conftest's outbound-mail stub.
"""
import uuid
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

import app.main as m
from app.db import SessionLocal
from app.models import Invitation, Organization, User
from app.security import create_access_token

API = "/api/organization"
PASSWORD = "Correct-Horse-Battery-9"
client = TestClient(m.app)     # no `with`: no lifespan tickers


def _org_with_admin():
    db = SessionLocal()
    try:
        tag = uuid.uuid4().hex[:10]
        org = Organization(name=f"inv-views-{tag}", status="active")
        db.add(org)
        db.flush()
        admin = User(org_id=org.id, full_name="Ada Admin", role="org_admin", is_active=True,
                     email=f"admin-{tag}@example.com", username=f"admin{tag}", password_hash="x",
                     email_verified=True)
        db.add(admin)
        db.commit()
        token = create_access_token(admin, remember=False, db=db)
        return org.id, {"Authorization": f"Bearer {token}"}
    finally:
        db.close()


def _invite(headers, email, role="host"):
    r = client.post(f"{API}/invitations", json={"email": email, "role": role}, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending" and body["invite_token"]
    return body


def _invitations(headers):
    r = client.get(f"{API}/invitations", params={"page_size": 100}, headers=headers)
    assert r.status_code == 200, r.text
    return {i["email"]: i for i in r.json()["items"]}


def _accept(token, name="Ivy Invitee"):
    return client.post(f"{API}/invitations/accept",
                       json={"token": token, "full_name": name, "password": PASSWORD})


def _attention(headers):
    r = client.get(f"{API}/overview", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["attention"]


def _email(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def test_admin_sees_one_pending_until_it_is_accepted_then_accepted():
    _, admin = _org_with_admin()
    ivy = _email("ivy")
    sent = _invite(admin, ivy)

    listed = _invitations(admin)
    assert list(listed) == [ivy] and listed[ivy]["status"] == "pending"
    assert [a["title"] for a in _attention(admin) if a["id"] == "invites"] == ["1 invitation awaiting acceptance"]

    r = _accept(sent["invite_token"])
    assert r.status_code == 200, r.text

    after = _invitations(admin)[ivy]
    assert after["status"] == "accepted" and after["accepted_at"]
    assert [i for i in _invitations(admin).values() if i["status"] == "pending"] == []
    assert not any(a["id"] == "invites" for a in _attention(admin))


def test_the_new_member_is_not_shown_other_peoples_pending_invitations():
    _, admin = _org_with_admin()
    ivy, bob = _email("ivy"), _email("bob")
    ivy_inv = _invite(admin, ivy, role="viewer")
    _invite(admin, bob, role="host")                      # still pending for someone else

    r = _accept(ivy_inv["invite_token"])
    assert r.status_code == 200, r.text
    invitee = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # The invitee's own dashboard: nothing of the organization's invitation backlog.
    assert _attention(invitee) == []
    # And the list itself stays admin-only.
    assert client.get(f"{API}/invitations", headers=invitee).status_code == 403

    # The admin still sees Bob's invitation as the one awaiting acceptance.
    titles = [a["title"] for a in _attention(admin) if a["id"] == "invites"]
    assert titles == ["1 invitation awaiting acceptance"]


def test_accepting_twice_is_refused_and_creates_one_member():
    org_id, admin = _org_with_admin()
    ivy = _email("ivy")
    token = _invite(admin, ivy)["invite_token"]
    assert _accept(token).status_code == 200
    again = _accept(token, name="Ivy Again")
    assert again.status_code == 400 and "already-used" in again.json()["detail"]

    db = SessionLocal()
    try:
        members = db.query(User).filter(User.org_id == org_id, User.email == ivy).count()
        rows = db.query(Invitation).filter(Invitation.org_id == org_id, Invitation.email == ivy).count()
    finally:
        db.close()
    assert members == 1 and rows == 1


def test_one_acceptance_leaves_other_invitations_and_other_organizations_alone():
    _, admin_a = _org_with_admin()
    _, admin_b = _org_with_admin()
    ivy, bob, cara = _email("ivy"), _email("bob"), _email("cara")
    ivy_inv = _invite(admin_a, ivy)
    _invite(admin_a, bob)
    _invite(admin_b, cara)

    assert _accept(ivy_inv["invite_token"]).status_code == 200

    a = _invitations(admin_a)
    assert a[ivy]["status"] == "accepted" and a[bob]["status"] == "pending"
    b = _invitations(admin_b)
    assert list(b) == [cara] and b[cara]["status"] == "pending"     # no cross-org rows


def test_declined_cancelled_and_expired_are_not_pending():
    org_id, admin = _org_with_admin()
    declined, cancelled, expired = _email("dec"), _email("can"), _email("exp")
    d = _invite(admin, declined)
    c = _invite(admin, cancelled)
    _invite(admin, expired)

    assert client.post(f"{API}/invitations/reject", json={"token": d["invite_token"]}).status_code == 200
    assert client.patch(f"{API}/invitations/{c['id']}", json={"action": "cancel"}, headers=admin).status_code == 200
    db = SessionLocal()
    try:
        row = db.query(Invitation).filter(Invitation.org_id == org_id, Invitation.email == expired).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    listed = _invitations(admin)
    assert (listed[declined]["status"], listed[cancelled]["status"], listed[expired]["status"]) == \
        ("rejected", "cancelled", "expired")
    assert not any(a["id"] == "invites" for a in _attention(admin))
    # An accepted invitation cannot be resent back to pending.
    ivy = _email("ivy")
    ivy_inv = _invite(admin, ivy)
    assert _accept(ivy_inv["invite_token"]).status_code == 200
    resend = client.patch(f"{API}/invitations/{ivy_inv['id']}", json={"action": "resend"}, headers=admin)
    assert resend.status_code == 409


def test_members_below_org_admin_get_no_admin_attention_but_admins_still_do():
    org_id, admin = _org_with_admin()
    _invite(admin, _email("pending"))
    db = SessionLocal()
    try:
        tokens = {}
        for role in ("viewer", "speaker", "host"):
            u = User(org_id=org_id, full_name=f"{role} member", role=role, is_active=True,
                     email=_email(role), username=f"{role}{uuid.uuid4().hex[:8]}", password_hash="x",
                     email_verified=True)
            db.add(u)
            db.commit()
            tokens[role] = create_access_token(u, remember=False, db=db)
    finally:
        db.close()
    for role, token in tokens.items():
        assert _attention({"Authorization": f"Bearer {token}"}) == [], role
    assert any(a["id"] == "invites" for a in _attention(admin))
