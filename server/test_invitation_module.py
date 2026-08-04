"""Invitation & Access Management — end-to-end checks through the real HTTP layer.

Real database, everything inside a transaction that is ALWAYS rolled back — same discipline as
test_event_module.py and test_org_overview.py. Going through TestClient with real JWTs is the
point: these assertions are about routing, RBAC, tenant isolation, the atomic claim and the
token resolver, and none of that is exercised by calling crud directly.

test_invitations.py holds the pure half (token hashing, the transition table, the invite
allow-tables, Svix verification).

Run: `python test_invitation_module.py` (or pytest).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import organization as crud
from app.db import SessionLocal, get_db
from app.models import Event, Organization, User
from app.ratelimit import _HITS
from app.security import create_access_token, hash_password, verify_password

# Creating an invitation queues a real email. TestClient runs BackgroundTasks synchronously, so
# without this every create in this file would POST to Resend. Blank = the send is skipped and
# recorded as a failure, which is app.email's documented degraded path.
settings.RESEND_API_KEY = ""


class Fixture:
    """Two organizations, four users, one event, one client. Torn down by a rollback."""

    def __init__(self):
        # The per-IP rate limiter is per-PROCESS and every TestClient request shares one
        # client host, so 40+ tests in one run would exhaust the invite budgets and mask real
        # assertions behind a 429. Cleared per test — the limits themselves are asserted
        # separately (test_public_endpoints_are_rate_limited).
        _HITS.clear()
        self.db = SessionLocal()
        self.tx = self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        uniq = uuid.uuid4().hex[:8]
        self.uniq = uniq

        self.org = Organization(name=f"Invite Org {uniq}", status="active")
        self.other_org = Organization(name=f"Other Org {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Invite Admin", f"adm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "Team Host", f"hst{uniq}", "host")
        self.colleague = self._user(self.org.id, "Existing Colleague", f"col{uniq}", "speaker")
        self.outsider = self._user(self.other_org.id, "Outsider", f"out{uniq}", "org_admin")
        self.db.flush()

        self.event = Event(org_id=self.org.id, created_by=self.admin.id,
                           title=f"Invite Event {uniq}", slug=f"invite-event-{uniq}",
                           status="published")
        self.other_event = Event(org_id=self.other_org.id, created_by=self.outsider.id,
                                 title="Foreign Event", slug=f"foreign-{uniq}", status="published")
        self.db.add_all([self.event, self.other_event])
        self.db.flush()

        m.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(m.app)

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def new_email(self, tag="new"):
        return f"{tag}{uuid.uuid4().hex[:8]}@example.com"

    def close(self):
        m.app.dependency_overrides.clear()
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001 — teardown must not mask a failure
            pass
        self.db.rollback()
        self.db.close()


def _invite(f, headers=None, **body):
    """Create an invitation and return (response_json, raw_token)."""
    r = f.client.post("/organization/invitations", headers=headers or f.auth(f.admin), json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    return data, data["invite_token"]


# ── New user flow ─────────────────────────────────────────────────────────────

def test_new_user_org_invitation_end_to_end(f):
    email = f.new_email()
    inv, token = _invite(f, email=email, role="moderator")
    assert inv["status"] == "pending" and inv["invite_url"].endswith(f"#token={token}")
    # The token rides in the FRAGMENT so it never reaches a server log or a Referer header.
    assert "#token=" in inv["invite_url"] and "?token=" not in inv["invite_url"]

    # Preview: public, and it reports from the SERVER, never the URL.
    p = f.client.get("/organization/invitations/preview", params={"token": token})
    assert p.status_code == 200, p.text
    prev = p.json()
    assert prev["org_name"] == f.org.name
    assert prev["role_label"] == "Moderator"
    assert prev["needs_account"] is True
    assert prev["email_hint"].endswith("@example.com") and email not in prev["email_hint"]

    # Accept: brand-new account -> a session IS issued.
    a = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "New Person", "password": "hunter2pw"})
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["access_token"] and body["login_required"] is False
    assert body["user"]["role"] == "moderator"

    created = crud.user_in_org(f.db, f.org.id, email)
    assert created is not None and created.is_active is True
    assert verify_password("hunter2pw", created.password_hash)
    # Single use: the same token cannot be replayed.
    again = f.client.post("/organization/invitations/accept",
                          json={"token": token, "full_name": "Again", "password": "hunter2pw"})
    assert again.status_code == 400


def test_new_user_event_invitation_creates_the_assignment(f):
    email = f.new_email()
    inv, token = _invite(f, email=email, event_id=str(f.event.id), event_role="host")
    # No explicit platform role -> derived conservatively from the event role.
    assert inv["event_role"] == "host" and inv["role"] == "host"
    assert inv["event_title"] == f.event.title

    a = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "New Host", "password": "hunter2pw"})
    assert a.status_code == 200, a.text
    assert a.json()["event_id"] == str(f.event.id)

    team = f.client.get(f"/events/{f.event.id}/team", headers=f.auth(f.admin)).json()
    assert [u["email"] for u in team["host"]] == [email]


def test_credited_event_roles_do_not_inflate_the_platform_role(f):
    """cohost/producer/panelist carry no broadcast authority (models/event.py), so the derived
    platform role must not be host or moderator — a platform role outlives the assignment."""
    for event_role in ("cohost", "producer", "panelist"):
        inv, _ = _invite(f, email=f.new_email(event_role), event_id=str(f.event.id),
                         event_role=event_role)
        assert inv["role"] == "speaker", (event_role, inv["role"])


# ── Existing user flow ────────────────────────────────────────────────────────

def test_existing_colleague_gets_access_but_never_a_session(f):
    """The security boundary of this whole feature: an emailed token must not be exchangeable
    for a session on an account that already has credentials — the admin can read the raw token
    straight out of the 201 response."""
    inv, token = _invite(f, email=f.colleague.email, event_id=str(f.event.id),
                         event_role="moderator")
    p = f.client.get("/organization/invitations/preview", params={"token": token}).json()
    assert p["needs_account"] is False

    before = f.colleague.password_hash
    a = f.client.post("/organization/invitations/accept", json={"token": token})
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["login_required"] is True
    assert body.get("access_token") is None, "no session may be minted for an existing account"

    f.db.refresh(f.colleague)
    # Neither the credentials nor the platform role are touched: users.role changes only
    # through PATCH /organization/users/{id}, which carries the self-lockout guards.
    assert f.colleague.password_hash == before
    assert f.colleague.role == "speaker"
    team = f.client.get(f"/events/{f.event.id}/team", headers=f.auth(f.admin)).json()
    assert f.colleague.email in [u["email"] for u in team["moderator"]]


def test_a_password_in_the_body_cannot_reset_an_existing_account(f):
    _, token = _invite(f, email=f.colleague.email, event_id=str(f.event.id), event_role="speaker")
    before = f.colleague.password_hash
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "Hijack", "password": "attacker-pw"})
    assert r.status_code == 200
    f.db.refresh(f.colleague)
    assert f.colleague.password_hash == before, "an existing password must be untouchable here"
    assert not verify_password("attacker-pw", f.colleague.password_hash)


def test_existing_member_cannot_be_re_invited_to_the_org_itself(f):
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.colleague.email, "role": "viewer"})
    assert r.status_code == 409 and "already a member" in r.text
    # But an EVENT invitation to that same colleague is the primary flow, so it must succeed.
    ok = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                       json={"email": f.colleague.email, "event_id": str(f.event.id),
                             "event_role": "speaker"})
    assert ok.status_code == 201, ok.text


def test_address_in_another_org_is_refused(f):
    """One user, one organization in this schema — there is no membership table to join."""
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.outsider.email, "event_id": str(f.event.id),
                            "event_role": "speaker"})
    assert r.status_code == 409 and "another organization" in r.text


def test_soft_deleted_member_can_be_re_invited(f):
    """users.email is globally UNIQUE, so inserting over a soft-deleted row is a 500. Re-hiring
    must REACTIVATE instead — and still not touch the password."""
    email = f.colleague.email
    before = f.colleague.password_hash
    assert f.client.delete(f"/organization/users/{f.colleague.id}",
                           headers=f.auth(f.admin)).status_code == 204
    f.db.refresh(f.colleague)
    assert f.colleague.deleted_at is not None

    _, token = _invite(f, email=email, role="host")
    a = f.client.post("/organization/invitations/accept", json={"token": token})
    assert a.status_code == 200, a.text
    assert a.json()["login_required"] is True
    f.db.refresh(f.colleague)
    assert f.colleague.deleted_at is None and f.colleague.is_active is True
    assert f.colleague.role == "host"
    assert f.colleague.password_hash == before


# ── Token validation ──────────────────────────────────────────────────────────

def test_every_invalid_token_state_is_indistinguishable(f):
    """Unknown, cancelled, declined, accepted, expired — one identical refusal, so possession
    of a random token reveals exactly one bit."""
    details = set()

    # unknown
    r = f.client.post("/organization/invitations/accept", json={"token": "z" * 43})
    details.add((r.status_code, r.json()["detail"]))

    # cancelled
    inv, token = _invite(f, email=f.new_email())
    f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                   json={"action": "cancel"})
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "X", "password": "hunter2pw"})
    details.add((r.status_code, r.json()["detail"]))

    # declined
    inv2, token2 = _invite(f, email=f.new_email())
    assert f.client.post("/organization/invitations/reject", json={"token": token2}).status_code == 200
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token2, "full_name": "X", "password": "hunter2pw"})
    details.add((r.status_code, r.json()["detail"]))

    # expired — push the clock back on the row itself
    inv3, token3 = _invite(f, email=f.new_email())
    f.db.execute(text("update invitations set expires_at = now() - interval '1 day' where id = :i"),
                 {"i": inv3["id"]})
    f.db.flush()
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token3, "full_name": "X", "password": "hunter2pw"})
    details.add((r.status_code, r.json()["detail"]))

    assert len(details) == 1, f"refusals are distinguishable: {details}"
    assert next(iter(details))[0] == 400


def test_preview_performs_no_write(f):
    """Mail scanners and link previewers fetch every URL in an email. A GET that mutated
    anything would have invitations consumed by robots before the human read them."""
    inv, token = _invite(f, email=f.new_email())
    f.db.execute(text("update invitations set expires_at = now() - interval '1 day' where id = :i"),
                 {"i": inv["id"]})
    f.db.flush()
    before = f.db.execute(text("select status from invitations where id = :i"),
                          {"i": inv["id"]}).scalar()
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 400
    after = f.db.execute(text("select status from invitations where id = :i"),
                         {"i": inv["id"]}).scalar()
    assert before == after == "pending", "the public preview must not flip the row to expired"


def test_resend_kills_the_previous_link(f):
    inv, token1 = _invite(f, email=f.new_email())
    r = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "resend"})
    assert r.status_code == 200, r.text
    token2 = r.json()["invite_token"]
    assert token2 != token1
    assert r.json()["resend_count"] == 1
    assert f.client.get("/organization/invitations/preview", params={"token": token1}).status_code == 400
    assert f.client.get("/organization/invitations/preview", params={"token": token2}).status_code == 200


def test_a_dead_inviter_invalidates_the_invitation(f):
    """A 7-day credential is re-authorized when it is CONSUMED, not only when it is issued: in
    between, the inviter can be demoted or removed.

    Mutated through the ORM, not raw SQL — the handler shares this Session, and db.get() would
    serve the identity map's cached row straight past an UPDATE it never saw.
    """
    _, token = _invite(f, email=f.new_email())
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 200

    f.admin.deleted_at = datetime.now(timezone.utc)
    f.admin.is_active = False
    f.db.flush()
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 400


def test_a_demoted_inviter_invalidates_the_invitation(f):
    """The same re-authorization, one step subtler: the inviter is still live but no longer
    permitted to grant the role they granted."""
    _, token = _invite(f, email=f.new_email(), role="org_admin")
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 200
    f.admin.role = "host"          # a host may not grant org_admin (INVITE_PLATFORM_ROLES)
    f.db.flush()
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 400


def test_a_suspended_organization_cannot_grow(f):
    """Suspension must not be able to GROW the tenant it froze."""
    _, token = _invite(f, email=f.new_email())
    f.org.status = "suspended"
    f.db.flush()
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "X", "password": "hunter2pw"})
    assert r.status_code == 400
    # And signing up cannot route around it either.
    r = f.client.post("/auth/register", json={
        "full_name": "Sneaky", "email": f.new_email("sneak"), "password": "hunter2pw",
        "organization_name": "Sneaky Co",
    })
    # Registration still works (it just creates their own org) but must NOT join the frozen one.
    if r.status_code == 201:
        assert r.json()["user"]["organization_name"] != f.org.name


# ── Races ─────────────────────────────────────────────────────────────────────

def test_claim_is_single_winner(f):
    """crud.claim is the concurrency gate: a conditional UPDATE that only succeeds from an open
    state. Without it, two accepts could both pass a read-then-check and both create a member."""
    inv, _ = _invite(f, email=f.new_email())
    inv_id = uuid.UUID(inv["id"])
    assert crud.claim(f.db, inv_id, "accepted") is True
    assert crud.claim(f.db, inv_id, "accepted") is False, "a claimed invitation must not re-claim"
    assert crud.claim(f.db, inv_id, "rejected") is False, "accept must beat a racing decline"


def test_accept_after_decline_is_refused(f):
    inv, token = _invite(f, email=f.new_email())
    assert f.client.post("/organization/invitations/reject", json={"token": token}).status_code == 200
    r = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "X", "password": "hunter2pw"})
    assert r.status_code == 400
    # No member was created behind the declined row.
    assert crud.user_in_org(f.db, f.org.id, inv["email"]) is None


# ── RBAC ──────────────────────────────────────────────────────────────────────

def test_only_admins_may_manage_invitations(f):
    inv, _ = _invite(f, email=f.new_email())
    HM = f.auth(f.host)
    assert f.client.get("/organization/invitations", headers=HM).status_code == 403
    assert f.client.get("/organization/invitations/stats", headers=HM).status_code == 403
    assert f.client.post("/organization/invitations", headers=HM,
                         json={"email": f.new_email(), "role": "viewer"}).status_code == 403
    assert f.client.post("/organization/invitations/bulk", headers=HM,
                         json={"emails": [f.new_email()]}).status_code == 403
    assert f.client.patch(f"/organization/invitations/{inv['id']}", headers=HM,
                          json={"action": "cancel"}).status_code == 403
    assert f.client.delete(f"/organization/invitations/{inv['id']}", headers=HM).status_code == 403
    assert f.client.post("/organization/invitations/delete-expired", headers=HM).status_code == 403


def test_nobody_can_invite_a_super_admin_or_an_unknown_role(f):
    for role in ("super_admin", "wizard"):
        r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                          json={"email": f.new_email(), "role": role})
        assert r.status_code == 422, (role, r.status_code)
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.new_email(), "event_id": str(f.event.id),
                            "event_role": "wizard"})
    assert r.status_code == 422


def test_event_invitations_require_an_event_role(f):
    """An invitation always creates an ORG MEMBER, and org membership alone opens every event in
    the org plus the audience-passphrase exemption. So it is never the audience mechanism — the
    refusal has to point the admin at the tool that is."""
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.new_email(), "event_id": str(f.event.id)})
    assert r.status_code == 422, r.text
    assert "access link" in r.text, r.text
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.new_email(), "event_role": "host"})
    assert r.status_code == 422 and "needs an event" in r.text


def test_public_endpoints_are_rate_limited(f):
    """The only unauthenticated endpoints in the app. NOT justified by token brute force — a
    256-bit token is ~2^255 indexed misses — but by keeping an anonymous endpoint from being a
    free-to-call amplifier."""
    _HITS.clear()
    codes = {
        f.client.post("/organization/invitations/accept", json={"token": "z" * 43}).status_code
        for _ in range(14)
    }
    assert 429 in codes, "accept must be rate limited"
    _HITS.clear()


# ── Tenant isolation ──────────────────────────────────────────────────────────

def test_cross_tenant_event_id_is_refused(f):
    """The blast radius if this were missed: an EventAssignment on another tenant's live event."""
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": f.new_email(), "event_id": str(f.other_event.id),
                            "event_role": "host"})
    assert r.status_code == 404, r.text
    # Same string the events API uses, so it is not an existence oracle.
    assert r.json()["detail"] == "Event not found"


