"""Attendee & Viewer experience — server-side checks.

Real database inside a transaction that is ALWAYS rolled back, same discipline as the host, event,
invitation, moderator and speaker suites. The request session is the non-closing proxy, so the REST
routes' commits become flushes and nothing survives the rollback (see the note in
test_speaker_module — this suite POSTs a lot).

The weight here is on the two things an attendee module can get catastrophically wrong:
  * ISOLATION — an attendee is legitimately outside the organizing org, so "scope by the caller's
    org" is not available as a shortcut and every rule has to be checked directly.
  * the LINE — what an attendee may do (chat, ask, vote, react, raise a hand) versus everything
    else, which is the entire rest of the platform.

Run: `python test_attendee_module.py` (or pytest).
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import attendee as crud
from app.db import SessionLocal, get_db
from app.models import (
    AuditLog, Event, EventAssignment, EventRegistration, LivePoll, LiveQuestion, Organization,
    SpeakerAsset, User,
)
from app.security import create_access_token, hash_password
from app.services import attendee as attendee_svc
from app.services import broadcast, bus, moderation as mod, speaker, viewer as viewer_svc

settings.RESEND_API_KEY = ""

PDF = (b"%PDF-1.4\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


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
        now = datetime.now(timezone.utc)

        self.org = Organization(name=f"Att Org {uniq}", status="active")
        self.other_org = Organization(name=f"Att Other {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Org Admin", f"aadm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "The Host", f"ahost{uniq}", "host")
        self.moderator = self._user(self.org.id, "The Moderator", f"amod{uniq}", "moderator")
        self.speaker = self._user(self.org.id, "The Speaker", f"aspk{uniq}", "speaker")
        # The attendee lives in ANOTHER org — which is the normal case for a public event, and the
        # reason none of these queries may scope by the caller's org.
        self.viewer = self._user(self.other_org.id, "A Viewer", f"avw{uniq}", "viewer")
        self.viewer2 = self._user(self.other_org.id, "Another Viewer", f"avw2{uniq}", "viewer")
        self.db.flush()

        # Public + live: the default visibility, and what makes an outside attendee legitimate.
        self.event = Event(org_id=self.org.id, created_by=self.admin.id, title=f"Att Event {uniq}",
                           slug=f"att-event-{uniq}", status="live", visibility="public",
                           start_time=now + timedelta(hours=2), timezone="Europe/London")
        # Registration-gated, so enforcement is testable.
        self.gated = Event(org_id=self.org.id, created_by=self.admin.id, title="Gated",
                           slug=f"att-gated-{uniq}", status="live", visibility="public",
                           registration_required=True, start_time=now + timedelta(days=1))
        # Private to the organizing org: an outside attendee must not see it at all.
        self.private = Event(org_id=self.org.id, created_by=self.admin.id, title="Private",
                             slug=f"att-priv-{uniq}", status="live", visibility="private")
        self.db.add_all([self.event, self.gated, self.private])
        self.db.flush()

        self.db.add_all([
            EventAssignment(event_id=self.event.id, user_id=self.host.id, role="host"),
            EventAssignment(event_id=self.event.id, user_id=self.moderator.id, role="moderator"),
            EventAssignment(event_id=self.event.id, user_id=self.speaker.id, role="speaker"),
        ])
        self.db.flush()

        proxy = _NonClosing(self.db)
        m.app.dependency_overrides[get_db] = lambda: proxy
        self.client = TestClient(m.app)

        self._patched = []
        for module in (mod, broadcast, speaker, attendee_svc):
            if hasattr(module, "SessionLocal"):
                self._patched.append((module, module.SessionLocal))
                module.SessionLocal = lambda _p=proxy: _p

        asyncio.run(bus.presence_clear(self.event.id))
        attendee_svc._last_reaction.clear()

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def ctx(self, user, event=None):
        return mod.resolve_ctx((event or self.event).id, user)

    def asset(self, *, status="approved", shared=False, filename="deck.pdf"):
        a = SpeakerAsset(event_id=self.event.id, org_id=self.org.id, uploaded_by=self.speaker.id,
                         uploader_name=self.speaker.full_name, filename=filename,
                         content_type="application/pdf", kind="slides", size_bytes=len(PDF),
                         pages=1, status=status, shared=shared, data=PDF)
        self.db.add(a)
        self.db.flush()
        return a

    def question(self, author, text="why?", **cols):
        q = LiveQuestion(event_id=self.event.id, org_id=self.org.id, user_id=author.id,
                         author_name=author.full_name, text=text, flags=[], **cols)
        self.db.add(q)
        self.db.flush()
        return q

    def audits(self, action):
        return (self.db.query(AuditLog)
                .filter(AuditLog.action == action, AuditLog.org_id == self.org.id)
                .order_by(AuditLog.created_at).all())

    def reg(self, event=None, user=None):
        self.db.expire_all()
        return crud.get_registration(self.db, (event or self.event).id, (user or self.viewer).id)

    def close(self):
        for module, original in getattr(self, "_patched", []):
            module.SessionLocal = original
        m.app.dependency_overrides.clear()
        try:
            asyncio.run(bus.presence_clear(self.event.id))
        except Exception:  # noqa: BLE001
            pass
        try:
            self.tx.rollback()
        except Exception:  # noqa: BLE001
            pass
        self.db.rollback()
        self.db.close()


def run(coro):
    return asyncio.run(coro)


# ── RBAC: what an attendee is, and is not ─────────────────────────────────────

def test_an_attendee_has_no_console_authority(f):
    ctx = f.ctx(f.viewer)
    assert ctx is not None, "a public event is viewable — that is what public means"
    assert ctx.can_moderate is False and ctx.can_host is False and ctx.can_speak is False


def test_every_privileged_action_is_refused(f):
    """The brief's "cannot" list, checked against the dispatcher rather than the interface."""
    ctx = f.ctx(f.viewer)
    payload = {"id": str(uuid.uuid4()), "identity": "x", "asset_id": str(uuid.uuid4())}
    for action in (
        "participant.mute", "participant.remove", "participant.ban", "participant.stage",
        "stage.admit", "stage.mute_all", "chat.delete", "chat.pin", "chat.mute",
        "qa.approve", "qa.dismiss", "qa.assign", "poll.create", "poll.launch", "poll.close",
        "announce.send", "presentation.start", "presentation.review", "presentation.share",
        "whiteboard.draw", "notes.save", "broadcast.end", "recording.start",
    ):
        err = run(mod.dispatch(ctx, action, payload))
        assert err, f"{action} must be refused for an attendee"


