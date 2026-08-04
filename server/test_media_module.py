"""Recording & Media Library — server-side checks.

Real database inside a transaction that is ALWAYS rolled back, same discipline as the host, event,
invitation, moderator, speaker and attendee suites. The request session is the non-closing proxy so
the REST routes' commits become flushes and nothing survives the rollback.

The weight is on the four things a media module can get catastrophically wrong:
  * ISOLATION — a recording is an organization's private asset; a foreign id must 404, never 403.
  * DOWNLOADS — "disabled", "expired", "wrong role" and "wrong passphrase" must each actually stop
    a file being handed out, and the URL must never be constructible without going through policy.
  * DELETION — soft delete must be reversible, purge must report what happened to the BYTES, and a
    duplicate sharing an object must stop that object being deleted underneath it.
  * HONESTY — a row with no file must never claim to be playable, and a missing transcript or AI
    provider must be reported rather than faked.

Run: `python test_media_module.py` (or pytest).
"""
import asyncio
import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import media as crud
from app.db import SessionLocal, get_db
from app.models import (
    AnalyticsSnapshot, AuditLog, BroadcastSession, Event, EventAssignment, LiveAnnouncement,
    LiveMessage, LivePoll, LiveQuestion, LiveRecording, MediaFolder, MediaMark, Organization, User,
)
from app.security import create_access_token, hash_password
from app.services import bus, media as media_svc, moderation as mod, storage
from app.services import broadcast

settings.RESEND_API_KEY = ""

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Ada Lovelace>Welcome to the quarterly review.

