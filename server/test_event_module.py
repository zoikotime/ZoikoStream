"""Event Management module — end-to-end checks through the real HTTP layer.

Real database, everything inside a transaction that is ALWAYS rolled back — same discipline
as test_org_overview.py and test_ops.py. Going through TestClient with real JWTs is the
point: these assertions are about routing, RBAC, tenant isolation, lifecycle guards and
response shape, and none of that is exercised by calling the crud functions directly.

test_events.py holds the PURE half (transition table, token hashing, access rules, duplicate
safety). This file holds the half that needs a request.

Run: `python test_event_module.py` (or pytest).
"""
import uuid

from sqlalchemy import text
from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import event as crud
from app.db import SessionLocal, get_db
from app.models import Organization, User
from app.security import create_access_token, hash_password

ROLES = ("host", "moderator", "speaker", "producer", "cohost", "panelist")

# Creating an event queues a real "event created" email (BackgroundTasks). TestClient runs
# background tasks synchronously, so without this every create in this file would POST to
# Resend and fail on the example.com recipients. Blank = the send is skipped and logged,
# which is app.email's documented degraded path.
settings.RESEND_API_KEY = ""


class Fixture:
    """Two organizations, three users, one client. Torn down by a rollback, so this can be
    run against a live database without leaving a row behind."""

    def __init__(self):
        self.db = SessionLocal()
        self.tx = self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        uniq = uuid.uuid4().hex[:8]

        self.org = Organization(name=f"Event Module Org {uniq}", status="active")
        self.other_org = Organization(name=f"Other Org {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Module Admin", f"adm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "Module Host", f"hst{uniq}", "host")
        self.outsider = self._user(self.other_org.id, "Outsider", f"out{uniq}", "org_admin")
        self.db.flush()

        # The session is shared with the app so the request handlers see (and roll back with)
        # these uncommitted rows.
        m.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(m.app)

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def close(self):
        m.app.dependency_overrides.clear()
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001 — teardown must not mask a test failure
            pass
        self.db.rollback()
        self.db.close()


def _event(f, headers, **overrides):
    body = {"title": f"Event {uuid.uuid4().hex[:6]}", "status": "draft", **overrides}
    r = f.client.post("/events", headers=headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── CRUD + the new fields ─────────────────────────────────────────────────────

def test_create_persists_every_new_field(f):
    H = f.auth(f.admin)
    ev = _event(f, H, visibility="invite_only", stream_quality="2k", replay_enabled=True,
                max_participants=500, access_password="hunter2", tags=["a", "b"])
    assert ev["stream_quality"] == "2k"
    assert ev["replay_enabled"] is True
    assert ev["max_participants"] == 500
    assert ev["visibility"] == "invite_only"
    assert ev["tags"] == ["a", "b"]
    # The passphrase is reported as a boolean and the hash is never serialized.
    assert ev["password_protected"] is True
    assert "access_password_hash" not in ev


def test_update_password_three_states(f):
    H = f.auth(f.admin)
    ev = _event(f, H, access_password="secret1")
    eid = ev["id"]
    # Omitted -> unchanged.
    r = f.client.patch(f"/events/{eid}", headers=H, json={"title": "Renamed"})
    assert r.json()["password_protected"] is True
    # "" -> cleared.
    r = f.client.patch(f"/events/{eid}", headers=H, json={"access_password": ""})
    assert r.json()["password_protected"] is False
    # value -> set.
    r = f.client.patch(f"/events/{eid}", headers=H, json={"access_password": "again12"})
    assert r.json()["password_protected"] is True


def test_list_enrichment_is_real_and_honest(f):
    H = f.auth(f.admin)
    ev = _event(f, H)
    r = f.client.get("/events", headers=H, params={"q": ev["title"]})
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["items"] if x["id"] == ev["id"])
    assert row["organization_name"] == f.org.name
    assert row["created_by_name"] == "Module Admin"
    assert isinstance(row["team_counts"], dict)
    assert row["access_link_count"] == 0
    assert row["has_recording"] is False and row["has_replay"] is False
    # No registrations table exists, so this must stay null rather than reporting a zero
    # that would read as "nobody signed up".
    assert row["registrations"] is None
    # Not on air -> no viewer figure is invented.
    assert row["current_viewers"] is None


def test_filters_and_pagination(f):
    H = f.auth(f.admin)
    a = _event(f, H, visibility="public", category="Webinar")
    b = _event(f, H, visibility="private", category="Workshop")

    ids = lambda r: {x["id"] for x in r.json()["items"]}  # noqa: E731
    assert a["id"] in ids(f.client.get("/events", headers=H, params={"visibility": "public"}))
    assert b["id"] not in ids(f.client.get("/events", headers=H, params={"visibility": "public"}))
    assert b["id"] in ids(f.client.get("/events", headers=H, params={"category": "Workshop"}))
    assert {a["id"], b["id"]} <= ids(f.client.get("/events", headers=H, params={"statuses": ["draft"]}))
    assert not ids(f.client.get("/events", headers=H, params={"statuses": ["live"]})) & {a["id"], b["id"]}

    r = f.client.get("/events", headers=H, params={"page_size": 1, "page": 1})
    body = r.json()
    assert len(body["items"]) == 1 and body["total"] >= 2 and body["page_size"] == 1

    cats = f.client.get("/events/categories", headers=H).json()
    assert "Webinar" in cats and "Workshop" in cats


def test_soft_delete_disappears_from_reads(f):
    H = f.auth(f.admin)
    ev = _event(f, H)
    assert f.client.delete(f"/events/{ev['id']}", headers=H).status_code == 204
    assert f.client.get(f"/events/{ev['id']}", headers=H).status_code == 404
    assert all(x["id"] != ev["id"] for x in f.client.get("/events", headers=H).json()["items"])


# ── Tenant isolation ──────────────────────────────────────────────────────────

def test_org_isolation_on_every_event_route(f):
    ev = _event(f, f.auth(f.admin))
    HO = f.auth(f.outsider)
    eid = ev["id"]
    # A cross-tenant id must be indistinguishable from a missing one — 404 everywhere,
    # never 403 (which would confirm the event exists).
    #
    # Each body below is VALID for its route on purpose: an invalid one is rejected by
    # request validation (422) before the handler runs, so it would never reach the
    # isolation check this test exists to prove.
    for method, path, body in [
        ("get", f"/events/{eid}", None),
        ("patch", f"/events/{eid}", {"title": "hax"}),
        ("delete", f"/events/{eid}", None),
        ("get", f"/events/{eid}/team", None),
        ("get", f"/events/{eid}/team/host", None),
        ("patch", f"/events/{eid}/team/host", {"user_ids": []}),
        ("post", f"/events/{eid}/team/host", {"user_id": str(uuid.uuid4())}),
        ("delete", f"/events/{eid}/team/host/{uuid.uuid4()}", None),
        ("get", f"/events/{eid}/access-links", None),
        ("post", f"/events/{eid}/access-links", {}),
        ("post", f"/events/{eid}/duplicate", {}),
        ("get", f"/events/{eid}/hosts", None),
        ("patch", f"/events/{eid}/hosts", {"user_ids": []}),
    ]:
        r = getattr(f.client, method)(path, headers=HO, **({} if body is None else {"json": body}))
        assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text[:120]}"
    assert all(x["id"] != eid for x in f.client.get("/events", headers=HO).json()["items"])


