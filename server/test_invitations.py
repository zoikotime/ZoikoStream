"""Self-check for the invitation token logic, the status transition table, and endpoint
authorization. Pure — the crud tests fake the Session, and the gating test never reaches a
query (auth is decided before any DB work).

The DB-backed half (full lifecycle, RBAC, tenant isolation, existing/new user, bulk, races,
webhook handling) lives in test_invitation_module.py.

Run: `python test_invitations.py` (or pytest).
"""
import typing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.testclient import TestClient

import app.main as m
from app.crud import organization as crud
from app.models import (
    ASSIGNMENT_ROLES,
    INVITATION_STATUSES,
    INVITE_EVENT_ROLES,
    INVITE_PLATFORM_ROLES,
    MAX_RESENDS,
    OPEN_STATUSES,
    ORG_ASSIGNABLE_ROLES,
    RESENDABLE_STATUSES,
    invitation_transition_error,
    status_label,
)
from app.routers.organization import router as orgr


class FakeDB:
    """Enough Session surface for the pure crud helpers."""

    def __init__(self, scalar_result=None):
        self._scalar = scalar_result
        self.committed = False

    def add(self, _): pass
    def flush(self): pass
    def commit(self): self.committed = True
    def refresh(self, _): pass
    def scalar(self, _stmt): return self._scalar
    def execute(self, _stmt): return SimpleNamespace(rowcount=1)


# ── token security ────────────────────────────────────────────────────────────

def test_token_is_hashed_not_stored():
    inv, raw = crud.create_invitation(FakeDB(), org_id="o1", email="A@X.com",
                                      role="host", invited_by_id="u1")
    assert len(raw) >= 32                       # secrets.token_urlsafe(32) = 256 bits
    assert inv.token_hash == crud._hash_token(raw)
    assert inv.token_hash != raw                # the raw token is NEVER stored
    assert len(inv.token_hash) == 64            # sha256 hex
    assert inv.status == "pending" and inv.email == "a@x.com"  # email normalized
    assert inv.expires_at is not None


def test_hash_is_deterministic_and_collision_free():
    assert crud._hash_token("abc") == crud._hash_token("abc")
    assert crud._hash_token("abc") != crud._hash_token("abd")
    a, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
    b, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
    assert a.token_hash != b.token_hash, "two invitations must never share a token"


def test_event_invitation_carries_both_roles():
    inv, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "speaker", "u1",
                                   event_id="e1", event_role="panelist", message="hi")
    assert inv.event_id == "e1" and inv.event_role == "panelist"
    # The platform role and the event role are separate columns on purpose: the platform role
    # is a ceiling, the assignment is the grant.
    assert inv.role == "speaker"
    assert inv.message == "hi"


# ── resend guard ──────────────────────────────────────────────────────────────

def test_resend_rotates_token_and_clears_delivery_facts():
    inv, raw1 = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
    inv.status = "expired"
    inv.send_error = "bounced"
    inv.sent_at = datetime.now(timezone.utc)
    inv.delivered_at = datetime.now(timezone.utc)
    inv.resend_count = 0
    inv2, raw2 = crud.resend_invitation(FakeDB(), inv)
    assert raw2 != raw1 and inv2.token_hash == crud._hash_token(raw2)
    assert inv2.status == "pending" and inv2.accepted_at is None
    # Delivery facts describe the PREVIOUS attempt — leaving them would have the console
    # reporting the old mail's fate for the new link.
    assert inv2.send_error is None and inv2.sent_at is None and inv2.delivered_at is None
    assert inv2.send_attempts == 0 and inv2.resend_count == 1
    assert inv2.last_sent_at is not None


def test_resend_refuses_terminal_states():
    """The guard lives in crud, not the router: create, single resend and BULK resend all pass
    through here, so a router-only check would be bypassed by the bulk endpoint."""
    for state in ("accepted", "rejected", "revoked"):
        inv, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
        inv.status = state
        inv.resend_count = 0
        try:
            crud.resend_invitation(FakeDB(), inv)
            raise AssertionError(f"resend from {state} must be refused")
        except crud.InvitationStateError:
            pass
    for state in RESENDABLE_STATUSES:
        inv, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
        inv.status = state
        inv.resend_count = 0
        crud.resend_invitation(FakeDB(), inv)   # must not raise