2
00:00:05.500 --> 00:00:09.000
Grace Hopper: Revenue is up eleven percent on the roadmap work.
"""


class _NonClosing:
    """A Session whose close() and commit() are neutered — see test_host_module for why."""

    def __init__(self, session):
        self._s = session

    def close(self):
        pass

    def commit(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


class Fixture:
    def __init__(self):
        self.db = SessionLocal()
        self.tx = self.db.begin_nested() if self.db.in_transaction() else self.db.begin()
        uniq = uuid.uuid4().hex[:8]
        self.uniq = uniq
        self.now = datetime.now(timezone.utc)

        self.org = Organization(name=f"Media Org {uniq}", status="active", storage_quota_gb=10)
        self.other_org = Organization(name=f"Media Other {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Org Admin", f"madm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "The Host", f"mhost{uniq}", "host")
        self.moderator = self._user(self.org.id, "The Moderator", f"mmod{uniq}", "moderator")
        self.speaker = self._user(self.org.id, "The Speaker", f"mspk{uniq}", "speaker")
        self.viewer = self._user(self.other_org.id, "Outside Viewer", f"mvw{uniq}", "viewer")
        self.rival = self._user(self.other_org.id, "Rival Admin", f"mriv{uniq}", "org_admin")
        self.db.flush()

        self.event = Event(org_id=self.org.id, created_by=self.admin.id, title=f"Media Event {uniq}",
                           slug=f"media-event-{uniq}", status="ended", visibility="public",
                           replay_enabled=True, start_time=self.now - timedelta(hours=2),
                           end_time=self.now - timedelta(hours=1), category="Town Hall",
                           tags=["quarterly"])
        self.db.add(self.event)
        self.db.flush()
        self.db.add_all([
            EventAssignment(event_id=self.event.id, user_id=self.host.id, role="host"),
            EventAssignment(event_id=self.event.id, user_id=self.speaker.id, role="speaker"),
        ])
        self.db.flush()

        proxy = _NonClosing(self.db)
        m.app.dependency_overrides[get_db] = lambda: proxy
        self.client = TestClient(m.app)

        self._patched = []
        for module in (mod, broadcast):
            if hasattr(module, "SessionLocal"):
                self._patched.append((module, module.SessionLocal))
                module.SessionLocal = lambda _p=proxy: _p

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def recording(self, *, status="stopped", size=5_000_000, key="set", event=None, **cols):
        """A finished capture with a real file, unless told otherwise."""
        event = event or self.event
        rid = uuid.uuid4()
        r = LiveRecording(
            id=rid, event_id=event.id, org_id=event.org_id, status=status, quality="1080p",
            started_at=self.now - timedelta(hours=2), stopped_at=self.now - timedelta(hours=1),
            size_bytes=size, duration_ms=3_600_000, enforced=bool(size),
            storage_key=(storage.recording_key(event.org_id, event.id, rid) if key == "set" else key),
            created_by=self.host.id, visibility=cols.pop("visibility", "organization"),
            tags=cols.pop("tags", []), **cols,
        )
        self.db.add(r)
        self.db.flush()
        return r

    def folder(self, name=None, parent_id=None):
        f = MediaFolder(org_id=self.org.id, name=name or f"Folder {uuid.uuid4().hex[:5]}",
                        parent_id=parent_id, created_by=self.host.id)
        self.db.add(f)
        self.db.flush()
        return f

    def sample(self, event=None, *, offset_s=0, viewers=10, **cols):
        s = AnalyticsSnapshot(event_id=(event or self.event).id, org_id=self.org.id,
                              viewers=viewers, **cols)
        self.db.add(s)
        self.db.flush()
        # created_at has a server default; overridden so the timeline is deterministic.
        s.created_at = self.now - timedelta(hours=2) + timedelta(seconds=offset_s)
        self.db.flush()
        return s

    def audits(self, action):
        return (self.db.query(AuditLog)
                .filter(AuditLog.action == action, AuditLog.org_id == self.org.id)
                .order_by(AuditLog.created_at).all())

    def close(self):
        for module, original in getattr(self, "_patched", []):
            module.SessionLocal = original
        m.app.dependency_overrides.clear()
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001
            pass
        self.db.rollback()
        self.db.close()


def with_storage(fn):
    """Run `fn` with object storage configured, so signed-URL paths are exercised."""
    saved = (settings.S3_BUCKET, settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY, settings.S3_REGION)
    settings.S3_BUCKET, settings.S3_ACCESS_KEY = "zoiko-test", "AKIATEST"
    settings.S3_SECRET_KEY, settings.S3_REGION = "secrettest", "eu-west-1"
    try:
        return fn()
    finally:
        (settings.S3_BUCKET, settings.S3_ACCESS_KEY,
         settings.S3_SECRET_KEY, settings.S3_REGION) = saved


# ── the library ───────────────────────────────────────────────────────────────

def test_library_lists_only_this_organizations_recordings():
    f = Fixture()
    try:
        mine = f.recording()
        theirs = LiveRecording(event_id=f.event.id, org_id=f.other_org.id, status="stopped",
                               size_bytes=1, storage_key="other/key.mp4")
        f.db.add(theirs)
        f.db.flush()

        body = f.client.get("/media/library", headers=f.auth(f.host)).json()
        ids = {i["id"] for i in body["items"]}
        assert str(mine.id) in ids
        assert str(theirs.id) not in ids, "a sibling org's recording reached this library"
        assert body["total"] == 1
    finally:
        f.close()


def test_default_shelf_hides_archived_failed_and_deleted():
    f = Fixture()
    try:
        live = f.recording()
        f.recording(archived_at=f.now)
        f.recording(status="failed", size=None, key=None)
        f.recording(deleted_at=f.now)

        shown = f.client.get("/media/library", headers=f.auth(f.host)).json()
        assert [i["id"] for i in shown["items"]] == [str(live.id)]

        for scope, expected in (("archived", 1), ("failed", 1), ("deleted", 1), ("all", 1)):
            body = f.client.get(f"/media/library?scope={scope}", headers=f.auth(f.host)).json()
            assert body["total"] == expected, f"{scope} shelf held {body['total']}"
    finally:
        f.close()


def test_private_recording_is_hidden_from_colleagues_in_sql_not_after_the_fact():
    """A private recording must not be counted in `total` either — a page of 60 that returns 12
    cards is how a filter bug looks like data loss."""
    f = Fixture()
    try:
        f.recording(visibility="private", created_by=f.host.id)
        f.recording(visibility="organization")

        # The speaker did not create it and is not an admin.
        body = f.client.get("/media/library", headers=f.auth(f.speaker)).json()
        assert body["total"] == 1 and len(body["items"]) == 1

        # Its creator sees it, and so does an org admin.
        assert f.client.get("/media/library", headers=f.auth(f.host)).json()["total"] == 2
        assert f.client.get("/media/library", headers=f.auth(f.admin)).json()["total"] == 2
    finally:
        f.close()


def test_search_and_tag_filters_compose():
    f = Fixture()
    try:
        f.recording(title="Pricing deep dive", tags=["sales", "emea"])
        f.recording(title="Pricing retro", tags=["sales"])
        f.recording(title="Engineering all hands", tags=["eng"])

        get = lambda q: f.client.get(f"/media/library?{q}", headers=f.auth(f.host)).json()  # noqa: E731
        assert get("q=pricing")["total"] == 2
        assert get("tag=sales")["total"] == 2
        # AND, not OR: picking two tags narrows.
        assert get("tag=sales&tag=emea")["total"] == 1
        assert get("q=pricing&tag=emea")["total"] == 1
        assert get("q=nothing-matches")["total"] == 0
    finally:
        f.close()


def test_title_falls_back_to_the_event_and_is_not_frozen_at_creation():
    """Storing a copy of the event title at capture time would freeze every past recording's name
    the first time somebody renamed the event."""
    f = Fixture()
    try:
        r = f.recording()
        body = f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.host)).json()
        assert body["recording"]["title"] == f.event.title
        assert body["recording"]["custom_title"] is None

        f.event.title = "Renamed Event"
        f.db.flush()
        again = f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.host)).json()
        assert again["recording"]["title"] == "Renamed Event"
    finally:
        f.close()


def test_sort_whitelist_refuses_an_unknown_key():
    f = Fixture()
    try:
        assert f.client.get("/media/library?sort=drop%20table", headers=f.auth(f.host)).status_code == 400
        assert f.client.get("/media/library?scope=nonsense", headers=f.auth(f.host)).status_code == 400
    finally:
        f.close()


# ── isolation ─────────────────────────────────────────────────────────────────

def test_foreign_recording_is_404_not_403():
    """403 would confirm the recording exists in another tenant."""
    f = Fixture()
    try:
        theirs = LiveRecording(event_id=f.event.id, org_id=f.other_org.id, status="stopped",
                               size_bytes=1, storage_key="other/key.mp4")
        f.db.add(theirs)
        f.db.flush()
        for method, path in (
            ("get", f"/media/recordings/{theirs.id}"),
            ("patch", f"/media/recordings/{theirs.id}"),
            ("post", f"/media/recordings/{theirs.id}/archive"),
            ("delete", f"/media/recordings/{theirs.id}"),
            ("post", f"/media/recordings/{theirs.id}/download"),
            ("get", f"/media/recordings/{theirs.id}/transcript"),
            ("get", f"/media/recordings/{theirs.id}/insights"),
        ):
            call = getattr(f.client, method)
            res = call(path, headers=f.auth(f.admin), **({"json": {}} if method in ("patch", "post") else {}))
            assert res.status_code == 404, f"{method} {path} -> {res.status_code}"
    finally:
        f.close()


def test_recording_cannot_be_filed_into_another_tenants_folder():
    """Both rows stay put, so this is easy to miss — but it is a cross-tenant write."""
    f = Fixture()
    try:
        r = f.recording()
        theirs = MediaFolder(org_id=f.other_org.id, name="Their shelf")
        f.db.add(theirs)
        f.db.flush()

        res = f.client.patch(f"/media/recordings/{r.id}", headers=f.auth(f.host),
                             json={"folder_id": str(theirs.id)})
        assert res.status_code == 200
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).folder_id is None, "filed into a foreign folder"
    finally:
        f.close()


def test_moderator_may_watch_but_not_manage_the_library():
    """Moderation is authority over a live room, not over the organization's media assets."""
    f = Fixture()
    try:
        r = f.recording()
        assert f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.moderator)).status_code == 200
        assert f.client.patch(f"/media/recordings/{r.id}", headers=f.auth(f.moderator),
                              json={"title": "mine now"}).status_code == 403
        assert f.client.delete(f"/media/recordings/{r.id}",
                               headers=f.auth(f.moderator)).status_code == 403
        assert f.client.post("/media/folders", headers=f.auth(f.moderator),
                             json={"name": "x"}).status_code == 403
    finally:
        f.close()