def test_invitations_are_org_scoped_everywhere(f):
    inv, _ = _invite(f, email=f.new_email())
    HO = f.auth(f.outsider)
    assert all(x["id"] != inv["id"]
               for x in f.client.get("/organization/invitations", headers=HO).json()["items"])
    for method, path, body in [
        ("patch", f"/organization/invitations/{inv['id']}", {"action": "cancel"}),
        ("delete", f"/organization/invitations/{inv['id']}", None),
    ]:
        r = getattr(f.client, method)(path, headers=HO, **({} if body is None else {"json": body}))
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


def test_accepting_never_lands_in_the_wrong_org(f):
    email = f.new_email()
    _, token = _invite(f, email=email, event_id=str(f.event.id), event_role="speaker")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Scoped", "password": "hunter2pw"})
    created = crud.find_user_by_email(f.db, email)
    assert created.org_id == f.org.id
    # The assignment belongs to the invitation's event, which belongs to the invitation's org.
    row = f.db.execute(text("""
        select e.org_id from event_assignments a join events e on e.id = a.event_id
        where a.user_id = :u"""), {"u": created.id}).scalar()
    assert row == f.org.id


def test_a_deleted_event_invalidates_its_open_invitations(f):
    inv, token = _invite(f, email=f.new_email(), event_id=str(f.event.id), event_role="speaker")
    assert f.client.delete(f"/events/{f.event.id}", headers=f.auth(f.admin)).status_code == 204
    # Cancelled at the source, so the console does not show a live invitation to a dead event.
    status = f.db.execute(text("select status from invitations where id = :i"),
                          {"i": inv["id"]}).scalar()
    assert status == "cancelled"
    # And the token is dead either way.
    assert f.client.get("/organization/invitations/preview", params={"token": token}).status_code == 400