# ── RBAC ──────────────────────────────────────────────────────────────────────

def test_only_org_admin_may_manage(f):
    ev = _event(f, f.auth(f.admin))
    HM = f.auth(f.host)
    eid = ev["id"]
    assert f.client.post("/events", headers=HM, json={"title": "nope"}).status_code == 403
    assert f.client.delete(f"/events/{eid}", headers=HM).status_code == 403
    assert f.client.post(f"/events/{eid}/duplicate", headers=HM, json={}).status_code == 403
    assert f.client.patch(f"/events/{eid}/team/host", headers=HM, json={"user_ids": []}).status_code == 403
    assert f.client.post(f"/events/{eid}/access-links", headers=HM, json={}).status_code == 403
    assert f.client.post("/events/bulk", headers=HM, json={"action": "publish", "ids": [eid]}).status_code == 403
    # Reads stay open to any member of the org.
    assert f.client.get(f"/events/{eid}", headers=HM).status_code == 200
    assert f.client.get(f"/events/{eid}/team", headers=HM).status_code == 200


def test_assigned_host_may_edit_but_not_manage(f):
    H = f.auth(f.admin)
    ev = _event(f, H)
    eid = ev["id"]
    f.client.post(f"/events/{eid}/team/host", headers=H, json={"user_id": str(f.host.id)})
    HM = f.auth(f.host)
    # _can_edit admits an assigned host …
    assert f.client.patch(f"/events/{eid}", headers=HM, json={"title": "Host edit"}).status_code == 200
    # … but assignment never escalates to org-admin powers.
    assert f.client.delete(f"/events/{eid}", headers=HM).status_code == 403