def test_only_org_admin_may_purge_or_change_policy():
    f = Fixture()
    try:
        r = f.recording(deleted_at=f.now)
        assert f.client.delete(f"/media/recordings/{r.id}/purge",
                               headers=f.auth(f.host)).status_code == 403
        assert f.client.patch("/media/settings", headers=f.auth(f.host),
                              json={"downloads": "disabled"}).status_code == 403
        assert f.client.post("/media/retention/run", headers=f.auth(f.host),
                             json={"dry_run": True}).status_code == 403
    finally:
        f.close()


# ── downloads ─────────────────────────────────────────────────────────────────

def test_download_returns_a_signed_url_and_counts_it():
    f = Fixture()
    try:
        r = f.recording()

        def run():
            res = f.client.post(f"/media/recordings/{r.id}/download", headers=f.auth(f.host), json={})
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["url"].startswith("https://") and "X-Amz-Signature=" in body["url"]
            assert "X-Amz-Expires=" in body["url"] and body["expires_in"] > 0
            # The object key never leaves the server as a durable location.
            assert "response-content-disposition" in body["url"]
            return body

        with_storage(run)
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).download_count == 1
        assert len(f.audits("media.recording.download")) == 1
    finally:
        f.close()


def test_disabled_downloads_are_actually_refused():
    f = Fixture()
    try:
        r = f.recording()
        f.client.patch("/media/settings", headers=f.auth(f.admin), json={"downloads": "disabled"})

        def run():
            res = f.client.post(f"/media/recordings/{r.id}/download", headers=f.auth(f.host), json={})
            assert res.status_code == 403
            assert "turned off" in res.json()["detail"]["error"]
            return res

        with_storage(run)
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).download_count == 0, "a refusal must not count"
        assert len(f.audits("media.recording.download_denied")) == 1
    finally:
        f.close()


def test_password_protected_download_needs_the_right_passphrase():
    f = Fixture()
    try:
        r = f.recording()
        res = f.client.patch(f"/media/recordings/{r.id}/download-policy", headers=f.auth(f.host),
                             json={"mode": "password", "password": "letmein"})
        assert res.status_code == 200 and res.json()["password_protected"] is True

        def run():
            no_pass = f.client.post(f"/media/recordings/{r.id}/download",
                                    headers=f.auth(f.host), json={})
            assert no_pass.status_code == 403 and no_pass.json()["detail"]["needs_password"] is True

            wrong = f.client.post(f"/media/recordings/{r.id}/download",
                                  headers=f.auth(f.host), json={"password": "guess"})
            assert wrong.status_code == 403 and wrong.json()["detail"]["needs_password"] is True

            right = f.client.post(f"/media/recordings/{r.id}/download",
                                  headers=f.auth(f.host), json={"password": "letmein"})
            assert right.status_code == 200 and right.json()["url"]

        with_storage(run)
        # The passphrase is hashed, never stored or echoed in clear.
        f.db.expire_all()
        stored = f.db.get(LiveRecording, r.id).download_password_hash
        assert stored and stored != "letmein" and stored.startswith("$2")
    finally:
        f.close()


def test_password_mode_without_a_passphrase_fails_closed():
    """A lock with no key must refuse, not wave everyone through."""
    f = Fixture()
    try:
        r = f.recording(download_policy="password")   # set directly, bypassing the route's guard
        rules = media_svc.download_rules(r, media_svc.policy_for(f.org))
        assert rules["mode"] == "disabled"
        # And the route itself refuses to leave a recording in that state.
        assert f.client.patch(f"/media/recordings/{r.id}/download-policy", headers=f.auth(f.host),
                              json={"mode": "password"}).status_code == 400
    finally:
        f.close()


def test_expired_download_window_stops_the_link():
    f = Fixture()
    try:
        r = f.recording()
        f.client.patch(f"/media/recordings/{r.id}/download-policy", headers=f.auth(f.host),
                       json={"expires_at": (f.now - timedelta(days=1)).isoformat()})

        def run():
            res = f.client.post(f"/media/recordings/{r.id}/download", headers=f.auth(f.host), json={})
            assert res.status_code == 403 and "window" in res.json()["detail"]["error"]

        with_storage(run)
    finally:
        f.close()


def test_role_below_the_bar_cannot_download_but_learns_nothing_it_should_not():
    f = Fixture()
    try:
        r = f.recording()

        def run():
            res = f.client.post(f"/media/recordings/{r.id}/download",
                                headers=f.auth(f.speaker), json={})
            assert res.status_code == 403 and "role" in res.json()["detail"]["error"]

            # A private recording refuses on VISIBILITY first, so the reply says nothing about
            # how downloads are configured.
            r.visibility = "private"
            f.db.flush()
            hidden = f.client.post(f"/media/recordings/{r.id}/download",
                                   headers=f.auth(f.speaker), json={})
            assert hidden.status_code == 403 and "role" not in hidden.json()["detail"]["error"]

        with_storage(run)
    finally:
        f.close()