# ── Duplicate prevention ──────────────────────────────────────────────────────

def test_duplicate_open_invitations_are_refused_case_insensitively(f):
    email = f.new_email()
    _invite(f, email=email, role="viewer")
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": email.upper(), "role": "host"})
    assert r.status_code == 409 and "already an open invitation" in r.text


def test_the_same_person_can_be_invited_to_two_different_events(f):
    """Keyed on (org, email, event) — the old email-only check refused this, which is the
    normal case for an internal moderator working two events."""
    second = Event(org_id=f.org.id, created_by=f.admin.id, title="Second",
                   slug=f"second-{f.uniq}", status="published")
    f.db.add(second)
    f.db.flush()
    email = f.new_email()
    _invite(f, email=email, event_id=str(f.event.id), event_role="speaker")
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": email, "event_id": str(second.id), "event_role": "moderator"})
    assert r.status_code == 201, r.text


def test_re_inviting_after_a_decline_is_allowed(f):
    """The unique indexes cover only the OPEN state, so a closed row never blocks a re-invite —
    and the decline survives as history rather than being overwritten."""
    email = f.new_email()
    _, token = _invite(f, email=email, role="viewer")
    f.client.post("/organization/invitations/reject", json={"token": token})
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": email, "role": "viewer"})
    assert r.status_code == 201, r.text
    rows = f.db.execute(text("select count(*) from invitations where lower(email) = :e"),
                        {"e": email}).scalar()
    assert rows == 2, "re-asking creates a NEW row so the decline stays visible"