def test_resend_is_capped():
    inv, _ = crud.create_invitation(FakeDB(), "o1", "a@x.com", "viewer", "u1")
    inv.status = "pending"
    inv.resend_count = MAX_RESENDS
    try:
        crud.resend_invitation(FakeDB(), inv)
        raise AssertionError("resend past the cap must be refused")
    except crud.InvitationStateError:
        pass


# ── transition table ──────────────────────────────────────────────────────────

def test_open_state_is_exactly_one():
    """Every duplicate check, the token resolver and both partial unique indexes read from
    OPEN_STATUSES. More than one open state and an emailed invitation becomes unredeemable."""
    assert OPEN_STATUSES == ("pending",)


def test_terminal_states_cannot_be_reopened():
    # Declining and revoking are answers. Re-asking must create a NEW row so the answer
    # survives in the history rather than being overwritten.
    for state in ("rejected", "revoked"):
        for target in INVITATION_STATUSES:
            if target == state:
                continue
            assert invitation_transition_error(state, target, has_event=True), f"{state}->{target}"


def test_accepted_only_moves_to_revoked_and_only_with_an_event():
    assert invitation_transition_error("accepted", "revoked", has_event=True) is None
    # Revoking an ORG invitation would mean deleting a User, bypassing the self-deletion and
    # super-admin guards on DELETE /organization/users/{id}.
    assert invitation_transition_error("accepted", "revoked", has_event=False)
    for target in ("pending", "cancelled", "expired", "rejected"):
        assert invitation_transition_error("accepted", target, has_event=True), target


def test_expired_and_cancelled_are_reopenable():
    # The most common real resend: the invitation lapsed and the admin sends a fresh link.
    assert invitation_transition_error("expired", "pending", has_event=False) is None
    assert invitation_transition_error("cancelled", "pending", has_event=False) is None
    # cancelled -> expired would be a laundering route back into pending.
    assert invitation_transition_error("cancelled", "expired", has_event=False)


def test_pending_transitions():
    for target in ("accepted", "rejected", "expired", "cancelled"):
        assert invitation_transition_error("pending", target, has_event=True) is None, target
    assert invitation_transition_error("pending", "revoked", has_event=True), "must accept first"


def test_no_op_transition_is_allowed():
    for state in INVITATION_STATUSES:
        assert invitation_transition_error(state, state, has_event=True) is None


def test_unknown_statuses_are_refused():
    assert invitation_transition_error("pending", "banana", has_event=False)
    assert invitation_transition_error("banana", "pending", has_event=False)


def test_declined_is_the_label_for_rejected():
    """The product says "Declined"; the column stores "rejected" so no migration is needed.
    The mapping happens once, at the API boundary."""
    assert status_label("rejected") == "Declined"
    assert status_label("revoked") == "Revoked"
    assert status_label("pending") == "Pending"


# ── who may grant what ────────────────────────────────────────────────────────

def test_invite_tables_cover_the_spec_rules():
    """A rank ladder CANNOT express these: _ROLE_RANK puts moderator(2) below host(3), so
    "never grant above your own rank" would happily let a host invite a moderator."""
    assert "moderator" not in INVITE_EVENT_ROLES["host"], "hosts must not invite moderators"
    assert "host" not in INVITE_EVENT_ROLES["moderator"], "moderators must not invite hosts"
    assert "host" not in INVITE_EVENT_ROLES["host"], "a host must not mint another host"
    # Nobody below org_admin may grant org_admin.
    for role in ("host", "moderator"):
        assert "org_admin" not in INVITE_PLATFORM_ROLES[role]
    # super_admin is platform-only and never invitable through the org API.
    for allowed in INVITE_PLATFORM_ROLES.values():
        assert "super_admin" not in allowed


def test_invite_tables_are_subsets_of_the_real_role_sets():
    for role, allowed in INVITE_PLATFORM_ROLES.items():
        assert set(allowed) <= set(ORG_ASSIGNABLE_ROLES), role
    for role, allowed in INVITE_EVENT_ROLES.items():
        assert set(allowed) <= set(ASSIGNMENT_ROLES), role