def test_no_file_means_no_download_and_no_playback_url():
    """A `stopped` row with no bytes must never claim to be playable."""
    f = Fixture()
    try:
        r = f.recording(size=None, key=None)

        def run():
            body = f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.host)).json()
            assert body["recording"]["has_file"] is False
            assert body["playback_url"] is None
            res = f.client.post(f"/media/recordings/{r.id}/download", headers=f.auth(f.host), json={})
            assert res.status_code == 403 and "No file" in res.json()["detail"]["error"]

        with_storage(run)
    finally:
        f.close()


def test_unconfigured_storage_reports_itself_instead_of_inventing_a_url():
    f = Fixture()
    try:
        r = f.recording()
        # No with_storage(): the deployment has no bucket.
        body = f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.host)).json()
        assert body["storage_configured"] is False and body["playback_url"] is None
        res = f.client.post(f"/media/recordings/{r.id}/download", headers=f.auth(f.host), json={})
        assert res.status_code == 403 and "not configured" in res.json()["detail"]["error"]
    finally:
        f.close()


def test_audience_reaches_a_public_replay_only_when_replay_is_enabled():
    f = Fixture()
    try:
        r = f.recording(visibility="public")

        def run():
            body = f.client.get(f"/media/events/{f.event.id}/recordings",
                                headers=f.auth(f.viewer)).json()
            assert [x["id"] for x in body["recordings"]] == [str(r.id)]
            # The projection is narrower than the library card.
            assert "error" not in body["recordings"][0] and "storage_class" not in body["recordings"][0]

            # The organizer's replay switch wins over the recording's own label.
            f.event.replay_enabled = False
            f.db.flush()
            off = f.client.get(f"/media/events/{f.event.id}/recordings",
                               headers=f.auth(f.viewer)).json()
            assert off["recordings"] == []

        with_storage(run)
    finally:
        f.close()


def test_org_visibility_never_reaches_an_outsider():
    f = Fixture()
    try:
        f.recording(visibility="organization")
        body = f.client.get(f"/media/events/{f.event.id}/recordings",
                            headers=f.auth(f.viewer)).json()
        assert body["recordings"] == []
    finally:
        f.close()


# ── deletion ──────────────────────────────────────────────────────────────────

def test_soft_delete_is_reversible_and_keeps_the_object():
    f = Fixture()
    try:
        r = f.recording()
        key = r.storage_key
        res = f.client.delete(f"/media/recordings/{r.id}", headers=f.auth(f.host))
        assert res.status_code == 200 and res.json()["restorable_until"]

        f.db.expire_all()
        row = f.db.get(LiveRecording, r.id)
        assert row.deleted_at is not None and row.storage_key == key, "the object must survive"

        assert f.client.post(f"/media/recordings/{r.id}/restore",
                             headers=f.auth(f.host)).status_code == 200
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).deleted_at is None
    finally:
        f.close()


def test_purge_requires_a_prior_delete():
    f = Fixture()
    try:
        r = f.recording()
        res = f.client.delete(f"/media/recordings/{r.id}/purge", headers=f.auth(f.admin))
        assert res.status_code == 409 and "recycle bin" in res.json()["detail"]
    finally:
        f.close()


def test_purge_reports_what_happened_to_the_bytes():
    """An admin told 'purged' while the file remains has been misinformed about a deletion
    request. Storage is unconfigured here, so the honest answer is that it was not deleted."""
    f = Fixture()
    try:
        r = f.recording(deleted_at=f.now)
        res = f.client.delete(f"/media/recordings/{r.id}/purge", headers=f.auth(f.admin))
        assert res.status_code == 200
        body = res.json()
        assert body["purged"] is True
        assert body["object_deleted"] is False and body["object_error"]
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id) is None
        assert len(f.audits("media.recording.purge")) == 1
    finally:
        f.close()


def test_a_duplicate_sharing_the_object_blocks_the_delete():
    f = Fixture()
    try:
        original = f.recording()
        res = f.client.post(f"/media/recordings/{original.id}/duplicate", headers=f.auth(f.host))
        assert res.status_code == 201
        copy_id = res.json()["id"]

        f.db.expire_all()
        copy = f.db.get(LiveRecording, uuid.UUID(copy_id))
        assert copy.storage_key == original.storage_key, "a duplicate shares the object"
        # A copy starts private regardless of the original's audience.
        assert copy.visibility == "private"
        assert copy.view_count == 0 and copy.download_count == 0

        assert crud.purge_blockers(f.db, original) == 1
        f.client.delete(f"/media/recordings/{original.id}", headers=f.auth(f.host))
        purge = f.client.delete(f"/media/recordings/{original.id}/purge", headers=f.auth(f.admin))
        assert purge.status_code == 200
        assert purge.json()["object_deleted"] is False
        assert "other library item" in purge.json()["object_error"]
    finally:
        f.close()


def test_archive_is_not_delete():
    f = Fixture()
    try:
        r = f.recording()
        f.client.post(f"/media/recordings/{r.id}/archive", headers=f.auth(f.host),
                      json={"archived": True})
        f.db.expire_all()
        row = f.db.get(LiveRecording, r.id)
        assert row.archived_at is not None and row.deleted_at is None
        assert row.storage_key, "archiving keeps the file"
        # Still openable — archiving is a shelf, not a permission.
        assert f.client.get(f"/media/recordings/{r.id}", headers=f.auth(f.host)).status_code == 200
    finally:
        f.close()


# ── folders ───────────────────────────────────────────────────────────────────

def test_deleting_a_folder_never_deletes_recordings():
    f = Fixture()
    try:
        folder = f.folder("Q3")
        child = f.folder("Q3 drafts", parent_id=folder.id)
        r = f.recording(folder_id=folder.id)

        res = f.client.delete(f"/media/folders/{folder.id}", headers=f.auth(f.host))
        assert res.status_code == 200 and res.json()["recordings_moved_to_root"] == 1

        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id) is not None, "the recording was deleted with the shelf"
        assert f.db.get(LiveRecording, r.id).folder_id is None
        assert f.db.get(MediaFolder, child.id).parent_id is None, "the child should be promoted"
    finally:
        f.close()