def test_the_things_an_attendee_may_do(f):
    """Chat, ask, vote, react and raise a hand — all already VIEWER_ACTIONS, which is why the
    audience side needed almost no new server code."""
    for action in ("chat.send", "chat.typing", "chat.react", "chat.report", "qa.ask", "qa.vote",
                   "poll.vote", "participant.hand", "participant.state", "reaction.send"):
        assert action in mod.VIEWER_ACTIONS, action
    # And the tiers stay disjoint — an action in two sets is judged by whichever branch is first.
    assert not (mod.VIEWER_ACTIONS & mod.HOST_ONLY)
    assert not (mod.VIEWER_ACTIONS & mod.SPEAKER_ACTIONS)


def test_a_private_event_is_invisible_to_an_outside_attendee(f):
    H = f.auth(f.viewer)
    # 404, never 403: a status code that distinguishes "exists but forbidden" is an existence
    # oracle for private events.
    assert f.client.get(f"/events/{f.private.id}/viewer", headers=H).status_code == 404
    assert f.client.post(f"/attendee/events/{f.private.id}/register", headers=H).status_code == 404
    assert f.client.post(f"/attendee/events/{f.private.id}/bookmark", headers=H,
                         json={"on": True}).status_code == 404
    assert f.client.get(f"/attendee/events/{f.private.id}/resources", headers=H).status_code == 404
    # The socket refuses it too.
    assert f.ctx(f.viewer, f.private) is None


def test_the_dashboard_never_lists_an_event_whose_page_would_404(f):
    """my_events expresses access_for as a WHERE clause. If the two drift, the dashboard shows a
    row that 404s when tapped."""
    rows = crud.my_events(f.db, f.viewer)
    ids = {r["id"] for r in rows}
    assert str(f.event.id) in ids, "a public event is discoverable"
    assert str(f.private.id) not in ids, "a private event of another org is not"

    # Cross-check every listed row against the real access rule.
    for r in rows:
        ev = f.db.get(Event, uuid.UUID(r["id"]))
        allowed, _, _ = viewer_svc.access_for(ev, f.viewer)
        assert allowed, f"{ev.title} was listed but access_for refuses it"