def test_duplicate_role_assignment_is_idempotent(f):
    """An admin who assigned the person manually while the invitation was open must not turn
    their accept into a uq_event_user_role violation."""
    email = f.new_email()
    _, token = _invite(f, email=email, event_id=str(f.event.id), event_role="speaker")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Dup", "password": "hunter2pw"})
    user = crud.user_in_org(f.db, f.org.id, email)
    # Assign the same role again through the events API.
    r = f.client.post(f"/events/{f.event.id}/team/speaker", headers=f.auth(f.admin),
                      json={"user_id": str(user.id)})
    assert r.status_code == 201
    n = f.db.execute(text("select count(*) from event_assignments where user_id = :u and role = 'speaker'"),
                     {"u": user.id}).scalar()
    assert n == 1


# ── Lifecycle: cancel / revoke / expire / delete ───────────────────────────────

def test_cancel_then_resend_reopens(f):
    inv, _ = _invite(f, email=f.new_email())
    c = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "cancel"})
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert c.json()["can_resend"] is True
    r = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "resend"})
    assert r.status_code == 200 and r.json()["status"] == "pending"


def test_revoke_removes_the_assignment_and_only_for_event_invitations(f):
    email = f.new_email()
    inv, token = _invite(f, email=email, event_id=str(f.event.id), event_role="moderator")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Mod", "password": "hunter2pw"})
    user = crud.user_in_org(f.db, f.org.id, email)
    assert user is not None

    r = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "revoke"})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    team = f.client.get(f"/events/{f.event.id}/team", headers=f.auth(f.admin)).json()
    assert email not in [u["email"] for u in team["moderator"]]
    # The ACCOUNT survives — revoke withdraws the event role, not the person.
    f.db.refresh(user)
    assert user.deleted_at is None and user.is_active is True
    # Terminal: no resend.
    again = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                           json={"action": "resend"})
    assert again.status_code == 409