def test_folder_names_are_unique_per_parent_and_nesting_is_capped():
    f = Fixture()
    try:
        first = f.client.post("/media/folders", headers=f.auth(f.host), json={"name": "Sales"})
        assert first.status_code == 201
        dupe = f.client.post("/media/folders", headers=f.auth(f.host), json={"name": "sales"})
        assert dupe.status_code == 409, "case-insensitive duplicate accepted"

        parent = uuid.UUID(first.json()["id"])
        child = f.client.post("/media/folders", headers=f.auth(f.host),
                              json={"name": "EMEA", "parent_id": str(parent)})
        assert child.status_code == 201
        grand = f.client.post("/media/folders", headers=f.auth(f.host),
                              json={"name": "France", "parent_id": child.json()["id"]})
        assert grand.status_code == 409 and "one level" in grand.json()["detail"]
    finally:
        f.close()


def test_empty_folders_still_appear_with_a_zero_count():
    """An inner join would hide a folder the moment its last recording moved out, which looks
    exactly like the folder having been deleted."""
    f = Fixture()
    try:
        f.folder("Empty shelf")
        folders = f.client.get("/media/folders", headers=f.auth(f.host)).json()
        assert [x["name"] for x in folders] == ["Empty shelf"]
        assert folders[0]["count"] == 0
    finally:
        f.close()


# ── bookmarks & notes ─────────────────────────────────────────────────────────

def test_bookmarks_are_idempotent_per_position_but_notes_are_not():
    f = Fixture()
    try:
        r = f.recording()
        add = lambda body: f.client.post(f"/media/recordings/{r.id}/marks",  # noqa: E731
                                         headers=f.auth(f.host), json=body)
        first = add({"at_ms": 12_000})
        second = add({"at_ms": 12_000})
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"], "a double-click made two bookmarks"

        # Several notes at one timestamp is legitimate.
        n1 = add({"at_ms": 12_000, "note": "check this figure"})
        n2 = add({"at_ms": 12_000, "note": "and this one"})
        assert n1.json()["id"] != n2.json()["id"]

        marks = f.client.get(f"/media/recordings/{r.id}/marks", headers=f.auth(f.host)).json()
        assert len(marks) == 3
        assert {m["kind"] for m in marks} == {"bookmark", "note"}
    finally:
        f.close()


def test_a_mark_is_clamped_to_the_recording_and_private_by_default():
    f = Fixture()
    try:
        r = f.recording()          # duration_ms = 3_600_000
        res = f.client.post(f"/media/recordings/{r.id}/marks", headers=f.auth(f.host),
                            json={"at_ms": 99_999_999})
        assert res.json()["at_ms"] == 3_600_000, "a mark past the end is a dead seek target"
        assert res.json()["shared"] is False

        # Somebody else's private mark is invisible; a shared one is not.
        others = f.client.get(f"/media/recordings/{r.id}/marks", headers=f.auth(f.admin)).json()
        assert others == []
        f.client.post(f"/media/recordings/{r.id}/marks", headers=f.auth(f.host),
                      json={"at_ms": 5000, "note": "read this out", "shared": True})
        seen = f.client.get(f"/media/recordings/{r.id}/marks", headers=f.auth(f.admin)).json()
        assert len(seen) == 1 and seen[0]["shared"] is True and seen[0]["author"]
        assert seen[0]["mine"] is False
    finally:
        f.close()


def test_only_the_author_can_delete_their_mark():
    f = Fixture()
    try:
        r = f.recording()
        created = f.client.post(f"/media/recordings/{r.id}/marks", headers=f.auth(f.host),
                                json={"at_ms": 1000, "note": "mine", "shared": True}).json()
        assert f.client.delete(f"/media/recordings/{r.id}/marks/{created['id']}",
                               headers=f.auth(f.admin)).status_code == 404
        assert f.client.delete(f"/media/recordings/{r.id}/marks/{created['id']}",
                               headers=f.auth(f.host)).status_code == 204
    finally:
        f.close()


def test_anyone_who_can_watch_can_mark():
    """A bookmark is the viewer's own record, not a change to the recording."""
    f = Fixture()
    try:
        r = f.recording()
        assert f.client.post(f"/media/recordings/{r.id}/marks", headers=f.auth(f.moderator),
                             json={"at_ms": 2000}).status_code == 201
    finally:
        f.close()


# ── transcripts ───────────────────────────────────────────────────────────────

def test_missing_transcript_says_why_and_offers_the_way_out():
    f = Fixture()
    try:
        r = f.recording()
        body = f.client.get(f"/media/recordings/{r.id}/transcript", headers=f.auth(f.host)).json()
        assert body["available"] is False
        assert "not configured" in body["reason"] and "WebVTT" in body["reason"]
        assert body["asr_configured"] is False and body["can_upload"] is True
        # No captions track is served for a recording that has none.
        assert f.client.get(f"/media/recordings/{r.id}/transcript.vtt",
                            headers=f.auth(f.host)).status_code == 404
    finally:
        f.close()


