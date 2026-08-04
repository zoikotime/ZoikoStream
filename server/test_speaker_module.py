"""Speaker & Panellist Dashboard — server-side checks.

Real database inside a transaction that is ALWAYS rolled back, same discipline as the host,
event, invitation and moderator suites.

The centre of gravity here is the PUBLISH GRANT. Before this module a speaker could be staged in
LiveKit with no token behind it, so "a speaker may share their screen" had to be checked against
the thing that actually decides: the source list derived from presence. Most of these tests are
about that, and about the line between a speaker and a moderator — a speaker publishes and answers
their own questions, and runs nothing.

Run: `python test_speaker_module.py` (or pytest).
"""
import asyncio
import io
import uuid

from starlette.testclient import TestClient

import app.main as m
from app.config import settings
from app.crud import speaker as crud_speaker
from app.db import SessionLocal, get_db
from app.models import (
    AuditLog, Event, EventAssignment, LiveActivity, LivePoll, LiveQuestion, MAX_ASSET_BYTES,
    Organization, SpeakerAsset, User,
)
from app.security import create_access_token, hash_password
from app.services import broadcast, bus, livekit, moderation as mod, speaker

settings.RESEND_API_KEY = ""

# A one-page PDF, byte for byte. Small enough to inline, real enough that the magic-byte check and
# the page-count parser both see what they expect.
PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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

        self.org = Organization(name=f"Spk Org {uniq}", status="active")
        self.other_org = Organization(name=f"Spk Other {uniq}", status="active")
        self.db.add_all([self.org, self.other_org])
        self.db.flush()

        self.admin = self._user(self.org.id, "Org Admin", f"sadm{uniq}", "org_admin")
        self.host = self._user(self.org.id, "The Host", f"shost{uniq}", "host")
        self.moderator = self._user(self.org.id, "The Moderator", f"smod{uniq}", "moderator")
        self.speaker = self._user(self.org.id, "The Speaker", f"sspk{uniq}", "speaker")
        self.panelist = self._user(self.org.id, "The Panellist", f"span{uniq}", "speaker")
        self.viewer = self._user(self.org.id, "A Viewer", f"svw{uniq}", "viewer")
        self.outsider = self._user(self.other_org.id, "Outside Speaker", f"sout{uniq}", "speaker")
        self.db.flush()

        self.event = Event(org_id=self.org.id, created_by=self.admin.id, title=f"Spk Event {uniq}",
                           slug=f"spk-event-{uniq}", status="live", visibility="public")
        self.unassigned = Event(org_id=self.org.id, created_by=self.admin.id, title="Not mine",
                                slug=f"spk-other-{uniq}", status="published")
        self.db.add_all([self.event, self.unassigned])
        self.db.flush()

        self.db.add_all([
            EventAssignment(event_id=self.event.id, user_id=self.host.id, role="host"),
            EventAssignment(event_id=self.event.id, user_id=self.moderator.id, role="moderator"),
            EventAssignment(event_id=self.event.id, user_id=self.speaker.id, role="speaker"),
            # `panelist` is a credited team role; it must earn the same console as `speaker`.
            EventAssignment(event_id=self.event.id, user_id=self.panelist.id, role="panelist"),
        ])
        self.db.flush()

        proxy = _NonClosing(self.db)
        # The REQUEST session is the proxy too, not the raw one. This suite is the first to POST
        # and DELETE over REST, and those routers commit — which ends the transaction this fixture
        # rolls back, leaving real rows (and 25 MB bytea payloads) in the database. Routing the
        # dependency through the proxy turns those commits into flushes, so the rollback actually
        # reverses everything while the code under test is unchanged.
        m.app.dependency_overrides[get_db] = lambda: proxy
        self.client = TestClient(m.app)

        self._patched = []
        for module in (mod, broadcast, speaker):
            self._patched.append((module, getattr(module, "SessionLocal", None)))
            if hasattr(module, "SessionLocal"):
                module.SessionLocal = lambda _p=proxy: _p

        asyncio.run(bus.presence_clear(self.event.id))

    def _user(self, org_id, name, slug, role):
        u = User(org_id=org_id, full_name=name, email=f"{slug}@example.com", username=slug,
                 password_hash=hash_password("pw"), role=role)
        self.db.add(u)
        return u

    def auth(self, user):
        return {"Authorization": f"Bearer {create_access_token(user, remember=False)}"}

    def ctx(self, user, event=None):
        return mod.resolve_ctx((event or self.event).id, user)

    def join(self, user, **patch):
        """Put someone in presence the way routers/live.py does on socket accept — including the
        `speaker` rung of the role ladder, which is what the publish grant reads."""
        ctx = self.ctx(user)
        role = ("host" if ctx.can_host else "moderator" if ctx.can_moderate
                else "speaker" if ctx.can_speak else "viewer")
        return asyncio.run(bus.presence_upsert(
            self.event.id, ctx.identity,
            {"name": ctx.name, "role": role, "waiting": False, "muted": False, **patch}))

    def asset(self, user=None, *, status="approved", kind="slides", data=PDF, pages=1,
              filename="deck.pdf", content_type="application/pdf"):
        who = user or self.speaker
        a = SpeakerAsset(
            event_id=self.event.id, org_id=self.org.id, uploaded_by=who.id,
            uploader_name=who.full_name, filename=filename, content_type=content_type,
            kind=kind, size_bytes=len(data), pages=pages, status=status, data=data,
        )
        self.db.add(a)
        self.db.flush()
        return a

    def question(self, author, *, assigned_to=None, text="why?", **cols):
        q = LiveQuestion(event_id=self.event.id, org_id=self.org.id, user_id=author.id,
                         author_name=author.full_name, text=text, flags=[],
                         assigned_to=assigned_to.id if assigned_to else None,
                         assigned_name=assigned_to.full_name if assigned_to else None, **cols)
        self.db.add(q)
        self.db.flush()
        return q

    def audits(self, action):
        # Scoped to THIS fixture's org. Unscoped, a leftover row from any other run (or another
        # tenant) would satisfy — or break — an assertion that is specifically about this tenant.
        return (self.db.query(AuditLog)
                .filter(AuditLog.action == action, AuditLog.org_id == self.org.id)
                .order_by(AuditLog.created_at).all())

    def close(self):
        for module, original in getattr(self, "_patched", []):
            if original is not None:
                module.SessionLocal = original
        m.app.dependency_overrides.clear()
        try:
            asyncio.run(bus.presence_clear(self.event.id))
            asyncio.run(bus.board_clear(self.event.id))
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