def test_revoking_an_org_invitation_is_refused(f):
    """It would mean deleting a User, bypassing the self-deletion and super-admin guards on
    DELETE /organization/users/{id}."""
    email = f.new_email()
    inv, token = _invite(f, email=email, role="viewer")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Org Member", "password": "hunter2pw"})
    r = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "revoke"})
    assert r.status_code == 409 and "Members & Access" in r.text


def test_expired_invitations_are_swept_and_can_be_cleaned_up(f):
    inv, _ = _invite(f, email=f.new_email())
    f.db.execute(text("update invitations set expires_at = now() - interval '1 day' where id = :i"),
                 {"i": inv["id"]})
    f.db.flush()
    # The lazy sweep runs on read.
    rows = f.client.get("/organization/invitations", headers=f.auth(f.admin)).json()["items"]
    row = next(x for x in rows if x["id"] == inv["id"])
    assert row["status"] == "expired"
    assert row["can_resend"] is True, "the most common real resend is a lapsed invitation"

    r = f.client.post("/organization/invitations/delete-expired", headers=f.auth(f.admin))
    assert r.status_code == 200 and inv["id"] in [str(x) for x in r.json()["succeeded"]]
    # HIDDEN, not destroyed — the row still exists for the audit trail.
    still = f.db.execute(text("select deleted_at is not null from invitations where id = :i"),
                         {"i": inv["id"]}).scalar()
    assert still is True
    assert all(x["id"] != inv["id"]
               for x in f.client.get("/organization/invitations", headers=f.auth(f.admin)).json()["items"])


def test_resending_a_closed_row_behind_a_newer_open_one_is_refused_not_a_500(f):
    """Reopening a closed row moves it back into the open set, where the partial unique index
    applies. A newer open invitation for the same person is LEGAL (closed rows don't block a
    re-invite), so the resend must refuse with a sentence — not violate the index and 500."""
    email = f.new_email("reopen")
    first, _ = _invite(f, email=email, role="viewer")
    f.client.patch(f"/organization/invitations/{first['id']}", headers=f.auth(f.admin),
                   json={"action": "cancel"})
    second, _ = _invite(f, email=email, role="viewer")   # legal: the first is closed

    r = f.client.patch(f"/organization/invitations/{first['id']}", headers=f.auth(f.admin),
                       json={"action": "resend"})
    assert r.status_code == 409, r.text
    assert "newer open invitation" in r.text
    # The Session survived, so the newer row is still usable.
    assert f.client.patch(f"/organization/invitations/{second['id']}", headers=f.auth(f.admin),
                          json={"action": "resend"}).status_code == 200