def test_uploaded_vtt_powers_search_captions_and_download():
    f = Fixture()
    try:
        r = f.recording()
        res = f.client.post(f"/media/recordings/{r.id}/transcript", headers=f.auth(f.host),
                            files={"file": ("review.vtt", VTT, "text/vtt")})
        assert res.status_code == 201, res.text
        assert res.json()["segments"] == 2
        assert res.json()["speakers"] == ["Ada Lovelace", "Grace Hopper"]
        assert res.json()["speaker_separation"] is True

        got = f.client.get(f"/media/recordings/{r.id}/transcript", headers=f.auth(f.host)).json()
        assert got["available"] is True and got["speaker_separation_note"] is None

        hits = f.client.get(f"/media/recordings/{r.id}/transcript?q=revenue",
                            headers=f.auth(f.host)).json()
        assert len(hits["hits"]) == 1 and hits["hits"][0]["start_ms"] == 5500

        vtt = f.client.get(f"/media/recordings/{r.id}/transcript.vtt", headers=f.auth(f.host))
        assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT")
        assert vtt.headers["x-content-type-options"] == "nosniff"

        txt = f.client.get(f"/media/recordings/{r.id}/transcript.txt", headers=f.auth(f.host))
        assert txt.status_code == 200 and "Ada Lovelace:" in txt.text
        assert "attachment" in txt.headers["content-disposition"]
        assert len(f.audits("media.transcript.download")) == 1
    finally:
        f.close()


def test_a_file_with_no_cues_is_refused_rather_than_stored_empty():
    """Storing it would make the UI claim a transcript exists."""
    f = Fixture()
    try:
        r = f.recording()
        res = f.client.post(f"/media/recordings/{r.id}/transcript", headers=f.auth(f.host),
                            files={"file": ("notes.txt", "just some prose", "text/plain")})
        assert res.status_code == 400
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).transcript is None
    finally:
        f.close()


def test_transcript_upload_needs_manage_rights():
    f = Fixture()
    try:
        r = f.recording()
        res = f.client.post(f"/media/recordings/{r.id}/transcript", headers=f.auth(f.moderator),
                            files={"file": ("review.vtt", VTT, "text/vtt")})
        assert res.status_code == 403
    finally:
        f.close()


# ── insights ──────────────────────────────────────────────────────────────────

def test_insights_derive_real_chapters_and_report_what_needs_a_model():
    f = Fixture()
    try:
        r = f.recording()
        start = r.started_at
        poll = LivePoll(event_id=f.event.id, org_id=f.org.id, question="Ship on Friday?",
                        options=[{"label": "Yes", "votes": 7}, {"label": "No", "votes": 3}],
                        status="closed", launched_at=start + timedelta(minutes=2),
                        closed_at=start + timedelta(minutes=3))
        ann = LiveAnnouncement(event_id=f.event.id, org_id=f.org.id, text="Break in five",
                               sent_at=start + timedelta(minutes=5))
        f.db.add_all([poll, ann])
        for i in range(12):
            f.db.add(LiveMessage(event_id=f.event.id, org_id=f.org.id, user_id=f.viewer.id,
                                 author_name="V", text="roadmap latency roadmap", flags=[]))
        f.db.flush()

        body = f.client.get(f"/media/recordings/{r.id}/insights", headers=f.auth(f.host)).json()

        titles = [c["title"] for c in body["chapters"]]
        assert any("Ship on Friday" in t for t in titles)
        assert any("Break in five" in t for t in titles)
        assert body["chapters"] == sorted(body["chapters"], key=lambda c: c["at_ms"])

        decisions = body["decisions"]["items"]
        assert len(decisions) == 1 and decisions[0]["outcome"] == "Yes"
        assert decisions[0]["share"] == 70.0

        assert "roadmap" in [k["term"] for k in body["keywords"]]
        assert body["engagement"]["messages"] == 12

        # The three model-dependent fields must say so, never guess.
        for field in ("summary", "action_items", "sentiment"):
            assert body[field]["available"] is False, field
            assert body[field]["reason"], field
        assert body["provider"] == "derived"
    finally:
        f.close()


def test_a_pre_recording_moment_is_not_a_chapter():
    """A poll launched before Record was pressed is not in the file, and pinning it to 00:00 would
    put a marker on footage that does not contain it."""
    f = Fixture()
    try:
        r = f.recording()
        f.db.add(LivePoll(event_id=f.event.id, org_id=f.org.id, question="Earlier poll",
                          options=[], status="live",
                          launched_at=r.started_at - timedelta(minutes=10)))
        f.db.flush()
        body = f.client.get(f"/media/recordings/{r.id}/insights", headers=f.auth(f.host)).json()
        assert not any("Earlier poll" in c["title"] for c in body["chapters"])
        assert all(c["at_ms"] >= 0 for c in body["chapters"])
    finally:
        f.close()


def test_a_viewers_insight_read_does_not_write_the_cache():
    f = Fixture()
    try:
        r = f.recording()
        body = f.client.get(f"/media/recordings/{r.id}/insights", headers=f.auth(f.moderator)).json()
        assert body["cached"] is False
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).insights is None, "a viewer wrote to the row"

        # A manager's read persists it, and the next read is served from the row.
        f.client.get(f"/media/recordings/{r.id}/insights", headers=f.auth(f.host))
        f.db.expire_all()
        assert f.db.get(LiveRecording, r.id).insights is not None
        again = f.client.get(f"/media/recordings/{r.id}/insights", headers=f.auth(f.host)).json()
        assert again["cached"] is True
    finally:
        f.close()


# ── policy & retention ────────────────────────────────────────────────────────

def test_unknown_policy_keys_are_named_not_silently_dropped():
    f = Fixture()
    try:
        res = f.client.patch("/media/settings", headers=f.auth(f.admin),
                             json={"downloads": "allowed", "downlods": "typo"})
        assert res.status_code == 400 and "downlods" in res.json()["detail"]
        f.db.expire_all()
        assert "downlods" not in (f.db.get(Organization, f.org.id).media or {})
    finally:
        f.close()


def test_policy_values_are_clamped():
    f = Fixture()
    try:
        res = f.client.patch("/media/settings", headers=f.auth(f.admin),
                             json={"link_ttl_minutes": 999999, "recycle_bin_days": 0})
        assert res.status_code == 200
        policy = res.json()["policy"]
        assert policy["link_ttl_minutes"] == 1440
        assert policy["recycle_bin_days"] == 1, "a 0-day recycle bin is not a recycle bin"
    finally:
        f.close()