def test_default_platform_role_never_inflates():
    """A platform role outlives an assignment and applies to every OTHER event in the org, so
    picking "cohost" must not silently hand out host."""
    from app.models import DEFAULT_PLATFORM_ROLE_FOR_EVENT_ROLE as DEFAULTS
    from app.security import _ROLE_RANK

    assert set(DEFAULTS) == set(ASSIGNMENT_ROLES), "every event role needs a default"
    for event_role in ("cohost", "producer", "panelist"):
        # models/event.py states these carry no broadcast authority — the default must agree.
        assert _ROLE_RANK[DEFAULTS[event_role]] <= _ROLE_RANK["speaker"], event_role


# ── schemas ───────────────────────────────────────────────────────────────────

def test_expire_is_not_an_admin_action():
    """Expiry is a fact about the clock. An admin-triggered expire was a laundering path:
    expire a declined row, then resend it."""
    from app.schemas.organization import InvitationAction

    actions = set(typing.get_args(InvitationAction.model_fields["action"].annotation))
    assert actions == {"resend", "cancel", "revoke"}, actions


def test_accept_password_is_optional():
    """An address that already has credentials supplies none — the router refuses to change an
    existing account's password from an emailed link."""
    from app.schemas.organization import InvitationAccept

    ok = InvitationAccept(token="x" * 20)
    assert ok.password is None and ok.full_name is None


def test_preview_never_carries_the_inviter_email_or_ids():
    from app.schemas.organization import InvitationPreview

    fields = set(InvitationPreview.model_fields)
    for leak in ("id", "invited_by_email", "token", "invite_token", "org_id", "event_id",
                 "email"):
        assert leak not in fields, f"the public preview must not expose {leak}"
    assert "email_hint" in fields, "the invitee still needs to know WHICH address was invited"


# ── endpoint authorization ────────────────────────────────────────────────────

def test_authorization_gating():
    """Every /organization route refuses an anonymous caller, except the invitee's three.

    HTTPBearer(auto_error=True) answers 403 when the header is ABSENT and 401 when it is
    present but invalid — both are refusals, so both are accepted here. Asserting only 401
    (as this test used to) made it die on the first route and silently stop checking the rest.
    """
    client = TestClient(m.app)
    public = {
        "/organization/invitations/accept",
        "/organization/invitations/reject",
        "/organization/invitations/preview",
    }
    problems = []
    for r in orgr.routes:
        for method in sorted(x for x in r.methods if x != "HEAD"):
            path = (r.path
                    .replace("{user_id}", "11111111-1111-1111-1111-111111111111")
                    .replace("{invitation_id}", "11111111-1111-1111-1111-111111111111"))
            resp = client.request(method, path, json={})
            if r.path in public:
                # Reachable without a token -> the failure must be about the BODY/QUERY
                # (missing token) or the token's validity, never authentication.
                if resp.status_code not in (400, 422):
                    problems.append(f"{method} {r.path} should be public, got {resp.status_code}")
            elif resp.status_code not in (401, 403):
                problems.append(f"{method} {r.path} unprotected: {resp.status_code}")
    assert not problems, "\n".join(problems)


def test_public_refusals_are_indistinguishable():
    """A random token must reveal exactly one bit: it worked, or it didn't."""
    client = TestClient(m.app)
    bad = "z" * 43
    accept = client.post("/organization/invitations/accept", json={"token": bad})
    reject = client.post("/organization/invitations/reject", json={"token": bad})
    assert accept.status_code == reject.status_code == 400
    assert accept.json()["detail"] == reject.json()["detail"], \
        "accept and decline must not be distinguishable by their refusal"
    # The preview refuses the same way, and never says why.
    preview = client.get("/organization/invitations/preview", params={"token": bad})
    assert preview.status_code == 400
    detail = preview.json()["detail"].lower()
    for leak in ("expired", "used", "revoked", "cancelled", "not found", "unknown"):
        assert leak not in detail, f"the refusal must not say '{leak}'"


# ── webhook signature verification ────────────────────────────────────────────