def test_re_cancelling_is_refused_and_the_flags_say_so(f):
    """The can_* flags answer "may this change?", not "is it already there?" — a cancelled row
    must not advertise Cancel, and a second cancel must not silently answer 200."""
    inv, _ = _invite(f, email=f.new_email())
    c = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "cancel"})
    assert c.status_code == 200 and c.json()["can_cancel"] is False
    again = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                           json={"action": "cancel"})
    assert again.status_code == 409, "a second cancel must not report success"


def test_revoke_is_one_transaction(f):
    """The status change and the assignment removal must land together — a revoke that
    committed the status first and then failed would leave the row reading `revoked` while the
    person kept the role."""
    email = f.new_email("atomic")
    inv, token = _invite(f, email=email, event_id=str(f.event.id), event_role="moderator")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Mod", "password": "hunter2pw"})
    user = crud.user_in_org(f.db, f.org.id, email)

    r = f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                       json={"action": "revoke"})
    assert r.status_code == 200
    # Both writes are visible, and neither is visible without the other.
    assert f.db.execute(text("select status from invitations where id = :i"),
                        {"i": inv["id"]}).scalar() == "revoked"
    assert f.db.execute(
        text("select count(*) from event_assignments where user_id = :u and role = 'moderator'"),
        {"u": user.id}).scalar() == 0
    # Revoking twice must not report success a second time.
    assert f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                          json={"action": "revoke"}).status_code == 409


def test_a_hidden_row_does_not_block_re_inviting(f):
    email = f.new_email()
    inv, _ = _invite(f, email=email, role="viewer")
    f.client.delete(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin))
    r = f.client.post("/organization/invitations", headers=f.auth(f.admin),
                      json={"email": email, "role": "viewer"})
    assert r.status_code == 201, r.text


# ── Bulk ──────────────────────────────────────────────────────────────────────

def test_bulk_create_reports_per_address(f):
    good1, good2 = f.new_email("a"), f.new_email("b")
    r = f.client.post("/organization/invitations/bulk", headers=f.auth(f.admin),
                      json={"emails": [good1, good2, f.colleague.email, good1], "role": "viewer"})
    assert r.status_code == 201, r.text
    body = r.json()
    # good1 appears twice in the request and is deduped; the colleague is already a member.
    assert len(body["succeeded"]) == 2, body
    assert len(body["failed"]) == 1 and body["failed"][0]["email"] == f.colleague.email
    assert "already a member" in body["failed"][0]["reason"]


def test_bulk_survives_a_duplicate_without_poisoning_the_rest(f):
    """The per-email rollback is mandatory: without it the first IntegrityError leaves the
    Session unusable and every remaining address fails with an unrelated reason."""
    dup = f.new_email("dup")
    _invite(f, email=dup, role="viewer")
    fresh = [f.new_email(f"z{i}") for i in range(3)]
    r = f.client.post("/organization/invitations/bulk", headers=f.auth(f.admin),
                      json={"emails": [dup, *fresh], "role": "viewer"})
    body = r.json()
    assert len(body["succeeded"]) == 3, body
    assert len(body["failed"]) == 1


def test_bulk_action_resend_cancel_delete(f):
    ids = [_invite(f, email=f.new_email(f"m{i}"))[0]["id"] for i in range(3)]
    r = f.client.post("/organization/invitations/bulk-action", headers=f.auth(f.admin),
                      json={"action": "resend", "ids": ids})
    assert r.status_code == 200 and len(r.json()["succeeded"]) == 3, r.text

    # An accepted invitation cannot be resent, and the batch says which one.
    email = f.new_email("acc")
    inv, token = _invite(f, email=email)
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Acc", "password": "hunter2pw"})
    r = f.client.post("/organization/invitations/bulk-action", headers=f.auth(f.admin),
                      json={"action": "resend", "ids": [inv["id"], *ids]})
    body = r.json()
    assert len(body["succeeded"]) == 3 and len(body["failed"]) == 1
    assert body["failed"][0]["id"] == inv["id"]

    r = f.client.post("/organization/invitations/bulk-action", headers=f.auth(f.admin),
                      json={"action": "cancel", "ids": ids})
    assert len(r.json()["succeeded"]) == 3
    r = f.client.post("/organization/invitations/bulk-action", headers=f.auth(f.admin),
                      json={"action": "delete", "ids": ids})
    assert len(r.json()["succeeded"]) == 3