# ── RBAC: what a speaker is, and is not ───────────────────────────────────────

def test_an_assignment_is_what_makes_a_speaker(f):
    assert f.ctx(f.speaker).can_speak is True
    # A panellist earns the same console — the media layer cannot tell the difference.
    assert f.ctx(f.panelist).can_speak is True
    # Staff present their own slides, so they are speakers too.
    assert f.ctx(f.host).can_speak is True
    assert f.ctx(f.admin).can_speak is True

    assert f.ctx(f.viewer).can_speak is False
    # A moderator with no speaking assignment is not a speaker.
    assert f.ctx(f.moderator).can_speak is False
    # And the org's speaker role alone is not a licence to speak at every event in it.
    assert f.ctx(f.speaker, f.unassigned).can_speak is False


def test_a_speaker_is_not_a_moderator_or_a_host(f):
    ctx = f.ctx(f.speaker)
    assert ctx.can_moderate is False and ctx.can_host is False
    for action in ("broadcast.end", "recording.start", "broadcast.golive"):
        err = run(mod.dispatch(ctx, action, {}))
        assert err and "host" in err.lower(), action
    for action in ("chat.delete", "participant.remove", "stage.admit", "qa.approve",
                   "poll.create", "announce.send", "presentation.review"):
        err = run(mod.dispatch(ctx, action, {"id": str(uuid.uuid4()), "identity": "x"}))
        assert err and "moderator" in err.lower(), f"{action} must need can_moderate, got {err!r}"


def test_cross_tenant_speaker_gets_nothing(f):
    ctx = f.ctx(f.outsider)
    assert ctx is not None, "a public event stays viewable — that part is intended"
    assert ctx.can_speak is False and ctx.can_moderate is False
    # Even WITH an assignment row: accepting an invitation creates assignments, so a stale one
    # crossing tenants is reachable rather than theoretical.
    f.db.add(EventAssignment(event_id=f.event.id, user_id=f.outsider.id, role="speaker"))
    f.db.flush()
    assert f.ctx(f.outsider).can_speak is False


def test_forbidden_rest_paths_refuse_a_speaker(f):
    H = f.auth(f.speaker)
    assert f.client.delete(f"/events/{f.event.id}", headers=H).status_code == 403
    assert f.client.patch(f"/events/{f.event.id}", headers=H, json={"title": "x"}).status_code == 403
    assert f.client.patch("/organization/profile", headers=H, json={"name": "x"}).status_code == 403
    assert f.client.get(f"/events/{f.event.id}/access-links", headers=H).status_code == 403
    assert f.client.patch(f"/events/{f.event.id}/team/speaker", headers=H,
                          json={"user_ids": [str(f.viewer.id)]}).status_code == 403


def test_the_speaker_tier_is_disjoint_from_the_others(f):
    """A gate that appears in two sets would be judged by whichever branch dispatch reaches
    first, which is exactly the kind of overlap that silently widens a permission."""
    assert mod.SPEAKER_ACTIONS, "the speaker set must not be empty"
    assert not (mod.SPEAKER_ACTIONS & mod.HOST_ONLY)
    assert not (mod.SPEAKER_ACTIONS & mod.VIEWER_ACTIONS)
    assert all(a in mod.ACTIONS for a in mod.SPEAKER_ACTIONS), "every gated action is registered"
    # Approving a presentation is a moderator decision, not a speaker's.
    assert "presentation.review" not in mod.SPEAKER_ACTIONS


# ── The publish grant ─────────────────────────────────────────────────────────

def test_a_speaker_gets_camera_and_mic_but_never_screen_share_by_default(f):
    """The whole point of source scoping. Being brought on stage to answer a question must not also
    hand somebody the main screen."""
    rec = f.join(f.speaker)
    sources = mod.allowed_sources(rec)
    assert livekit.CAMERA in sources and livekit.MICROPHONE in sources
    assert livekit.SCREEN_SHARE not in sources, "share needs an explicit grant"

    # A HOST presents by default — they are the one running the show.
    assert livekit.SCREEN_SHARE in mod.allowed_sources(f.join(f.host))


def test_an_audience_member_may_publish_nothing_until_staged(f):
    rec = f.join(f.viewer)
    assert mod.allowed_sources(rec) == ()
    staged = {**rec, "on_stage": True}
    assert livekit.CAMERA in mod.allowed_sources(staged)


def test_a_banned_or_waiting_participant_publishes_nothing(f):
    base = f.join(f.speaker)
    assert mod.allowed_sources({**base, "banned": True}) == ()
    assert mod.allowed_sources({**base, "waiting": True}) == ()