def test_an_org_member_still_sees_their_own_private_events(f):
    rows = crud.my_events(f.db, f.admin)
    assert str(f.private.id) in {r["id"] for r in rows}


# ── Registration ──────────────────────────────────────────────────────────────

def test_registration_gates_the_media_but_not_the_page(f):
    """The page must render for somebody who hasn't signed up — that is where the Register button
    is. Collapsing this into access_for would 404 them and leave them nowhere to register."""
    H = f.auth(f.viewer)
    landing = f.client.get(f"/events/{f.gated.id}/viewer", headers=H)
    assert landing.status_code == 200, "the page loads"
    body = landing.json()
    assert body["access"]["registration_required"] is True
    assert body["access"]["registration_enforced"] is True
    assert body["access"]["registered"] is False
    assert body["stream"]["playback_ready"] is False
    assert "egister" in body["stream"]["playback_blocked_reason"]

    # And the media itself is refused.
    assert f.client.get(f"/events/{f.gated.id}/playback", headers=H).status_code == 409


def test_registering_opens_the_media(f):
    H = f.auth(f.viewer)
    assert f.client.post(f"/attendee/events/{f.gated.id}/register", headers=H).status_code == 200
    body = f.client.get(f"/events/{f.gated.id}/viewer", headers=H).json()
    assert body["access"]["registered"] is True
    # LiveKit may be unconfigured in this environment, in which case the reason changes but must
    # no longer be about registration.
    assert "egister" not in (body["stream"]["playback_blocked_reason"] or "")
    assert f.audits("attendee.register")


def test_registering_twice_is_not_an_error(f):
    H = f.auth(f.viewer)
    assert f.client.post(f"/attendee/events/{f.gated.id}/register", headers=H).status_code == 200
    assert f.client.post(f"/attendee/events/{f.gated.id}/register", headers=H).status_code == 200
    rows = f.db.query(EventRegistration).filter(
        EventRegistration.event_id == f.gated.id, EventRegistration.user_id == f.viewer.id).all()
    assert len(rows) == 1, "one row per person per event"


def test_cancelling_keeps_the_history(f):
    H = f.auth(f.viewer)
    f.client.post(f"/attendee/events/{f.gated.id}/register", headers=H)
    f.client.post(f"/attendee/events/{f.gated.id}/bookmark", headers=H, json={"on": True})
    assert f.client.delete(f"/attendee/events/{f.gated.id}/register", headers=H).status_code == 200

    row = f.reg(f.gated)
    assert row is not None and row.status == "cancelled"
    assert row.bookmarked is True, "cancelling a registration must not discard the bookmark"
    assert row.registered_at is not None, "nor the fact that they once signed up"
    # And the gate closes again.
    assert f.client.get(f"/events/{f.gated.id}/playback", headers=H).status_code == 409


def test_a_full_event_refuses_a_new_registration(f):
    f.gated.registration_limit = 1
    f.db.flush()
    assert f.client.post(f"/attendee/events/{f.gated.id}/register",
                         headers=f.auth(f.viewer)).status_code == 200
    r = f.client.post(f"/attendee/events/{f.gated.id}/register", headers=f.auth(f.viewer2))
    assert r.status_code == 409 and "full" in r.json()["detail"].lower()


def test_bookmarking_does_not_smuggle_anybody_past_the_gate(f):
    """The row is created by ANY interaction, so its default status must not be `registered`."""
    H = f.auth(f.viewer)
    f.client.post(f"/attendee/events/{f.gated.id}/bookmark", headers=H, json={"on": True})
    row = f.reg(f.gated)
    assert row.bookmarked is True and row.status == "cancelled"
    assert crud.is_registered(f.db, f.gated.id, f.viewer.id) is False
    assert f.client.get(f"/events/{f.gated.id}/playback", headers=H).status_code == 409


def test_watching_does_not_promote_a_cancelled_registration(f):
    """record_join sets `attended` only from `registered`. Otherwise opening the media would
    manufacture the registration the gate is supposed to require."""
    crud.cancel(f.db, f.gated, f.viewer)
    f.db.flush()
    crud.record_join(f.db, f.gated, f.viewer)
    f.db.flush()
    row = f.reg(f.gated)
    assert row.status == "cancelled"
    assert row.first_joined_at is not None, "history is still recorded"