def test_unassigned_host_cannot_edit_someone_elses_event(f):
    ev = _event(f, f.auth(f.admin))
    r = f.client.patch(f"/events/{ev['id']}", headers=f.auth(f.host), json={"title": "no"})
    assert r.status_code == 403


# ── Team assignment ───────────────────────────────────────────────────────────

def test_team_covers_six_roles_with_duplicate_prevention(f):
    H = f.auth(f.admin)
    eid = _event(f, H)["id"]

    for role in ROLES:
        r = f.client.post(f"/events/{eid}/team/{role}", headers=H, json={"user_id": str(f.host.id)})
        assert r.status_code == 201, (role, r.text)
        assert set(r.json()) == set(ROLES), "every role must be in the aggregate payload"
        assert len(r.json()[role]) == 1

    # Idempotent add — a double submit is not a duplicate row and not an error.
    r = f.client.post(f"/events/{eid}/team/host", headers=H, json={"user_id": str(f.host.id)})
    assert r.status_code == 201 and len(r.json()["host"]) == 1
    assert f.db.execute(
        text("select count(*) from event_assignments where event_id = :e and role = 'host'"),
        {"e": eid},
    ).scalar() == 1

    # Replace semantics: PATCH sets the whole set, so an empty list clears the role.
    r = f.client.patch(f"/events/{eid}/team/speaker", headers=H, json={"user_ids": []})
    assert r.status_code == 200 and r.json()["speaker"] == []

    r = f.client.delete(f"/events/{eid}/team/host/{f.host.id}", headers=H)
    assert r.status_code == 200 and r.json()["host"] == []
    # Removing again is a 404, so the console can tell "already gone" from "just removed".
    assert f.client.delete(f"/events/{eid}/team/host/{f.host.id}", headers=H).status_code == 404


def test_team_role_and_membership_validation(f):
    H = f.auth(f.admin)
    eid = _event(f, H)["id"]
    # Unknown role -> 422 (the vocabulary is closed).
    assert f.client.post(f"/events/{eid}/team/wizard", headers=H, json={"user_id": str(f.host.id)}).status_code == 422
    # Cross-tenant assignee -> 400 naming the id, never a silent success.
    r = f.client.post(f"/events/{eid}/team/host", headers=H, json={"user_id": str(f.outsider.id)})
    assert r.status_code == 400 and str(f.outsider.id) in r.text


def test_legacy_per_role_routes_still_work(f):
    H = f.auth(f.admin)
    eid = _event(f, H)["id"]
    assert f.client.patch(f"/events/{eid}/hosts", headers=H, json={"user_ids": [str(f.host.id)]}).status_code == 200
    assert len(f.client.get(f"/events/{eid}/hosts", headers=H).json()) == 1
    # Both surfaces read the same table, so they can never disagree.
    assert len(f.client.get(f"/events/{eid}/team", headers=H).json()["host"]) == 1


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def test_lifecycle_transitions_through_the_api(f):
    H = f.auth(f.admin)
    eid = _event(f, H)["id"]
    patch = lambda s: f.client.patch(f"/events/{eid}", headers=H, json={"status": s})  # noqa: E731

    assert patch("live").status_code == 400            # not published
    assert patch("published").status_code == 200
    assert patch("draft").status_code == 200           # unpublish
    assert patch("published").status_code == 200
    assert patch("live").status_code == 200
    assert patch("paused").status_code == 200
    assert patch("archived").status_code == 400        # still on air
    assert patch("ended").status_code == 200           # ending from a pause works
    assert patch("archived").status_code == 200


def test_publish_requires_a_title_through_the_api(f):
    H = f.auth(f.admin)
    r = f.client.post("/events", headers=H, json={"status": "draft"})   # no title
    eid = r.json()["id"]
    bad = f.client.patch(f"/events/{eid}", headers=H, json={"status": "published"})
    assert bad.status_code == 400 and "title" in bad.text.lower()
    # Setting the title in the SAME request is allowed — the guard sees the merged value.
    ok = f.client.patch(f"/events/{eid}", headers=H, json={"status": "published", "title": "Now titled"})
    assert ok.status_code == 200


# ── Bulk ──────────────────────────────────────────────────────────────────────