def test_disabling_a_camera_no_longer_cuts_the_microphone(f):
    """The regression that matters most here. Every media control used to be expressed as
    `can_publish` on/off, so "turn this speaker's camera off" revoked their whole publish
    permission — cutting their audio mid-sentence."""
    f.join(f.speaker)
    sid = str(f.speaker.id)
    run(mod.dispatch(f.ctx(f.moderator), "stage.camera", {"identity": sid, "allowed": False}))
    rec = run(bus.presence_get(f.event.id, sid))
    sources = mod.allowed_sources(rec)
    assert livekit.CAMERA not in sources, "the camera is off"
    assert livekit.MICROPHONE in sources, "but they can still be HEARD"


def test_share_can_be_granted_and_taken_away(f):
    f.join(f.speaker)
    sid, ctx = str(f.speaker.id), f.ctx(f.moderator)

    run(mod.dispatch(ctx, "stage.share", {"identity": sid, "allowed": True}))
    assert livekit.SCREEN_SHARE in mod.allowed_sources(run(bus.presence_get(f.event.id, sid)))

    run(mod.dispatch(ctx, "stage.share", {"identity": sid, "allowed": False}))
    after = mod.allowed_sources(run(bus.presence_get(f.event.id, sid)))
    assert livekit.SCREEN_SHARE not in after
    assert livekit.CAMERA in after and livekit.MICROPHONE in after, "only the share was revoked"
    assert f.audits("live.stage.share")


def test_the_token_is_scoped_to_exactly_those_sources(f):
    """A grant is only half of it — but it is the half a client holds, so it must not be wider than
    the permission."""
    f.join(f.speaker)
    snap = run(speaker.snapshot_extra(f.ctx(f.speaker)))
    assert snap["can_speak"] is True
    assert set(snap["publish_sources"]) == {livekit.CAMERA, livekit.MICROPHONE}
    assert snap["publish_identity"].endswith("#host"), "suffixed, or a second tab kicks them off"
    if livekit.configured():
        assert snap["publish_token"], "an on-stage speaker must get a grant"

    # An attendee gets no speaker block at all.
    assert run(speaker.snapshot_extra(f.ctx(f.viewer))) == {}


def test_an_offstage_speaker_gets_no_token(f):
    """A speaker who has not been brought on cannot be handed a publish credential — that is the
    "unauthorized screen sharing" guard at the token level."""
    f.join(f.viewer)                       # in the room, role viewer, not staged
    snap = run(speaker.snapshot_extra(f.ctx(f.viewer)))
    assert snap == {}, "not a speaker at all"

    # A speaker whose sources have all been revoked also gets nothing.
    f.join(f.speaker, camera_allowed=False, mic_allowed=False)
    snap = run(speaker.snapshot_extra(f.ctx(f.speaker)))
    assert snap["publish_sources"] == []
    assert snap["publish_token"] is None


def test_staging_does_not_relabel_an_assigned_speaker(f):
    """participant.stage used to write role="viewer" on the way down, which demoted a real speaker
    and — now that presence drives the grant — would have silently removed their publish rights."""
    f.join(f.speaker)
    sid, ctx = str(f.speaker.id), f.ctx(f.moderator)
    run(mod.dispatch(ctx, "participant.stage", {"identity": sid, "on_stage": False}))
    rec = run(bus.presence_get(f.event.id, sid))
    assert rec["role"] == "speaker", "an assigned speaker keeps their role"

    # An attendee promoted to the stage IS relabelled — that is what the branch is for.
    f.join(f.viewer)
    run(mod.dispatch(ctx, "participant.stage", {"identity": str(f.viewer.id), "on_stage": True}))
    assert run(bus.presence_get(f.event.id, str(f.viewer.id)))["role"] == "speaker"


def test_the_token_helper_narrows_its_grant(f):
    """clean_sources drops anything LiveKit doesn't know, so a typo cannot widen a grant."""
    assert livekit.clean_sources(["camera", "nonsense"]) == [livekit.CAMERA]
    assert livekit.clean_sources([]) == []
    assert livekit.clean_sources(None) == []
    if livekit.configured():
        scoped = livekit.create_stream_token("u1", "event_x", True, sources=[livekit.CAMERA])
        wide = livekit.create_stream_token("u1", "event_x", True)
        assert scoped and wide and scoped != wide


# ── Presentations ─────────────────────────────────────────────────────────────