def test_an_organizer_never_faces_their_own_registration_gate(f):
    for staff in (f.admin, f.host, f.moderator):
        body = f.client.get(f"/events/{f.gated.id}/viewer", headers=f.auth(staff)).json()
        assert "egister" not in (body["stream"]["playback_blocked_reason"] or ""), staff.full_name


# ── Personalisation ───────────────────────────────────────────────────────────

def test_bookmark_reminder_and_question_saves_round_trip(f):
    H = f.auth(f.viewer)
    assert f.client.post(f"/attendee/events/{f.event.id}/bookmark", headers=H,
                         json={"on": True}).json()["bookmarked"] is True
    assert f.client.post(f"/attendee/events/{f.event.id}/bookmark", headers=H,
                         json={"on": False}).json()["bookmarked"] is False

    r = f.client.post(f"/attendee/events/{f.event.id}/reminder", headers=H,
                      json={"minutes_before": 30})
    assert r.status_code == 200 and r.json()["reminder_at"]
    # Absolute, not an offset: a rescheduled event must not move somebody's alarm.
    assert f.reg().reminder_at < f.event.start_time
    assert f.client.post(f"/attendee/events/{f.event.id}/reminder", headers=H,
                         json={"minutes_before": None}).json()["reminder_at"] is None

    q = f.question(f.viewer)
    first = f.client.post(f"/attendee/events/{f.event.id}/questions/{q.id}/bookmark", headers=H)
    assert first.json()["saved"] is True and str(q.id) in first.json()["question_bookmarks"]
    again = f.client.post(f"/attendee/events/{f.event.id}/questions/{q.id}/bookmark", headers=H)
    assert again.json()["saved"] is False, "it toggles"


def test_a_reminder_needs_a_start_time_and_cannot_be_ancient(f):
    no_start = Event(org_id=f.org.id, created_by=f.admin.id, title="Undated", status="published",
                     slug=f"att-undated-{f.uniq}", visibility="public")
    f.db.add(no_start)
    f.db.flush()
    assert f.client.post(f"/attendee/events/{no_start.id}/reminder", headers=f.auth(f.viewer),
                         json={"minutes_before": 15}).status_code == 409
    # A far-past event cannot take a reminder either.
    old = Event(org_id=f.org.id, created_by=f.admin.id, title="Old", status="ended",
                slug=f"att-old-{f.uniq}", visibility="public",
                start_time=datetime.now(timezone.utc) - timedelta(days=800))
    f.db.add(old)
    f.db.flush()
    assert f.client.post(f"/attendee/events/{old.id}/reminder", headers=f.auth(f.viewer),
                         json={"minutes_before": 0}).status_code == 422


def test_watch_history_is_accumulated_and_clamped(f):
    H = f.auth(f.viewer)
    crud.record_join(f.db, f.event, f.viewer)
    f.db.flush()
    for _ in range(3):
        assert f.client.post(f"/attendee/events/{f.event.id}/heartbeat", headers=H,
                             json={"seconds": 30}).status_code == 204
    assert f.reg().watch_seconds == 90

    # A tab suspended for an hour must not bank an hour.
    f.client.post(f"/attendee/events/{f.event.id}/heartbeat", headers=H, json={"seconds": 99999})
    assert f.reg().watch_seconds == 90 + crud.MAX_HEARTBEAT_SECONDS
    # Nonsense is worth nothing rather than an error.
    f.client.post(f"/attendee/events/{f.event.id}/heartbeat", headers=H, json={"seconds": -5})
    assert f.reg().watch_seconds == 90 + crud.MAX_HEARTBEAT_SECONDS


def test_saved_questions_are_capped(f):
    for i in range(crud.MAX_QUESTION_BOOKMARKS + 5):
        crud.toggle_question_bookmark(f.db, f.event, f.viewer, uuid.uuid4())
    f.db.flush()
    assert len(f.reg().question_bookmarks) == crud.MAX_QUESTION_BOOKMARKS


def test_one_attendee_cannot_read_or_write_anothers_state(f):
    """Every row is scoped by user_id. Two attendees on the same event share nothing."""
    f.client.post(f"/attendee/events/{f.event.id}/bookmark", headers=f.auth(f.viewer),
                  json={"on": True})
    assert f.reg(user=f.viewer).bookmarked is True
    assert f.reg(user=f.viewer2) is None, "the other attendee has no row at all"

    landing = f.client.get(f"/events/{f.event.id}/viewer", headers=f.auth(f.viewer2)).json()
    assert landing["me"]["bookmarked"] is False
    assert landing["me"]["question_bookmarks"] == []