def test_bulk_is_bounded_and_org_scoped(f):
    assert f.client.post("/organization/invitations/bulk", headers=f.auth(f.admin),
                         json={"emails": [f"x{i}@example.com" for i in range(101)]}
                         ).status_code == 422
    r = f.client.post("/organization/invitations/bulk-action", headers=f.auth(f.admin),
                      json={"action": "cancel", "ids": [str(uuid.uuid4())]})
    assert r.json()["failed"][0]["reason"] == "Invitation not found"


# ── Delivery reporting ────────────────────────────────────────────────────────

def test_manual_invitation_sends_no_email_and_is_visibly_unsent(f):
    """sent_at IS NULL is what distinguishes a manual invitation from a mailed one — which is
    exactly why delivery is timestamps rather than a status."""
    inv, _ = _invite(f, email=f.new_email(), role="viewer", send_email=False)
    assert inv["delivery"] == "not_sent" and inv["sent_at"] is None
    assert inv["invite_url"], "a manual invitation's deliverable IS the link"


def test_a_failed_send_is_recorded_not_swallowed(f):
    """RESEND_API_KEY is blank in this suite, so every delivery attempt fails. That must show
    on the row — an invitation that looks sent but never left is unfixable by an operator."""
    inv, _ = _invite(f, email=f.new_email(), role="viewer", send_email=True)
    row = f.db.execute(text("select send_error, send_attempts, sent_at from invitations where id = :i"),
                       {"i": inv["id"]}).first()
    assert row.send_error is not None and row.send_attempts == 1 and row.sent_at is None
    fetched = f.client.get("/organization/invitations", headers=f.auth(f.admin),
                           params={"q": inv["email"]}).json()["items"][0]
    assert fetched["delivery"] == "failed"
    stats = f.client.get("/organization/invitations/stats", headers=f.auth(f.admin)).json()
    assert stats["failed_delivery"] >= 1


def test_delivery_is_only_confirmed_by_the_webhook(f):
    inv, _ = _invite(f, email=f.new_email(), role="viewer", send_email=False)
    f.db.execute(text("update invitations set provider_message_id = :m, sent_at = now(), send_error = null where id = :i"),
                 {"m": f"msg-{f.uniq}", "i": inv["id"]})
    f.db.flush()
    fetched = f.client.get("/organization/invitations", headers=f.auth(f.admin),
                           params={"q": inv["email"]}).json()["items"][0]
    assert fetched["delivery"] == "sent", "handed to the provider is not the same as delivered"

    assert crud.mark_delivered(f.db, f"msg-{f.uniq}") == 1
    fetched = f.client.get("/organization/invitations", headers=f.auth(f.admin),
                           params={"q": inv["email"]}).json()["items"][0]
    assert fetched["delivery"] == "delivered"
    # Idempotent: a replay inside the signature window is a no-op.
    assert crud.mark_delivered(f.db, f"msg-{f.uniq}") == 0
    # And it never touches the lifecycle.
    assert f.db.execute(text("select status from invitations where id = :i"),
                        {"i": inv["id"]}).scalar() == "pending"


def test_webhook_matching_is_by_message_id_not_recipient(f):
    """Matching on `to` would flip every open invitation for that address across ALL orgs — an
    unauthenticated cross-tenant write."""
    assert crud.mark_delivered(f.db, "no-such-message-id") == 0


# ── Registration interplay ────────────────────────────────────────────────────

def test_signing_up_with_a_pending_invitation_is_refused_not_consumed(f):
    """register must NOT be a second accept path.

    It briefly was, and that was a token bypass: RegisterIn carries no token and the route
    proves no ownership of the address, so anyone who knew an invited corporate address could
    take that seat with a password of their choosing. It also honoured a grant from a since-
    demoted inviter and wrote no audit row.

    So it refuses, and writes nothing — the invitation stays redeemable through its token.
    """
    email = f.new_email("signup")
    inv, token = _invite(f, email=email, event_id=str(f.event.id), event_role="moderator")
    r = f.client.post("/auth/register", json={
        "full_name": "Signed Up", "email": email, "password": "hunter2pw",
        "organization_name": "Their Own Company",
    })
    assert r.status_code == 409, r.text
    assert "invitation link" in r.text
    # Nothing was written: no user, no stray org, and the invitation is untouched.
    assert crud.find_user_by_email(f.db, email) is None
    assert f.db.execute(text("select count(*) from organizations where name = 'Their Own Company'")
                        ).scalar() == 0
    assert f.db.execute(text("select status from invitations where id = :i"),
                        {"i": inv["id"]}).scalar() == "pending"
    # And the real path still works afterwards.
    a = f.client.post("/organization/invitations/accept",
                      json={"token": token, "full_name": "Signed Up", "password": "hunter2pw"})
    assert a.status_code == 200, a.text
    user = crud.find_user_by_email(f.db, email)
    assert user.org_id == f.org.id and user.role == "moderator"