def test_bulk_reports_per_id_outcomes(f):
    H = f.auth(f.admin)
    good = _event(f, H)["id"]
    untitled = f.client.post("/events", headers=H, json={"status": "draft"}).json()["id"]

    r = f.client.post("/events/bulk", headers=H, json={"action": "publish", "ids": [good, untitled]})
    assert r.status_code == 200
    body = r.json()
    # A partial failure is stated, not rounded up to success or thrown away.
    assert body["succeeded"] == [good]
    assert len(body["failed"]) == 1 and body["failed"][0]["id"] == untitled
    assert "title" in body["failed"][0]["reason"].lower()

    assert f.client.post("/events/bulk", headers=H, json={"action": "unpublish", "ids": [good]}).json()["succeeded"] == [good]
    assert f.client.post("/events/bulk", headers=H, json={"action": "delete", "ids": [untitled]}).json()["succeeded"] == [untitled]
    # A cross-tenant or missing id is a per-id failure, not a 404 for the whole request.
    missing = str(uuid.uuid4())
    assert f.client.post("/events/bulk", headers=H, json={"action": "publish", "ids": [missing]}).json()["failed"][0]["id"] == missing
    # Bounded transaction: one frame cannot ask for an unbounded write.
    assert f.client.post("/events/bulk", headers=H,
                         json={"action": "publish", "ids": [missing] * 101}).status_code == 422


# ── Duplicate ─────────────────────────────────────────────────────────────────

def test_duplicate_copies_config_but_never_secrets(f):
    H = f.auth(f.admin)
    src = _event(f, H, visibility="invite_only", stream_quality="2k", replay_enabled=True,
                 access_password="hunter2", start_time="2030-01-01T10:00:00Z",
                 end_time="2030-01-01T11:00:00Z", status="published")
    eid = src["id"]
    f.client.post(f"/events/{eid}/team/moderator", headers=H, json={"user_id": str(f.host.id)})
    f.client.post(f"/events/{eid}/access-links", headers=H, json={"label": "Press"})

    clone = f.client.post(f"/events/{eid}/duplicate", headers=H, json={"copy_team": True}).json()
    assert clone["status"] == "draft"                       # never inherits a live status
    assert clone["start_time"] is None                      # never inherits a stale date
    assert clone["password_protected"] is False             # never inherits a secret
    assert clone["slug"] != src["slug"]                     # unique within the org
    assert clone["stream_quality"] == "2k" and clone["replay_enabled"] is True
    assert clone["visibility"] == "invite_only"
    assert len(f.client.get(f"/events/{clone['id']}/team", headers=H).json()["moderator"]) == 1
    # Links belong to one event's audience and are never cloned.
    assert f.client.get(f"/events/{clone['id']}/access-links", headers=H).json() == []

    plain = f.client.post(f"/events/{eid}/duplicate", headers=H, json={"copy_team": False}).json()
    assert f.client.get(f"/events/{plain['id']}/team", headers=H).json()["moderator"] == []


# ── Viewer access links ───────────────────────────────────────────────────────

def test_access_link_lifecycle_and_redemption(f):
    H, HO = f.auth(f.admin), f.auth(f.outsider)
    # invite_only + published: reachable ONLY with a link (or by the organizing org).
    ev = _event(f, H, visibility="invite_only", title="Invite Only", status="published")
    eid = ev["id"]

    created = f.client.post(f"/events/{eid}/access-links", headers=H,
                            json={"label": "Press", "expires_in_days": 7}).json()
    raw = created["token"]
    assert raw and f"/events/{eid}/watch" in created["url"]

    # Only the hash is persisted; the list response never re-serves the secret.
    stored = f.db.execute(text("select token_hash from event_access_links where id = :i"),
                          {"i": created["id"]}).scalar()
    assert stored == crud._hash_token(raw) != raw
    assert f.client.get(f"/events/{eid}/access-links", headers=H).json()[0]["token"] is None

    # Redemption: no token -> the event does not exist for this caller.
    assert f.client.get(f"/events/{eid}/viewer", headers=HO).status_code == 404
    r = f.client.get(f"/events/{eid}/viewer", headers=HO, params={"token": raw})
    assert r.status_code == 200 and r.json()["access"]["basis"] == "access_link"
    assert f.client.get(f"/events/{eid}/access-links", headers=H).json()[0]["uses"] >= 1

    # Rotation invalidates the previous secret immediately, on the same row.
    rotated = f.client.post(f"/events/{eid}/access-links/{created['id']}/rotate", headers=H,
                            json={"expires_in_days": 7}).json()
    assert rotated["token"] != raw and rotated["label"] == "Press"
    assert f.client.get(f"/events/{eid}/viewer", headers=HO, params={"token": raw}).status_code == 404
    assert f.client.get(f"/events/{eid}/viewer", headers=HO, params={"token": rotated["token"]}).status_code == 200

    # Revocation is soft — the row survives for the audit trail, the token stops working.
    assert f.client.post(f"/events/{eid}/access-links/{created['id']}/revoke", headers=H).status_code == 200
    assert f.client.get(f"/events/{eid}/viewer", headers=HO, params={"token": rotated["token"]}).status_code == 404
    assert f.client.get(f"/events/{eid}/access-links", headers=H).json()[0]["revoked_at"] is not None