# ── Preferences ───────────────────────────────────────────────────────────────

def test_preferences_are_whitelisted(f):
    H = f.auth(f.viewer)
    defaults = f.client.get("/attendee/preferences", headers=H).json()
    assert defaults["language"] == "en" and defaults["notify"]["event_starting"] is True

    r = f.client.patch("/attendee/preferences", headers=H, json={
        "language": "fr", "high_contrast": True, "text_size": "larger",
        # None of these may be stored: unknown key, invalid enum, and an attempt at another table.
        "is_admin": True, "role": "super_admin", "language_x": "zz",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "fr" and body["high_contrast"] is True
    assert "is_admin" not in body and "role" not in body

    f.db.expire_all()
    stored = f.db.get(User, f.viewer.id).preferences
    assert "is_admin" not in stored and "role" not in stored
    assert f.db.get(User, f.viewer.id).role == "viewer", "a preference patch cannot touch the role"


def test_an_invalid_preference_value_is_dropped(f):
    assert attendee_svc.clean_preferences({"language": "klingon"}) == {}
    assert attendee_svc.clean_preferences({"text_size": "enormous"}) == {}
    assert attendee_svc.clean_preferences({"reminder_offset_minutes": 7}) == {}
    # Favourites are bounded and de-duplicated, and a non-id is dropped.
    out = attendee_svc.clean_preferences({"favorite_speakers": ["../etc/passwd", str(uuid.uuid4())]})
    assert len(out["favorite_speakers"]) == 1
    ids = [str(uuid.uuid4()) for _ in range(attendee_svc.MAX_FAVORITES + 20)]
    assert len(attendee_svc.clean_preferences({"favorite_speakers": ids})["favorite_speakers"]) \
        == attendee_svc.MAX_FAVORITES
    # A patch of nothing recognised is a 422, not a silent success.
    assert f.client.patch("/attendee/preferences", headers=f.auth(f.viewer),
                          json={"nonsense": 1}).status_code == 422


def test_notify_switches_merge_rather_than_replace(f):
    H = f.auth(f.viewer)
    f.client.patch("/attendee/preferences", headers=H, json={"notify": {"poll_started": False}})
    body = f.client.patch("/attendee/preferences", headers=H,
                          json={"notify": {"announcement": False}}).json()
    assert body["notify"]["poll_started"] is False, "the earlier choice survives"
    assert body["notify"]["announcement"] is False
    assert body["notify"]["event_starting"] is True, "untouched keys keep their default"


# ── Reactions ─────────────────────────────────────────────────────────────────

def test_a_reaction_broadcasts_and_counts_without_being_persisted(f):
    ctx = f.ctx(f.viewer)
    frames = run(attendee_svc._reaction_send(ctx, {"emoji": "👏"}))
    channel, kind, data = frames[0]
    assert (channel, kind) == ("reaction", "reaction.new")
    assert data["emoji"] == "👏" and data["totals"]["👏"] == 1
    assert run(attendee_svc.reaction_totals(f.event.id))["👏"] == 1
    # Deliberately NOT audited: one row per clap at 10,000 attendees is a write storm for
    # information nobody reads back. The aggregate above is what survives.
    assert f.audits("attendee.reaction") == []


def test_only_allow_listed_emoji_are_accepted(f):
    ctx = f.ctx(f.viewer)
    assert run(attendee_svc._reaction_send(ctx, {"emoji": "<script>"})) == []
    assert run(attendee_svc._reaction_send(ctx, {"emoji": "🦄"})) == []
    assert run(attendee_svc._reaction_send(ctx, {"emoji": ""})) == []
    assert run(attendee_svc.reaction_totals(f.event.id)) == {}


def test_reactions_are_burst_limited(f):
    """The one action that can melt the bus by being used exactly as intended."""
    ctx = f.ctx(f.viewer)
    sent = sum(1 for _ in range(attendee_svc.REACTION_BURST + 6)
               if run(attendee_svc._reaction_send(ctx, {"emoji": "🔥"})))
    assert sent == attendee_svc.REACTION_BURST, sent
    # Throttling returns nothing rather than an error frame — a rate-limit toast on a tap the
    # attendee already saw animate would be noise.
    assert run(attendee_svc._reaction_send(ctx, {"emoji": "🔥"})) == []


def test_reactions_respect_the_hosts_switch(f):
    ctx = f.ctx(f.viewer)
    run(bus.state_set(f.event.id, {"reactions_enabled": False}))
    out = run(attendee_svc._reaction_send(ctx, {"emoji": "👍"}))
    assert isinstance(out, str) and "off" in out.lower()
    # Staff bypass their own audience controls, same rule as chat.
    assert not isinstance(run(attendee_svc._reaction_send(f.ctx(f.moderator), {"emoji": "👍"})), str)


# ── Shared resources ──────────────────────────────────────────────────────────

def test_a_file_needs_both_approval_and_sharing(f):
    """Two decisions, not one: approving lets a deck go on the main screen, sharing lets ten
    thousand people fetch the file."""
    pending_shared = f.asset(status="pending", shared=True, filename="draft.pdf")
    approved_only = f.asset(status="approved", shared=False, filename="internal.pdf")
    released = f.asset(status="approved", shared=True, filename="handout.pdf")

    names = {r["filename"] for r in
             [crud.resource_out(a) for a in crud.shared_assets(f.db, f.event.id)]}
    assert names == {"handout.pdf"}

    H = f.auth(f.viewer)
    listed = f.client.get(f"/attendee/events/{f.event.id}/resources", headers=H).json()
    assert [r["filename"] for r in listed] == ["handout.pdf"]

    for a in (pending_shared, approved_only):
        assert f.client.get(f"/attendee/events/{f.event.id}/resources/{a.id}/file",
                            headers=H).status_code == 404
    ok = f.client.get(f"/attendee/events/{f.event.id}/resources/{released.id}/file", headers=H)
    assert ok.status_code == 200 and ok.content == PDF


def test_a_download_is_an_attachment_with_hardening_headers(f):
    a = f.asset(shared=True)
    r = f.client.get(f"/attendee/events/{f.event.id}/resources/{a.id}/file",
                     headers=f.auth(f.viewer))
    # attachment, not inline: an attendee is downloading, and forcing a download is one less way
    # for user-uploaded bytes to be rendered on our own origin.
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "private" in r.headers["cache-control"]
    assert f.audits("attendee.download")


def test_sharing_is_a_moderator_decision_and_needs_approval_first(f):
    pending = f.asset(status="pending")
    # A speaker cannot release their own file to the audience.
    assert run(mod.dispatch(f.ctx(f.speaker), "presentation.share",
                            {"asset_id": str(pending.id)})) is not None
    # Nor can a moderator share something unapproved.
    err = run(mod.dispatch(f.ctx(f.moderator), "presentation.share",
                           {"asset_id": str(pending.id)}))
    assert isinstance(err, str) and "pprove" in err

    ok = f.asset(status="approved")
    assert run(mod.dispatch(f.ctx(f.moderator), "presentation.share",
                            {"asset_id": str(ok.id)})) is None
    f.db.expire_all()
    assert f.db.get(SpeakerAsset, ok.id).shared is True
    assert f.audits("live.presentation.share")


def test_unsharing_removes_it_from_the_audience(f):
    a = f.asset(status="approved", shared=True)
    run(mod.dispatch(f.ctx(f.moderator), "presentation.share",
                     {"asset_id": str(a.id), "shared": False}))
    f.db.expire_all()
    assert f.db.get(SpeakerAsset, a.id).shared is False
    assert crud.shared_assets(f.db, f.event.id) == []
    assert f.client.get(f"/attendee/events/{f.event.id}/resources/{a.id}/file",
                        headers=f.auth(f.viewer)).status_code == 404


def test_a_resource_id_cannot_reach_another_event(f):
    other = SpeakerAsset(event_id=f.gated.id, org_id=f.org.id, uploaded_by=f.speaker.id,
                         filename="x.pdf", content_type="application/pdf", kind="slides",
                         size_bytes=len(PDF), status="approved", shared=True, data=PDF)
    f.db.add(other)
    f.db.flush()
    assert crud.shared_asset(f.db, f.event.id, other.id) is None
    assert f.client.get(f"/attendee/events/{f.event.id}/resources/{other.id}/file",
                        headers=f.auth(f.viewer)).status_code == 404


def test_the_attendee_projection_hides_the_uploader_and_the_review(f):
    a = f.asset(shared=True)
    a.review_note = "internal note"
    f.db.flush()
    out = crud.resource_out(a)
    for leaked in ("review_note", "status", "uploaded_by", "data", "reviewed_by"):
        assert leaked not in out, f"{leaked} must not reach an attendee"
    assert out["filename"] == "deck.pdf" and out["shared_by"] == f.speaker.full_name


# ── The socket projection ─────────────────────────────────────────────────────

def test_the_attendee_snapshot_carries_participation_and_nothing_else(f):
    full = {
        "event": {"id": "e", "features": {"chat": True}},
        "messages": [{"id": "m"}], "questions": [], "polls": [], "announcements": [],
        "speakers": [], "identity": "u1", "reactions": {"👏": 3}, "resources": [{"id": "r"}],
        "participants": [{"identity": "someone", "chat_muted": True}],
        "activity": [{"id": "a"}], "recordings": [{"id": "rec"}], "recording": {"id": "rec"},
        "analytics": {"viewers": 9, "engagement": 40, "devices": []},
        "health": {"level": "ok"}, "broadcast": {"status": "live", "settings": {"bitrate_kbps": 9}},
        "publish_token": "tok", "notes": "private", "whiteboard": [],
        "can_moderate": True, "can_host": True, "can_speak": True,
    }
    out = mod.viewer_snapshot(full)
    for leaked in ("participants", "activity", "recordings", "recording", "health", "analytics",
                   "publish_token", "notes", "whiteboard", "broadcast"):
        assert leaked not in out, f"{leaked} must not reach an attendee"
    assert out["can_moderate"] is False and out["can_host"] is False
    # What they DO get: the room's participation surface, their own identity, the tally, the files.
    assert out["messages"] and out["identity"] == "u1"
    assert out["reactions"] == {"👏": 3} and out["resources"] == [{"id": "r"}]
    assert out["audience"] == {"viewers": 9}, "the count, not the engagement breakdown"


def test_attendee_envelopes_pass_participation_and_drop_the_rest(f):
    def env(channel, type_, data=None):
        return {"channel": channel, "type": type_, "data": data or {}}

    for channel in ("chat", "qa", "poll", "announcement", "reaction"):
        assert mod.viewer_envelope(env(channel, "anything")) is not None, channel
    for e in (env("activity", "activity.new"), env("participants", "participant.update"),
              env("moderator", "message.flagged"), env("whiteboard", "board.add"),
              env("recording", "recording.update"), env("broadcast", "settings.update")):
        assert mod.viewer_envelope(e) is None, e["channel"]
    tick = mod.viewer_envelope(env("analytics", "analytics.tick",
                                   {"viewers": 4, "engagement": 90, "hands": 2}))
    assert tick["data"] == {"viewers": 4}, "the count only"


def test_an_attendee_sees_strictly_less_than_a_speaker(f):
    snap = {"event": {}, "participants": [{"identity": "u"}], "presentation": {"asset_id": "x"},
            "whiteboard": [], "notes": "n", "publish_token": "t", "analytics": {"viewers": 1},
            "identity": "me", "reactions": {}, "can_moderate": True, "can_host": True}
    v, s = mod.viewer_snapshot(snap), mod.speaker_snapshot(snap)
    for key in ("participants", "presentation", "whiteboard", "notes", "publish_token"):
        assert key not in v and key in s, key
    # Both get their own identity and the reaction tally.
    assert v["identity"] == "me" and s["identity"] == "me"


def test_the_snapshot_extra_gives_every_tier_its_identity_and_the_tally(f):
    f.asset(shared=True, filename="slides.pdf")
    run(attendee_svc._reaction_send(f.ctx(f.viewer2), {"emoji": "🎉"}))
    extra = run(attendee_svc.snapshot_extra(f.ctx(f.viewer)))
    assert extra["identity"] == str(f.viewer.id)
    assert extra["reactions"]["🎉"] == 1
    assert [r["filename"] for r in extra["resources"]] == ["slides.pdf"]


# ── Chat / Q&A / polls from the audience ──────────────────────────────────────

def test_an_attendee_can_chat_ask_and_vote(f):
    ctx = f.ctx(f.viewer)
    assert run(mod.dispatch(ctx, "chat.send", {"text": "hello from the audience"})) is None
    assert run(mod.dispatch(ctx, "qa.ask", {"text": "what about pricing?"})) is None
    q = f.db.query(LiveQuestion).filter(LiveQuestion.user_id == f.viewer.id).one()
    assert run(mod.dispatch(ctx, "qa.vote", {"id": str(q.id)})) is None
    f.db.expire_all()
    assert f.db.get(LiveQuestion, q.id).votes == 1

    poll = LivePoll(event_id=f.event.id, org_id=f.org.id, question="Ready?",
                    options=[{"label": "Yes", "votes": 0}], status="live")
    f.db.add(poll)
    f.db.flush()
    assert run(mod.dispatch(ctx, "poll.vote", {"id": str(poll.id), "option": 0})) is None
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).options[0]["votes"] == 1


def test_the_hosts_audience_controls_apply_to_an_attendee(f):
    """An attendee is NOT staff, so chat_enabled/slow mode/emoji-only all bite."""
    ctx = f.ctx(f.viewer)
    assert mod.chat_gate({"chat_enabled": False}, ctx, "hi", {}) is not None
    assert mod.chat_gate({"emoji_only": True}, ctx, "hello", {}) is not None
    assert mod.chat_gate({"emoji_only": True}, ctx, "🎉", {}) is None
    assert mod.chat_gate({"subscriber_only": True}, ctx, "hi", {}) is not None
    # And staff bypass them.
    assert mod.chat_gate({"chat_enabled": False}, f.ctx(f.moderator), "hi", {}) is None


def test_an_attendee_can_raise_and_lower_their_own_hand_only(f):
    ctx = f.ctx(f.viewer)
    run(bus.presence_upsert(f.event.id, ctx.identity, {"name": ctx.name, "role": "viewer"}))
    run(mod.dispatch(ctx, "participant.hand", {"raised": True}))
    rec = run(bus.presence_get(f.event.id, ctx.identity))
    assert rec["hand"] is True and rec["hand_at"], "stamped, so a moderator's queue has an order"

    # Somebody else's hand is not theirs to touch.
    other = f.ctx(f.viewer2)
    run(bus.presence_upsert(f.event.id, other.identity, {"name": other.name, "hand": True}))
    err = run(mod.dispatch(ctx, "participant.dismiss_hand", {"identity": other.identity}))
    assert err and "moderator" in err.lower()
    assert run(bus.presence_get(f.event.id, other.identity))["hand"] is True


def test_an_attendee_cannot_publish_media(f):
    """The playback token is subscribe-only, and presence gives an attendee no publish sources."""
    rec = run(bus.presence_upsert(f.event.id, str(f.viewer.id),
                                  {"name": "A Viewer", "role": "viewer"}))
    assert mod.allowed_sources(rec) == ()
    assert run(speaker.snapshot_extra(f.ctx(f.viewer))) == {}, "no speaker block, so no grant"


# ── Audit coverage ────────────────────────────────────────────────────────────

def test_attendee_actions_are_audited_into_the_organizers_tenant(f):
    """An attendee belongs to another org; the ORGANIZER is the one who must be able to read their
    own event's trail."""
    H = f.auth(f.viewer)
    a = f.asset(shared=True)
    f.client.post(f"/attendee/events/{f.event.id}/register", headers=H)
    f.client.post(f"/attendee/events/{f.event.id}/bookmark", headers=H, json={"on": True})
    f.client.get(f"/attendee/events/{f.event.id}/resources/{a.id}/file", headers=H)
    f.client.delete(f"/attendee/events/{f.event.id}/register", headers=H)

    for action in ("attendee.register", "attendee.bookmark", "attendee.download",
                   "attendee.cancel_registration"):
        rows = f.audits(action)
        assert rows, f"{action} left no audit row"
        assert str(rows[-1].org_id) == str(f.org.id), f"{action} audited into the wrong tenant"
        assert rows[-1].meta.get("event_id") == str(f.event.id)


def test_chat_qa_and_poll_participation_reach_the_activity_feed(f):
    ctx = f.ctx(f.viewer)
    run(mod.dispatch(ctx, "qa.ask", {"text": "logged?"}))
    from app.models import LiveActivity
    feed = f.db.query(LiveActivity).filter(LiveActivity.event_id == f.event.id).all()
    assert any("question" in a.text.lower() for a in feed)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main():
    passed = failed = 0
    for fn in TESTS:
        f = Fixture()
        try:
            fn(f)
            passed += 1
            print(f"  ok    {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
        finally:
            f.close()
    print(f"\nattendee module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