def test_settings_state_what_the_deployment_cannot_do():
    f = Fixture()
    try:
        body = f.client.get("/media/settings", headers=f.auth(f.admin)).json()
        caps = body["capabilities"]
        assert caps["signed_urls"] is False           # no bucket in this test environment
        assert caps["asr"] is False and caps["ai_summaries"] is False
        assert caps["download_watermark"] is False and caps["cold_storage_transition"] is False
        for key in ("download_watermark", "cold_storage", "retention"):
            assert body["notes"][key], key
    finally:
        f.close()


def test_retention_dry_run_changes_nothing_and_is_the_default():
    f = Fixture()
    try:
        old = f.recording()
        old.stopped_at = f.now - timedelta(days=200)
        f.db.flush()
        f.client.patch("/media/settings", headers=f.auth(f.admin),
                       json={"auto_archive_days": 90, "auto_delete_days": 180})

        # dry_run defaults to TRUE, so the destructive form is always deliberate.
        preview = f.client.post("/media/retention/run", headers=f.auth(f.admin), json={}).json()
        assert preview["dry_run"] is True
        assert preview["plan"] == [["archive", 90], ["delete", 180]], preview["plan"]
        assert preview["counts"]["archive"] == 1 and preview["counts"]["delete"] == 1
        f.db.expire_all()
        assert f.db.get(LiveRecording, old.id).archived_at is None, "a dry run mutated a row"

        applied = f.client.post("/media/retention/run", headers=f.auth(f.admin),
                                json={"dry_run": False}).json()
        assert applied["dry_run"] is False
        f.db.expire_all()
        row = f.db.get(LiveRecording, old.id)
        # Archive is applied BEFORE delete, so nothing skips a stage.
        assert row.archived_at is not None and row.deleted_at is not None
        assert len(f.audits("media.retention.run")) == 1
    finally:
        f.close()


def test_storage_stats_count_the_recycle_bin_towards_the_quota():
    """A bin that does not count towards the quota is a way to be over quota without being told."""
    f = Fixture()
    try:
        f.recording(size=2 * 1024 ** 3)
        f.recording(size=1024 ** 3, deleted_at=f.now)
        f.recording(size=1024 ** 3, archived_at=f.now)

        body = f.client.get("/media/stats", headers=f.auth(f.host)).json()
        s = body["storage"]
        assert s["active_bytes"] == 2 * 1024 ** 3
        assert s["recycle_bin_bytes"] == 1024 ** 3 and s["archived_bytes"] == 1024 ** 3
        assert s["used_bytes"] == 4 * 1024 ** 3
        assert s["quota_bytes"] == 10 * 1024 ** 3
        assert s["percent_used"] == 40.0
        assert body["storage_alert"] is False

        # Cross the threshold and the alert flips — a real comparison, not a decoration.
        f.recording(size=6 * 1024 ** 3)
        hot = f.client.get("/media/stats", headers=f.auth(f.host)).json()
        assert hot["storage_alert"] is True
    finally:
        f.close()


def test_library_totals_report_no_average_rather_than_zero():
    f = Fixture()
    try:
        body = f.client.get("/media/stats", headers=f.auth(f.host)).json()
        assert body["recordings"] == 0
        assert body["avg_duration_ms"] is None, "an average over zero recordings is not 0"
    finally:
        f.close()


# ── recording lifecycle ───────────────────────────────────────────────────────

def test_a_failed_capture_is_retryable_and_a_retry_is_a_new_row():
    f = Fixture()
    try:
        failed = f.recording(status="failed", size=None, key=None,
                             error="No recording storage configured")
        f.db.flush()

        body = f.client.get("/media/library?scope=failed", headers=f.auth(f.host)).json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["retryable"] is True and item["has_file"] is False
        assert item["error"] and item["retry_count"] == 0

        # A row that has burned through its retries is no longer offered.
        failed.retry_count = 3
        f.db.flush()
        again = f.client.get("/media/library?scope=failed", headers=f.auth(f.host)).json()
        assert again["items"][0]["retryable"] is False
    finally:
        f.close()


def test_retry_is_refused_off_air_and_across_events():
    f = Fixture()
    try:
        failed = f.recording(status="failed", size=None, key=None)
        ctx = mod.resolve_ctx(f.event.id, f.host)
        asyncio.run(bus.state_clear(f.event.id))

        out = asyncio.run(broadcast._recording_retry(ctx, {"recording_id": str(failed.id)}))
        assert isinstance(out, str) and "live" in out, out

        # A recording from a SIBLING event in the same org is not retryable through this socket.
        other = Event(org_id=f.org.id, created_by=f.admin.id, title="Sibling",
                      slug=f"sib-{f.uniq}", status="ended")
        f.db.add(other)
        f.db.flush()
        elsewhere = f.recording(status="failed", size=None, key=None, event=other)
        out2 = asyncio.run(broadcast._recording_retry(ctx, {"recording_id": str(elsewhere.id)}))
        assert out2 == "Unknown recording"

        # And a stopped recording is not a failure to retry.
        good = f.recording()
        out3 = asyncio.run(broadcast._recording_retry(ctx, {"recording_id": str(good.id)}))
        assert "failed" in out3
    finally:
        asyncio.run(bus.state_clear(f.event.id))
        f.close()


def test_recording_retry_is_host_only():
    f = Fixture()
    try:
        assert "recording.retry" in broadcast.ACTIONS
        assert "recording.retry" in mod.HOST_ONLY, "a moderator could restart a capture"
    finally:
        f.close()