def test_access_link_is_scoped_to_its_own_event(f):
    H, HO = f.auth(f.admin), f.auth(f.outsider)
    a = _event(f, H, visibility="invite_only", status="published")
    b = _event(f, H, visibility="invite_only", status="published")
    token = f.client.post(f"/events/{a['id']}/access-links", headers=H, json={}).json()["token"]
    # A valid link for event A must not open event B.
    assert f.client.get(f"/events/{a['id']}/viewer", headers=HO, params={"token": token}).status_code == 200
    assert f.client.get(f"/events/{b['id']}/viewer", headers=HO, params={"token": token}).status_code == 404


def test_expired_link_is_refused(f):
    H, HO = f.auth(f.admin), f.auth(f.outsider)
    ev = _event(f, H, visibility="invite_only", status="published")
    created = f.client.post(f"/events/{ev['id']}/access-links", headers=H, json={"expires_in_days": 1}).json()
    # Move the expiry into the past — the same row, now stale.
    f.db.execute(text("update event_access_links set expires_at = now() - interval '1 day' where id = :i"),
                 {"i": created["id"]})
    f.db.flush()
    assert f.client.get(f"/events/{ev['id']}/viewer", headers=HO,
                        params={"token": created["token"]}).status_code == 404


def test_passphrase_gates_media_not_the_page(f):
    H, HO = f.auth(f.admin), f.auth(f.outsider)
    ev = _event(f, H, visibility="public", status="published", access_password="hunter2")
    eid = ev["id"]

    # The page still renders for an outsider — it reports the requirement instead of 404ing,
    # so the attendee gets a prompt rather than a dead end.
    r = f.client.get(f"/events/{eid}/viewer", headers=HO)
    assert r.status_code == 200
    assert r.json()["access"]["password_required"] is True
    assert r.json()["access"]["password_satisfied"] is False
    assert f.client.get(f"/events/{eid}/viewer", headers=HO,
                        params={"password": "wrong"}).json()["access"]["password_satisfied"] is False
    assert f.client.get(f"/events/{eid}/viewer", headers=HO,
                        params={"password": "hunter2"}).json()["access"]["password_satisfied"] is True

    # The organizing org is exempt from its own audience gate.
    assert f.client.get(f"/events/{eid}/viewer", headers=H).json()["access"]["password_satisfied"] is True


def test_playback_requires_live_and_the_passphrase(f):
    H = f.auth(f.admin)
    ev = _event(f, H, visibility="public", status="published")
    eid = ev["id"]
    # Not live -> 409 with a reason, never a token.
    r = f.client.get(f"/events/{eid}/playback", headers=H)
    assert r.status_code == 409 and "live" in r.json()["detail"].lower()

    f.client.patch(f"/events/{eid}", headers=H, json={"status": "live"})
    f.client.patch(f"/events/{eid}", headers=H, json={"access_password": "hunter2"})
    # An outsider with no passphrase is refused the MEDIA with a 403 (prompt), not a 404.
    r = f.client.get(f"/events/{eid}/playback", headers=f.auth(f.outsider))
    assert r.status_code in (403, 409)   # 409 when LiveKit is unconfigured on this deployment
    if r.status_code == 403:
        assert "passphrase" in r.json()["detail"].lower()


def test_draft_event_is_invisible_to_attendees(f):
    H = f.auth(f.admin)
    ev = _event(f, H, visibility="public")   # draft
    # A draft does not exist for the audience, even though it is public and even for its own
    # organization — the landing endpoint is not an existence oracle for unpublished work.
    assert f.client.get(f"/events/{ev['id']}/viewer", headers=H).status_code == 404


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
    print(f"\nevent module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