def test_register_cannot_bypass_the_token_after_the_inviter_is_demoted(f):
    """The concrete escalation the refusal closes: accept re-authorizes the grant, so a demoted
    inviter's org_admin invitation dies — register must not honour it either."""
    email = f.new_email("bypass")
    _, token = _invite(f, email=email, role="org_admin")
    f.admin.role = "host"          # a host may not grant org_admin
    f.db.flush()
    assert f.client.post("/organization/invitations/accept",
                         json={"token": token, "full_name": "M", "password": "attacker-pw"}
                         ).status_code == 400
    r = f.client.post("/auth/register", json={
        "full_name": "Attacker", "email": email, "password": "attacker-pw",
        "organization_name": "whatever"})
    assert r.status_code == 409, "register must not mint the seat the accept path refused"
    assert crud.find_user_by_email(f.db, email) is None


def test_registration_without_an_invitation_still_creates_an_org(f):
    email = f.new_email("solo")
    r = f.client.post("/auth/register", json={
        "full_name": "Solo Founder", "email": email, "password": "hunter2pw",
        "organization_name": f"Solo Co {f.uniq}",
    })
    assert r.status_code == 201, r.text
    user = crud.find_user_by_email(f.db, email)
    assert user.role == "org_admin" and user.org_id not in (f.org.id, f.other_org.id)


# ── Listing / filtering ───────────────────────────────────────────────────────

def test_list_filters_and_stats(f):
    a, _ = _invite(f, email=f.new_email("filt"), role="host")
    b, _ = _invite(f, email=f.new_email("filt"), event_id=str(f.event.id), event_role="speaker")
    ids = lambda r: {x["id"] for x in r.json()["items"]}  # noqa: E731
    H = f.auth(f.admin)

    assert {a["id"], b["id"]} <= ids(f.client.get("/organization/invitations", headers=H,
                                                  params={"status": "pending"}))
    assert b["id"] in ids(f.client.get("/organization/invitations", headers=H,
                                       params={"event_id": str(f.event.id)}))
    assert a["id"] not in ids(f.client.get("/organization/invitations", headers=H,
                                           params={"event_id": str(f.event.id)}))
    # The role facet looks at BOTH columns, because a row shows whichever it carries.
    assert a["id"] in ids(f.client.get("/organization/invitations", headers=H,
                                       params={"role": "host"}))
    assert b["id"] in ids(f.client.get("/organization/invitations", headers=H,
                                       params={"role": "speaker"}))
    assert a["id"] in ids(f.client.get("/organization/invitations", headers=H,
                                       params={"q": a["email"].split("@")[0]}))
    page = f.client.get("/organization/invitations", headers=H, params={"page_size": 1}).json()
    assert len(page["items"]) == 1 and page["total"] >= 2

    stats = f.client.get("/organization/invitations/stats", headers=H).json()
    assert stats.get("pending", 0) >= 2


def test_server_computes_the_action_flags(f):
    """The console shows a button only when the API would accept it — the client never
    re-implements the state machine."""
    inv, token = _invite(f, email=f.new_email())
    row = f.client.get("/organization/invitations", headers=f.auth(f.admin),
                       params={"q": inv["email"]}).json()["items"][0]
    assert row["can_resend"] and row["can_cancel"] and not row["can_revoke"]
    assert row["status_label"] == "Pending"

    f.client.post("/organization/invitations/reject", json={"token": token})
    row = f.client.get("/organization/invitations", headers=f.auth(f.admin),
                       params={"q": inv["email"]}).json()["items"][0]
    assert row["status_label"] == "Declined", "the stored 'rejected' must read as Declined"
    assert not row["can_resend"] and not row["can_cancel"] and not row["can_revoke"]


def test_audit_rows_are_written_for_every_transition(f):
    email = f.new_email("audit")
    inv, token = _invite(f, email=email, event_id=str(f.event.id), event_role="speaker")
    f.client.post("/organization/invitations/accept",
                  json={"token": token, "full_name": "Audited", "password": "hunter2pw"})
    f.client.patch(f"/organization/invitations/{inv['id']}", headers=f.auth(f.admin),
                   json={"action": "revoke"})
    actions = {r[0] for r in f.db.execute(text(
        "select action from audit_logs where target_id = :i"), {"i": inv["id"]}).all()}
    assert {"invitation.created", "invitation.accepted", "invitation.revoked"} <= actions, actions


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main():
    passed = failed = 0
    for fn in TESTS:
        # A fresh fixture per test so one failure cannot leak state into the next.
        f = Fixture()
        try:
            fn(f)
            passed += 1
            print(f"  ok    {fn.__name__}")
        except Exception as exc:  # noqa: BLE001 — report and continue
            failed += 1
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
        finally:
            f.close()
    print(f"\ninvitation module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