def test_a_speaker_can_upload_and_a_pdf_is_paged(f):
    r = f.client.post(f"/speaker/events/{f.event.id}/assets", headers=f.auth(f.speaker),
                      files={"file": ("talk.pdf", io.BytesIO(PDF), "application/pdf")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "slides" and body["pages"] == 1
    assert body["status"] == "pending", "not approved on arrival"
    assert f.audits("live.presentation.upload")


def test_an_image_is_one_slide_and_powerpoint_is_not_presentable(f):
    H = f.auth(f.speaker)
    img = f.client.post(f"/speaker/events/{f.event.id}/assets", headers=H,
                        files={"file": ("slide.png", io.BytesIO(PNG), "image/png")}).json()
    assert img["kind"] == "image" and img["pages"] == 1

    ppt = f.client.post(
        f"/speaker/events/{f.event.id}/assets", headers=H,
        files={"file": ("deck.pptx", io.BytesIO(b"PK\x03\x04junk"),
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    ).json()
    # Accepted (speakers have .pptx) but explicitly NOT presentable — no converter in this stack.
    assert ppt["kind"] == "file" and ppt["pages"] is None

    err = run(speaker._presentation_start(f.ctx(f.speaker), {"asset_id": ppt["id"]}))
    assert isinstance(err, str) and "PDF" in err


def test_uploads_are_filtered_by_type_and_content(f):
    H = f.auth(f.speaker)
    # An executable dressed as a presentation would be served back from our own origin.
    assert f.client.post(f"/speaker/events/{f.event.id}/assets", headers=H,
                         files={"file": ("x.html", io.BytesIO(b"<script>"), "text/html")}
                         ).status_code == 415
    # A declared PDF that is not one.
    assert f.client.post(f"/speaker/events/{f.event.id}/assets", headers=H,
                         files={"file": ("fake.pdf", io.BytesIO(b"not a pdf"), "application/pdf")}
                         ).status_code == 400
    assert f.client.post(f"/speaker/events/{f.event.id}/assets", headers=H,
                         files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
                         ).status_code == 400


def test_an_oversized_upload_is_refused(f):
    big = b"%PDF-1.4\n" + b"\x00" * (MAX_ASSET_BYTES + 1024)
    r = f.client.post(f"/speaker/events/{f.event.id}/assets", headers=f.auth(f.speaker),
                      files={"file": ("huge.pdf", io.BytesIO(big), "application/pdf")})
    assert r.status_code == 413, r.status_code


def test_only_the_events_team_can_upload_or_read(f):
    """A viewer of a public event is not on its team, and an outsider's event id must 404 rather
    than 403 — the event is resolved org-scoped before any permission is considered."""
    assert f.client.post(f"/speaker/events/{f.event.id}/assets", headers=f.auth(f.viewer),
                         files={"file": ("x.pdf", io.BytesIO(PDF), "application/pdf")}
                         ).status_code == 403
    assert f.client.get(f"/speaker/events/{f.event.id}/assets",
                        headers=f.auth(f.viewer)).status_code == 403
    assert f.client.get(f"/speaker/events/{f.event.id}/assets",
                        headers=f.auth(f.outsider)).status_code == 404
    # A moderator may read (they approve) but not upload into somebody else's session.
    assert f.client.get(f"/speaker/events/{f.event.id}/assets",
                        headers=f.auth(f.moderator)).status_code == 200


def test_an_unapproved_file_is_only_visible_to_its_owner_and_staff(f):
    pending = f.asset(f.speaker, status="pending")
    url = f"/speaker/events/{f.event.id}/assets/{pending.id}/file"
    assert f.client.get(url, headers=f.auth(f.speaker)).status_code == 200, "own file previews"
    assert f.client.get(url, headers=f.auth(f.moderator)).status_code == 200, "staff review it"
    assert f.client.get(url, headers=f.auth(f.panelist)).status_code == 403, "another speaker's"

    approved = f.asset(f.speaker, status="approved")
    assert f.client.get(f"/speaker/events/{f.event.id}/assets/{approved.id}/file",
                        headers=f.auth(f.panelist)).status_code == 200


def test_the_download_sets_hardening_headers(f):
    a = f.asset()
    r = f.client.get(f"/speaker/events/{f.event.id}/assets/{a.id}/file", headers=f.auth(f.speaker))
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "inline" in r.headers["content-disposition"]
    assert "private" in r.headers["cache-control"]


def test_a_file_id_cannot_reach_another_event(f):
    """Every asset lookup is scoped to the event AND the org, so an id from the wire is useless
    outside its own session."""
    other = SpeakerAsset(event_id=f.unassigned.id, org_id=f.org.id, uploaded_by=f.speaker.id,
                         filename="x.pdf", content_type="application/pdf", kind="slides",
                         size_bytes=len(PDF), status="approved", data=PDF)
    f.db.add(other)
    f.db.flush()
    assert crud_speaker.get_asset(f.db, f.org.id, f.event.id, other.id) is None
    assert f.client.get(f"/speaker/events/{f.event.id}/assets/{other.id}/file",
                        headers=f.auth(f.speaker)).status_code == 404


def test_a_speaker_deletes_only_their_own_file(f):
    theirs = f.asset(f.speaker)
    someone_elses = f.asset(f.panelist)
    H = f.auth(f.speaker)
    assert f.client.delete(f"/speaker/events/{f.event.id}/assets/{someone_elses.id}",
                           headers=H).status_code == 403
    assert f.client.delete(f"/speaker/events/{f.event.id}/assets/{theirs.id}",
                           headers=H).status_code == 204
    f.db.expire_all()
    assert f.db.get(SpeakerAsset, theirs.id).deleted_at is not None, "soft delete, so audit resolves"
    # A moderator can clear anything on the event.
    assert f.client.delete(f"/speaker/events/{f.event.id}/assets/{someone_elses.id}",
                           headers=f.auth(f.moderator)).status_code == 204
    assert f.audits("live.presentation.delete")


def test_a_listing_never_carries_the_bytes(f):
    """Ten decks would otherwise pull 250 MB through the connection to render a file list."""
    f.asset()
    rows = crud_speaker.list_assets(f.db, f.org.id, f.event.id)
    assert rows and "data" not in rows[0]
    assert rows[0]["size_bytes"] == len(PDF)


def test_the_per_event_file_cap_holds(f):
    for i in range(crud_speaker.MAX_ASSETS_PER_EVENT):
        f.asset(filename=f"d{i}.pdf")
    r = f.client.post(f"/speaker/events/{f.event.id}/assets", headers=f.auth(f.speaker),
                      files={"file": ("one-more.pdf", io.BytesIO(PDF), "application/pdf")})
    assert r.status_code == 409


def test_page_counting_is_honest_about_what_it_cannot_read(f):
    assert crud_speaker.count_pdf_pages(PDF) == 1
    # No page tree and no page objects -> None, not a guess. The console then lets the browser's
    # own viewer page freely instead of clamping to a wrong number.
    assert crud_speaker.count_pdf_pages(b"%PDF-1.4\ncompressed junk\n") is None


# ── Presentation playback ─────────────────────────────────────────────────────

def test_an_unapproved_deck_cannot_go_on_screen(f):
    pending = f.asset(status="pending")
    err = run(speaker._presentation_start(f.ctx(f.speaker), {"asset_id": str(pending.id)}))
    assert isinstance(err, str) and "approved" in err
    # A moderator may override — they are the ones who approve.
    out = run(speaker._presentation_start(f.ctx(f.moderator), {"asset_id": str(pending.id)}))
    assert not isinstance(out, str), out


def test_presenting_broadcasts_state_and_audits_start_and_stop(f):
    a = f.asset()
    ctx = f.ctx(f.speaker)
    assert run(mod.dispatch(ctx, "presentation.start", {"asset_id": str(a.id)})) is None
    live = (run(bus.state_get(f.event.id))).get("presentation")
    assert live["asset_id"] == str(a.id) and live["slide"] == 1
    assert live["presenter_identity"] == ctx.identity
    assert f.audits("live.presentation.start")

    assert run(mod.dispatch(ctx, "presentation.slide", {"slide": 1})) is None
    assert run(mod.dispatch(ctx, "presentation.stop", {})) is None
    assert not (run(bus.state_get(f.event.id))).get("presentation", {}).get("asset_id")
    assert f.audits("live.presentation.stop")


def test_only_the_presenter_drives_the_deck(f):
    a = f.asset()
    run(mod.dispatch(f.ctx(f.speaker), "presentation.start", {"asset_id": str(a.id)}))
    # Another panellist cannot page somebody else's slides or take them off screen.
    assert isinstance(run(mod.dispatch(f.ctx(f.panelist), "presentation.slide", {"slide": 5})), str)
    assert isinstance(run(mod.dispatch(f.ctx(f.panelist), "presentation.stop", {})), str)
    # A moderator can — the live save when a speaker's laptop dies.
    assert run(mod.dispatch(f.ctx(f.moderator), "presentation.slide", {"slide": 1})) is None


def test_a_slide_number_is_clamped_to_the_page_count(f):
    a = f.asset(pages=3)
    ctx = f.ctx(f.speaker)
    run(mod.dispatch(ctx, "presentation.start", {"asset_id": str(a.id)}))
    run(mod.dispatch(ctx, "presentation.slide", {"slide": 99}))
    assert (run(bus.state_get(f.event.id)))["presentation"]["slide"] == 3
    run(mod.dispatch(ctx, "presentation.slide", {"slide": -4}))
    assert (run(bus.state_get(f.event.id)))["presentation"]["slide"] == 1
    # Garbage is ignored rather than coerced to a slide.
    run(mod.dispatch(ctx, "presentation.slide", {"slide": "nonsense"}))
    assert (run(bus.state_get(f.event.id)))["presentation"]["slide"] == 1


def test_approval_notifies_the_uploader_privately(f):
    a = f.asset(f.speaker, status="pending")
    frames = run(speaker._presentation_review(f.ctx(f.moderator),
                                              {"asset_id": str(a.id), "approved": True}))
    kinds = {(c, t) for c, t, _d in frames}
    assert ("presentation", "asset.update") in kinds
    notice = next(d for _c, t, d in frames if t == "participant.notice")
    assert notice["to_identity"] == str(f.speaker.id), "addressed to the uploader"
    assert "approved" in notice["text"]

    f.db.expire_all()
    row = f.db.get(SpeakerAsset, a.id)
    assert row.status == "approved" and row.reviewed_by == f.moderator.id
    assert f.audits("live.presentation.approved")


def test_a_rejection_carries_the_reason(f):
    a = f.asset(status="pending")
    run(speaker._presentation_review(f.ctx(f.moderator),
                                     {"asset_id": str(a.id), "approved": False,
                                      "note": "wrong deck"}))
    f.db.expire_all()
    row = f.db.get(SpeakerAsset, a.id)
    assert row.status == "rejected" and row.review_note == "wrong deck"
    assert f.audits("live.presentation.rejected")


# ── Whiteboard ────────────────────────────────────────────────────────────────

def test_drawing_broadcasts_a_bounded_object(f):
    ctx = f.ctx(f.speaker)
    assert run(mod.dispatch(ctx, "whiteboard.draw", {
        "tool": "pen", "colour": "#ff0000", "width": 5,
        "points": [[0.1, 0.1], [0.2, 0.2]],
    })) is None
    board = run(bus.board_all(f.event.id))
    assert len(board) == 1
    obj = board[0]
    assert obj["author"] == ctx.identity and obj["colour"] == "#ff0000"


def test_geometry_from_the_wire_is_clamped_and_sanitised(f):
    """Every field lands in an SVG attribute on every viewer's screen, so none of it is trusted."""
    obj = speaker.clean_object({
        "tool": "rect", "colour": "javascript:alert(1)", "width": 9999,
        "x": -5, "y": 2, "x2": 0.5, "y2": 0.5,
    }, obj_id="x", author="a", name="A")
    assert obj["colour"] == "#0f172a", "a non-hex colour falls back"
    assert obj["width"] <= 40
    assert 0 <= obj["x"] <= 1 and 0 <= obj["y"] <= 1

    # An unknown tool becomes a pen rather than passing through.
    pen = speaker.clean_object({"tool": "exploit", "points": [[0, 0], [1, 1]]},
                               obj_id="y", author="a", name="A")
    assert pen["tool"] == "pen"
    # Point count is capped.
    long = speaker.clean_object({"tool": "pen", "points": [[0, 0]] * 5000},
                                obj_id="z", author="a", name="A")
    assert len(long["points"]) <= speaker.MAX_POINTS
    # Nothing to draw -> nothing stored.
    assert speaker.clean_object({"tool": "pen", "points": [[0, 0]]}, obj_id="q", author="a", name="A") is None
    assert speaker.clean_object({"tool": "text", "text": "  "}, obj_id="q", author="a", name="A") is None


def test_a_speaker_erases_only_their_own_marks(f):
    run(mod.dispatch(f.ctx(f.speaker), "whiteboard.draw",
                     {"tool": "pen", "points": [[0, 0], [1, 1]]}))
    mark = (run(bus.board_all(f.event.id)))[0]

    err = run(mod.dispatch(f.ctx(f.panelist), "whiteboard.erase", {"id": mark["id"]}))
    assert isinstance(err, str) and "your own" in err
    assert len(run(bus.board_all(f.event.id))) == 1

    assert run(mod.dispatch(f.ctx(f.speaker), "whiteboard.erase", {"id": mark["id"]})) is None
    assert run(bus.board_all(f.event.id)) == []


def test_clear_removes_only_mine_unless_a_moderator_does_it(f):
    run(mod.dispatch(f.ctx(f.speaker), "whiteboard.draw", {"tool": "pen", "points": [[0, 0], [1, 1]]}))
    run(mod.dispatch(f.ctx(f.panelist), "whiteboard.draw", {"tool": "pen", "points": [[0, 1], [1, 0]]}))
    assert len(run(bus.board_all(f.event.id))) == 2

    run(mod.dispatch(f.ctx(f.speaker), "whiteboard.clear", {}))
    left = run(bus.board_all(f.event.id))
    assert len(left) == 1 and left[0]["author"] == str(f.panelist.id), "other people's work survives"

    run(mod.dispatch(f.ctx(f.moderator), "whiteboard.clear", {}))
    assert run(bus.board_all(f.event.id)) == []
    assert f.audits("live.whiteboard.clear")


def test_the_board_is_capped(f):
    """A full board says so rather than silently dropping the stroke somebody just drew."""
    original = bus.MAX_BOARD_OBJECTS
    bus.MAX_BOARD_OBJECTS = 2
    try:
        ctx = f.ctx(f.speaker)
        for _ in range(2):
            assert run(mod.dispatch(ctx, "whiteboard.draw",
                                    {"tool": "pen", "points": [[0, 0], [1, 1]]})) is None
        err = run(mod.dispatch(ctx, "whiteboard.draw",
                               {"tool": "pen", "points": [[0, 0], [1, 1]]}))
        assert isinstance(err, str) and "full" in err
    finally:
        bus.MAX_BOARD_OBJECTS = original


def test_an_attendee_cannot_draw(f):
    err = run(mod.dispatch(f.ctx(f.viewer), "whiteboard.draw",
                           {"tool": "pen", "points": [[0, 0], [1, 1]]}))
    assert err and "speaker" in err.lower()
    assert run(bus.board_all(f.event.id)) == []


# ── Q&A ───────────────────────────────────────────────────────────────────────

def test_a_speaker_answers_only_questions_assigned_to_them(f):
    mine = f.question(f.viewer, assigned_to=f.speaker, text="for me")
    theirs = f.question(f.viewer, assigned_to=f.panelist, text="for them")
    ctx = f.ctx(f.speaker)

    assert run(mod.dispatch(ctx, "qa.respond",
                            {"id": str(mine.id), "answer": "because", "completed": True})) is None
    f.db.expire_all()
    row = f.db.get(LiveQuestion, mine.id)
    assert row.status == "answered" and row.answer_text == "because"
    assert row.answered_by == f.speaker.id and row.answered_at is not None
    assert f.audits("live.question.respond")

    err = run(mod.dispatch(ctx, "qa.respond", {"id": str(theirs.id), "answer": "nope"}))
    assert isinstance(err, str) and "assigned to you" in err
    f.db.expire_all()
    assert f.db.get(LiveQuestion, theirs.id).answer_text is None


def test_an_unassigned_question_is_nobodys_to_answer(f):
    q = f.question(f.viewer)
    err = run(mod.dispatch(f.ctx(f.speaker), "qa.respond", {"id": str(q.id), "answer": "x"}))
    assert isinstance(err, str)
    # A moderator may answer anything — they route the queue.
    assert run(mod.dispatch(f.ctx(f.moderator), "qa.respond",
                            {"id": str(q.id), "answer": "mine now"})) is None


def test_answering_without_typing_still_closes_the_question(f):
    """"I answered it out loud" is the common case, and forcing a transcript would mean nobody
    marks anything done."""
    q = f.question(f.viewer, assigned_to=f.speaker)
    run(mod.dispatch(f.ctx(f.speaker), "qa.respond", {"id": str(q.id), "completed": True}))
    f.db.expire_all()
    row = f.db.get(LiveQuestion, q.id)
    assert row.status == "answered" and row.answer_text is None


def test_saving_an_answer_without_closing_leaves_it_open(f):
    q = f.question(f.viewer, assigned_to=f.speaker, status="approved")
    run(mod.dispatch(f.ctx(f.speaker), "qa.respond",
                     {"id": str(q.id), "answer": "draft", "completed": False}))
    f.db.expire_all()
    row = f.db.get(LiveQuestion, q.id)
    assert row.answer_text == "draft" and row.status != "answered"


def test_escalating_hands_a_question_back_without_deleting_it(f):
    q = f.question(f.viewer, assigned_to=f.speaker, status="approved")
    assert run(mod.dispatch(f.ctx(f.speaker), "qa.escalate",
                            {"id": str(q.id), "reason": "not my area"})) is None
    f.db.expire_all()
    row = f.db.get(LiveQuestion, q.id)
    assert row is not None, "a flag, never a delete — the moderator decides"
    assert "escalated" in (row.flags or []) and row.status == "pending"
    assert f.audits("live.question.escalate")

    # And not somebody else's question.
    other = f.question(f.viewer, assigned_to=f.panelist)
    assert isinstance(run(mod.dispatch(f.ctx(f.speaker), "qa.escalate", {"id": str(other.id)})), str)


def test_the_snapshot_carries_only_my_assigned_questions(f):
    f.question(f.viewer, assigned_to=f.speaker, text="mine")
    f.question(f.viewer, assigned_to=f.panelist, text="theirs")
    f.question(f.viewer, text="nobody's")
    f.join(f.speaker)
    snap = run(speaker.snapshot_extra(f.ctx(f.speaker)))
    texts = {q["text"] for q in snap["assigned_questions"]}
    assert texts == {"mine"}


# ── Polls / chat ──────────────────────────────────────────────────────────────

def test_a_speaker_votes_but_cannot_run_a_poll(f):
    poll = LivePoll(event_id=f.event.id, org_id=f.org.id, question="Ready?",
                    options=[{"label": "Yes", "votes": 0}, {"label": "No", "votes": 0}],
                    status="live")
    f.db.add(poll)
    f.db.flush()
    ctx = f.ctx(f.speaker)

    assert run(mod.dispatch(ctx, "poll.vote", {"id": str(poll.id), "option": 0})) is None
    f.db.expire_all()
    assert f.db.get(LivePoll, poll.id).options[0]["votes"] == 1

    for action in ("poll.create", "poll.launch", "poll.close", "poll.delete"):
        assert run(mod.dispatch(ctx, action, {"id": str(poll.id), "question": "x",
                                              "options": ["a", "b"]})), action


def test_a_speaker_can_reply_in_chat(f):
    """chat.send is a viewer action, so a speaker gets it for free — and staff bypass the audience
    controls, which a speaker deliberately does NOT."""
    f.join(f.speaker)
    ctx = f.ctx(f.speaker)
    assert run(mod.dispatch(ctx, "chat.send", {"text": "hello from the stage"})) is None
    # Chat turned off applies to a speaker: they are on stage, not running the room.
    assert mod.chat_gate({"chat_enabled": False}, ctx, "hi", {}) is not None
    assert mod.chat_gate({"chat_enabled": False}, f.ctx(f.moderator), "hi", {}) is None


# ── Notes, issues, analytics ──────────────────────────────────────────────────

def test_notes_are_private_and_never_broadcast(f):
    """The bus fans every returned frame to every subscriber, so a handler that echoed the text
    would publish a speaker's prep to the whole room."""
    frames = run(speaker._notes_save(f.ctx(f.speaker), {"notes": "open with the demo"}))
    assert frames == [], "no frames at all"
    assert crud_speaker.notes_for(f.db, f.event.id, f.speaker.id) == "open with the demo"
    # And they are not in anybody else's snapshot.
    f.join(f.panelist)
    assert run(speaker.snapshot_extra(f.ctx(f.panelist)))["notes"] is None


def test_notes_need_an_assignment(f):
    out = run(speaker._notes_save(f.ctx(f.viewer), {"notes": "sneaky"}))
    assert isinstance(out, str) and "assigned" in out


def test_notes_are_readable_over_rest_for_the_green_room(f):
    run(speaker._notes_save(f.ctx(f.speaker), {"notes": "prep"}))
    r = f.client.get(f"/speaker/events/{f.event.id}/notes", headers=f.auth(f.speaker))
    assert r.status_code == 200 and r.json()["notes"] == "prep"
    # Somebody else's notes are not reachable through this route — it only ever reads the caller's.
    assert f.client.get(f"/speaker/events/{f.event.id}/notes",
                        headers=f.auth(f.panelist)).json()["notes"] is None


def test_a_technical_issue_reaches_the_consoles_and_not_the_room(f):
    f.join(f.speaker, quality="poor", packet_loss=12)
    frames = run(mod.dispatch(f.ctx(f.speaker), "speaker.issue",
                              {"kind": "audio", "detail": "crackling"}))
    assert frames is None, frames
    # `activity` and `moderator` are both absent from the attendee allow-list.
    assert mod.viewer_envelope({"channel": "moderator", "type": "speaker.issue", "data": {}}) is None
    assert mod.viewer_envelope({"channel": "activity", "type": "activity.new", "data": {}}) is None

    rows = f.audits("live.speaker.issue")
    assert rows and rows[-1].meta["kind"] == "audio"
    # The speaker's own measured figures ride along, so a moderator sees numbers not just a moan.
    assert rows[-1].meta["measured"]["quality"] == "poor"
    feed = f.db.query(LiveActivity).filter(LiveActivity.event_id == f.event.id).all()
    assert any("crackling" in a.text for a in feed)


def test_an_unknown_issue_kind_falls_back_rather_than_being_stored(f):
    run(mod.dispatch(f.ctx(f.speaker), "speaker.issue", {"kind": "<script>", "detail": ""}))
    assert f.audits("live.speaker.issue")[-1].meta["kind"] == "other"


def test_speaking_time_and_quality_reach_the_speakers_own_snapshot(f):
    f.join(f.speaker, speaking_ms=90_000, quality="good")
    snap = run(speaker.snapshot_extra(f.ctx(f.speaker)))
    assert snap["speaking"]["seconds"] == 90
    assert snap["speaking"]["quality"] == "good"
    assert snap["stage"]["on_stage"] is True


# ── Projection: what a speaker may and may not receive ────────────────────────

def test_the_speaker_projection_drops_the_moderation_surface(f):
    """A speaker is ON the broadcast, so they get the stage roster and the presentation. They are
    not running it, so the audit timeline, recordings and the full analytics block stay out."""
    full = {
        "event": {"id": "e"}, "participants": [{"identity": "u1", "name": "A", "role": "speaker",
                                                "chat_muted": True, "muted_until": "x",
                                                "banned": False, "bitrate_kbps": 900}],
        "messages": [], "questions": [], "polls": [], "announcements": [],
        "activity": [{"id": "a"}], "recordings": [{"id": "r"}], "recording": {"id": "r"},
        "analytics": {"viewers": 12, "participants": 20, "engagement": 44, "devices": []},
        "health": {"level": "ok"}, "broadcast": {"status": "live", "settings": {"bitrate_kbps": 9}},
        "can_moderate": True, "can_host": True, "can_speak": True,
        "presentation": {"asset_id": "x"}, "whiteboard": [], "notes": "mine",
        "publish_token": "tok", "publish_sources": ["camera"],
    }
    out = mod.speaker_snapshot(full)

    for leaked in ("activity", "recordings", "recording", "health", "analytics"):
        assert leaked not in out, f"{leaked} must not reach a speaker"
    assert out["can_moderate"] is False and out["can_host"] is False
    # Their own things DO come through.
    assert out["presentation"] and out["notes"] == "mine" and out["publish_token"] == "tok"
    # The audience figures they are entitled to, without the engagement/device breakdown.
    assert out["audience"] == {"viewers": 12, "participants": 20, "speakers": 0, "hands": 0}
    # And the roster is narrowed: a moderator's working notes on somebody are not stage business.
    p = out["participants"][0]
    assert p["role"] == "speaker"
    for leaked in ("chat_muted", "muted_until", "banned", "bitrate_kbps"):
        assert leaked not in p, f"{leaked} must not reach a speaker"


def test_speaker_envelopes_pass_participation_and_drop_moderation(f):
    def env(channel, type_, data=None):
        return {"channel": channel, "type": type_, "data": data or {}}

    # Channels they take part in, unfiltered.
    for channel in ("chat", "qa", "poll", "announcement", "presentation", "whiteboard"):
        assert mod.speaker_envelope(env(channel, "anything")) is not None, channel
    # The moderation surface is dropped entirely.
    for e in (env("activity", "activity.new"), env("moderator", "message.flagged"),
              env("recording", "recording.update"), env("broadcast", "settings.update")):
        assert mod.speaker_envelope(e) is None, e["channel"]
    # Stage changes come through, narrowed by the presence allow-list.
    rec = mod.speaker_envelope(env("participants", "participant.update",
                                   {"identity": "u", "role": "speaker", "chat_muted": True}))
    assert rec and "chat_muted" not in rec["data"] and rec["data"]["role"] == "speaker"
    # The analytics tick is narrowed to counters.
    tick = mod.speaker_envelope(env("analytics", "analytics.tick",
                                    {"viewers": 3, "engagement": 90, "devices": []}))
    assert tick["data"] == {"viewers": 3}


def test_an_attendee_still_sees_less_than_a_speaker(f):
    """The two projections must not have converged: the speaker tier exists precisely because it
    sits between the attendee and the console."""
    snap = {"event": {}, "participants": [{"identity": "u"}], "presentation": {"asset_id": "x"},
            "whiteboard": [], "notes": "n", "publish_token": "t", "analytics": {"viewers": 1},
            "can_moderate": True, "can_host": True}
    viewer = mod.viewer_snapshot(snap)
    spk = mod.speaker_snapshot(snap)
    for key in ("participants", "presentation", "whiteboard", "notes", "publish_token"):
        assert key not in viewer, f"an attendee must not receive {key}"
        assert key in spk, f"a speaker must receive {key}"


# ── The dashboard's data ──────────────────────────────────────────────────────

def test_assigned_role_speaker_or_panelist_is_what_my_sessions_means(f):
    H = f.auth(f.speaker)
    mine = f.client.get("/events", headers=H,
                        params={"assigned": "me", "assigned_role": ["speaker", "panelist"]}).json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == str(f.event.id)
    # A panellist's own listing finds it through the other role name.
    theirs = f.client.get("/events", headers=f.auth(f.panelist),
                          params={"assigned": "me", "assigned_role": ["speaker", "panelist"]}).json()
    assert theirs["total"] == 1
    # Reading every event in the org is a different question, and still allowed.
    assert f.client.get("/events", headers=H).json()["total"] >= 2


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
    print(f"\nspeaker module: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