def test_svix_verification():
    import base64
    import hashlib
    import hmac

    from app.routers.webhooks import _verify

    secret_raw = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(secret_raw).decode()
    ts = str(int(datetime.now(timezone.utc).timestamp()))
    body = '{"type":"email.delivered","data":{"email_id":"abc"}}'
    sig = base64.b64encode(
        hmac.new(secret_raw, f"msg_1.{ts}.{body}".encode(), hashlib.sha256).digest()
    ).decode()

    assert _verify(secret, "msg_1", ts, f"v1,{sig}", body) is True
    # Multiple space-separated entries exist during key rotation — all must be tried.
    assert _verify(secret, "msg_1", ts, f"v1,other v1,{sig}", body) is True
    # Tamper with any signed component and it must fail.
    assert _verify(secret, "msg_2", ts, f"v1,{sig}", body) is False
    assert _verify(secret, "msg_1", ts, f"v1,{sig}", body + " ") is False
    assert _verify(secret, "msg_1", ts, "v1,deadbeef", body) is False
    assert _verify(secret, "msg_1", ts, "", body) is False
    # An unknown version prefix is not a valid signature.
    assert _verify(secret, "msg_1", ts, f"v2,{sig}", body) is False
    # Replay outside the tolerance window is refused.
    old = str(int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()))
    old_sig = base64.b64encode(
        hmac.new(secret_raw, f"msg_1.{old}.{body}".encode(), hashlib.sha256).digest()
    ).decode()
    assert _verify(secret, "msg_1", old, f"v1,{old_sig}", body) is False
    # A non-numeric timestamp is a malformed request, not a 500.
    assert _verify(secret, "msg_1", "not-a-number", f"v1,{sig}", body) is False
    # A malformed secret fails closed instead of raising.
    assert _verify("whsec_!!!not-base64!!!", "msg_1", ts, f"v1,{sig}", body) is False


def test_webhook_refuses_when_unconfigured():
    """Mirrors the LiveKit webhook: refuse an unsigned body rather than accept-and-ignore."""
    from app.config import settings

    original = settings.RESEND_WEBHOOK_SECRET
    settings.RESEND_WEBHOOK_SECRET = ""
    try:
        resp = TestClient(m.app).post("/webhooks/resend", json={"type": "email.delivered"})
        assert resp.status_code == 503, resp.status_code
    finally:
        settings.RESEND_WEBHOOK_SECRET = original


# ── misc ──────────────────────────────────────────────────────────────────────

def test_unique_username_appends_suffix():
    taken = {"bob", "bob1"}
    orig = crud.username_taken
    crud.username_taken = lambda db, name: name.lower() in taken
    try:
        assert crud.unique_username(FakeDB(), "bob") == "bob2"
        assert crud.unique_username(FakeDB(), "alice") == "alice"
    finally:
        crud.username_taken = orig


def test_build_member_does_not_commit():
    """The accept path commits ONCE, together with the claim and the event assignment, so a
    failure anywhere cannot leave a half-accepted invitation."""
    inv = SimpleNamespace(org_id="o1", email="a@x.com", role="moderator")
    db = FakeDB()
    user = crud.build_member(db, inv, "Ann", "ann", "hash")
    assert user.org_id == "o1" and user.role == "moderator" and user.is_active is True
    assert db.committed is False, "build_member must leave the commit to its caller"


def test_soft_delete_sets_marker_and_deactivates():
    u = SimpleNamespace(is_active=True, deleted_at=None)
    crud.soft_delete_user(FakeDB(), u)
    assert u.is_active is False and u.deleted_at is not None


def test_reactivate_never_touches_the_password():
    """Re-admitting a removed member must not let a token holder reset their credentials."""
    u = SimpleNamespace(is_active=False, deleted_at=datetime.now(timezone.utc),
                        role="viewer", password_hash="original")
    crud.reactivate_user(FakeDB(), u, role="host")
    assert u.deleted_at is None and u.is_active is True and u.role == "host"
    assert u.password_hash == "original"


def test_mask_email():
    from app.routers.organization import _mask_email

    assert _mask_email("bob@example.com") == "b**@example.com"
    assert _mask_email("a@example.com") == "a*@example.com"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("invitations self-check passed")