def test_egress_result_writes_real_duration_and_marks_a_failure_failed():
    """FileInfo.duration is the file's real playable length; stopped_at - started_at includes the
    paused stretches the file does not contain."""
    f = Fixture()
    try:
        r = f.recording(status="recording", size=None, key="set")
        r.egress_id = f"EG_{f.uniq}"
        f.db.flush()

        class Info:
            egress_id = r.egress_id
            file_results = [type("F", (), {"size": 42_000_000, "location": "s3://b/k.mp4",
                                           "duration": 1_800_000_000_000})()]
            error = None

        out = broadcast.record_egress_result(Info)
        assert out and out["duration_ms"] == 1_800_000, out
        f.db.expire_all()
        row = f.db.get(LiveRecording, r.id)
        assert row.status == "stopped" and row.enforced is True and row.size_bytes == 42_000_000

        # An egress that reported an error becomes `failed`, so the library never offers a download.
        bad = f.recording(status="recording", size=None, key="set")
        bad.egress_id = f"EGX_{f.uniq}"
        f.db.flush()

        class Broken:
            egress_id = bad.egress_id
            file_results = []
            error = "upload failed: access denied"

        broadcast.record_egress_result(Broken)
        f.db.expire_all()
        broken = f.db.get(LiveRecording, bad.id)
        assert broken.status == "failed" and broken.enforced is False and broken.error
    finally:
        f.close()


def test_speaking_time_is_flushed_from_presence_onto_the_assignment():
    """Presence is ephemeral, so ending the broadcast is the ONLY moment this figure can be saved —
    without it, speaker analytics has no source."""
    f = Fixture()
    try:
        ctx = mod.resolve_ctx(f.event.id, f.host)
        totals = {
            str(f.speaker.id): 90_000,
            # The publisher identity carries a #host suffix; both spellings are one person.
            broadcast.publisher_identity(str(f.host.id)): 30_000,
            str(uuid.uuid4()): 12_000,        # somebody with no assignment
        }
        written = broadcast._persist_speaking_time(f.db, ctx, totals)
        f.db.flush()
        assert written == 2

        rows = {a.role: a.speaking_ms for a in f.db.query(EventAssignment)
                .filter(EventAssignment.event_id == f.event.id).all()}
        assert rows["speaker"] == 90_000 and rows["host"] == 30_000

        # Accumulates rather than overwrites: a second go-live in one event must not erase the first.
        broadcast._persist_speaking_time(f.db, ctx, {str(f.speaker.id): 10_000})
        f.db.flush()
        f.db.expire_all()
        again = f.db.query(EventAssignment).filter(
            EventAssignment.event_id == f.event.id, EventAssignment.role == "speaker").one()
        assert again.speaking_ms == 100_000
    finally:
        f.close()


def test_a_recording_start_that_cannot_capture_is_failed_immediately():
    """Otherwise the row sits at `recording` forever waiting for a file that will never arrive."""
    f = Fixture()
    try:
        ctx = mod.resolve_ctx(f.event.id, f.host)
        asyncio.run(bus.state_set(f.event.id, {"settings": broadcast.DEFAULT_SETTINGS}))
        frames = asyncio.run(broadcast._recording_start(ctx, {}))
        assert isinstance(frames, list)
        payload = frames[0][2]
        assert payload["status"] == "failed" and payload["enforced"] is False
        assert payload["has_file"] is False and payload["retryable"] is True

        f.db.expire_all()
        row = f.db.query(LiveRecording).filter(LiveRecording.event_id == f.event.id).one()
        assert row.storage_key is None, "no key may be claimed for a capture that never started"
        assert row.error
    finally:
        asyncio.run(bus.state_clear(f.event.id))
        f.close()


# ── audit ─────────────────────────────────────────────────────────────────────

def test_every_state_change_is_audited():
    f = Fixture()
    try:
        r = f.recording()
        f.client.patch(f"/media/recordings/{r.id}", headers=f.auth(f.host), json={"title": "Renamed"})
        f.client.post(f"/media/recordings/{r.id}/view", headers=f.auth(f.host))
        f.client.post(f"/media/recordings/{r.id}/archive", headers=f.auth(f.host), json={"archived": True})
        f.client.delete(f"/media/recordings/{r.id}", headers=f.auth(f.host))
        f.client.post(f"/media/recordings/{r.id}/restore", headers=f.auth(f.host))

        for action in ("media.recording.update", "media.recording.view", "media.recording.archive",
                       "media.recording.delete", "media.recording.restore"):
            assert len(f.audits(action)) == 1, action
        # The update audit records what it replaced, so a rename is reversible from the trail.
        assert f.audits("media.recording.update")[0].meta["before"]["title"] is None
    finally:
        f.close()


def test_a_view_is_counted_and_audited_because_who_watched_is_a_real_question():
    f = Fixture()
    try:
        r = f.recording()
        f.client.post(f"/media/recordings/{r.id}/view", headers=f.auth(f.host))
        f.db.expire_all()
        row = f.db.get(LiveRecording, r.id)
        assert row.view_count == 1 and row.last_viewed_at is not None
        assert f.audits("media.recording.view")[0].actor_id == f.host.id
    finally:
        f.close()


# ── the XLSX writer, used by the analytics export ─────────────────────────────

def test_hand_rolled_xlsx_is_a_readable_workbook():
    """Written here rather than only in the service self-check because a corrupt export is
    indistinguishable from a working one until somebody opens it in Excel."""
    from app.services import analytics as asvc

    book = asvc.to_xlsx([("a", "Event"), ("b", "Attended")],
                        [{"a": 'He said "hi", loudly', "b": 42}, {"a": "Café", "b": None}])
    with zipfile.ZipFile(io.BytesIO(book)) as zf:
        assert zf.testzip() is None, "the archive is corrupt"
        assert zf.namelist()[0] == "[Content_Types].xml"
        sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "&quot;hi&quot;" in sheet and "Café" in sheet
        assert '<c r="B2"><v>42</v></c>' in sheet
        assert 'r="B3"' not in sheet


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        try:
            fn()
            passed += 1
            print(f"ok    {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
