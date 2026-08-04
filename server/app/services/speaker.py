"""Speaker / panellist domain: the publish grant, presentation playback, the whiteboard,
private notes and raising a technical issue.

Structured exactly like services/broadcast.py, and for the same reasons: its actions register
into the ONE dispatcher in services/moderation.py (one socket, one auth check, one audit path)
and its snapshot contribution registers as a snapshot extra. Nothing here re-implements chat,
Q&A or polls — the speaker console reuses those.

Import direction is one-way (speaker -> moderation) to keep it acyclic; routers/live.py imports
this module, which is what performs the registration.

Two rules carried over from the host module:

  * A control that cannot be enforced says so. Every LiveKit call returns whether it took
    effect, and that boolean is stored and broadcast.
  * No invented telemetry. The speaker's own figures (speaking time, connection quality) come
    from real presence records; a number with no source is absent, not estimated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from ..crud import speaker as crud
from ..models import LiveQuestion
from . import bus, livekit
from . import moderation as mod

# A whiteboard object is free-form geometry from a browser, so every field is bounded. Without
# this a single frame could store a stroke with a million points and every console would then
# try to render it.
MAX_POINTS = 600
MAX_TEXT = 500
BOARD_TOOLS = ("pen", "highlighter", "line", "arrow", "rect", "ellipse", "text", "note")
# Hex colours only — the value lands in an SVG `stroke` attribute on every viewer's screen.
_HEX = "0123456789abcdefABCDEF"


def _iso(dt):
    return mod._iso(dt)


# ── presentation playback ─────────────────────────────────────────────────────
# WHICH deck is on screen and which slide lives in the bus, next to the broadcast settings: it is
# per-broadcast state that every console has to converge on, and it changes on a keypress. The
# FILE lives in Postgres (models.live.SpeakerAsset) and is fetched over REST — see
# routers/speaker.py for why bytes cannot travel over the socket.


def presentation_state(state: dict) -> dict:
    """The live presentation block, from the bus state. Always a full shape so the console can
    render "nothing is being presented" without special-casing a missing key."""
    p = state.get("presentation") or {}
    return {
        "asset_id": p.get("asset_id"),
        "filename": p.get("filename"),
        "kind": p.get("kind"),
        "pages": p.get("pages"),
        "slide": int(p.get("slide") or 1),
        "presenter_identity": p.get("presenter_identity"),
        "presenter_name": p.get("presenter_name"),
        "started_at": p.get("started_at"),
    }


def _asset_row(db, ctx, asset_id):
    return crud.get_asset(db, ctx.org_id, ctx.event_id, asset_id)


async def _presentation_start(ctx, payload):
    """Put a deck on screen.

    Three gates, all of them load-bearing:
      * the asset must resolve inside THIS event and org (crud.get_asset scopes both), so an id
        from the wire cannot reach another tenant's file;
      * it must be APPROVED — that is what the approval step is for. A moderator may override,
        because they are the ones who approve;
      * only one presentation at a time. Taking over is allowed (a panel hands off constantly)
        but it is recorded as a takeover rather than silently replacing the state.
    """
    def work(db):
        asset = _asset_row(db, ctx, payload.get("asset_id"))
        if asset is None:
            return "That file no longer exists"
        # Kind BEFORE status: "this file can never be presented" is the more useful answer, and
        # approving a .pptx would not change it.
        if asset.kind == "file":
            return ("PowerPoint can't be presented here — export it as a PDF and upload that. "
                    "You can still share your screen.")
        if asset.status != "approved" and not ctx.can_moderate:
            return "That presentation hasn't been approved yet"
        return {
            "asset_id": str(asset.id), "filename": asset.filename,
            "kind": asset.kind, "pages": asset.pages,
        }

    info = await mod.tx(work)
    if isinstance(info, str):
        return info

    state = await bus.state_get(ctx.event_id)
    previous = (state.get("presentation") or {}).get("presenter_name")
    now = datetime.now(timezone.utc)
    live = {
        **info, "slide": 1,
        "presenter_identity": ctx.identity, "presenter_name": ctx.name,
        "started_at": now.isoformat(),
    }
    await bus.state_set(ctx.event_id, {"presentation": live})

    took_over = bool(previous and previous != ctx.name)
    text = (f"{ctx.name} took over presenting: {info['filename']}" if took_over
            else f"{ctx.name} started presenting: {info['filename']}")
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "system", text, audit="live.presentation.start",
        target_type="speaker_asset", target_id=info["asset_id"],
        meta={"filename": info["filename"], "took_over": took_over}))
    return [("presentation", "presentation.update", live), ("activity", "activity.new", act)]


async def _presentation_stop(ctx, payload):
    state = await bus.state_get(ctx.event_id)
    live = state.get("presentation") or {}
    if not live.get("asset_id"):
        return "Nothing is being presented"
    # Only the presenter can end their own presentation — or a moderator, who may need to pull
    # something off screen immediately.
    if live.get("presenter_identity") != ctx.identity and not ctx.can_moderate:
        return f"{live.get('presenter_name') or 'Someone else'} is presenting"
    await bus.state_set(ctx.event_id, {"presentation": {}})
    act = await mod.tx(lambda db: mod.record(
        db, ctx, "system", f"{ctx.name} stopped presenting", audit="live.presentation.stop",
        target_type="speaker_asset", target_id=live.get("asset_id")))
    return [("presentation", "presentation.update", presentation_state({})),
            ("activity", "activity.new", act)]


async def _presentation_slide(ctx, payload):
    """Move to a slide. Broadcast rather than local so every console — and the recording
    composite — follows the presenter instead of each viewer paging independently.

    Not audited: a slide change is not a security event, and a 40-slide deck would put 40 rows in
    the compliance log for one talk. The START and STOP are audited, which is the fact that
    matters (what was shown, by whom, when).
    """
    state = await bus.state_get(ctx.event_id)
    live = state.get("presentation") or {}
    if not live.get("asset_id"):
        return "Nothing is being presented"
    if live.get("presenter_identity") != ctx.identity and not ctx.can_moderate:
        return f"{live.get('presenter_name') or 'Someone else'} is presenting"

    try:
        want = int(payload.get("slide"))
    except (TypeError, ValueError):
        return []
    # Clamp to the page count when we know it. `pages` is None for a PDF whose page tree we
    # couldn't read (crud.speaker.count_pdf_pages says why), and in that case the browser's own
    # viewer is the authority — so the number is only floored, not capped.
    pages = live.get("pages")
    slide = max(1, min(want, int(pages))) if pages else max(1, want)
    updated = {**live, "slide": slide}
    await bus.state_set(ctx.event_id, {"presentation": updated})
    return [("presentation", "presentation.update", updated)]


async def _presentation_share(ctx, payload):
    """Share (or unshare) an approved file with the AUDIENCE as a download.

    A second decision, not a side effect of approval: approving a deck lets it go on the main
    screen, sharing it lets everybody watching fetch the file. An unapproved file can never be
    shared — otherwise "share" would become a way around the review step.

    Moderator tier (it is absent from SPEAKER_ACTIONS), because releasing a file to ten thousand
    people is the organizers' call.
    """
    share = bool(payload.get("shared", True))

    def work(db):
        asset = _asset_row(db, ctx, payload.get("asset_id"))
        if asset is None:
            return None
        if share and asset.status != "approved":
            return "Approve the file before sharing it with the audience"
        asset.shared = share
        verb = "shared with the audience" if share else "withdrawn from the audience"
        act = mod.record(db, ctx, "system",
                         f"{ctx.name} {verb}: {asset.filename}",
                         audit=f"live.presentation.{'share' if share else 'unshare'}",
                         target_type="speaker_asset", target_id=asset.id,
                         meta={"filename": asset.filename, "shared": share})
        return crud.asset_out(asset), act

    out = await mod.tx(work)
    if out is None:
        return []
    if isinstance(out, str):
        return out
    asset, act = out
    # `resource.update` reaches the audience: `presentation` is in VIEWER_CHANNELS, so an attendee's
    # downloads list appears the moment a host shares something, with no refresh.
    return [("presentation", "asset.update", asset),
            ("presentation", "resource.update", {"shared": share, "resource": asset}),
            ("activity", "activity.new", act)]


async def _presentation_review(ctx, payload):
    """Approve or reject an uploaded deck. MODERATOR tier, not speaker — the point of the step is
    that somebody other than the uploader vets what goes on the main screen."""
    approve = bool(payload.get("approved", True))
    note = mod._text(payload, "note", 400)
    now = datetime.now(timezone.utc)

    def work(db):
        asset = _asset_row(db, ctx, payload.get("asset_id"))
        if asset is None:
            return None
        asset.status = "approved" if approve else "rejected"
        asset.reviewed_by = ctx.user_id
        asset.reviewed_at = now
        asset.review_note = note or None
        verb = "approved" if approve else "rejected"
        act = mod.record(db, ctx, "system",
                         f"{ctx.name} {verb} {asset.uploader_name or 'a speaker'}'s presentation "
                         f"({asset.filename})",
                         audit=f"live.presentation.{verb}", target_type="speaker_asset",
                         target_id=asset.id, meta={"filename": asset.filename, "note": note})
        return crud.asset_out(asset), act

    out = await mod.tx(work)
    if not out:
        return []
    asset, act = out
    # Addressed to the UPLOADER as well as broadcast: "Presentation Approved" is one of the
    # notifications a speaker is promised, and they may not be looking at the file list.
    frames = [("presentation", "asset.update", asset), ("activity", "activity.new", act)]
    if asset["uploaded_by"]:
        frames.append(("participants", "participant.notice", {
            "to_identity": asset["uploaded_by"],
            "from_name": ctx.name,
            "text": (f"Your presentation \"{asset['filename']}\" was approved."
                     if approve else
                     f"Your presentation \"{asset['filename']}\" was rejected"
                     + (f": {note}" if note else ".")),
        }))
    return frames


# ── whiteboard ────────────────────────────────────────────────────────────────
# SVG geometry in the bus, one object per stroke/shape. No canvas library and no new table:
# objects are broadcast as they are drawn, the board is rebuilt from the bus on connect, and it
# is dropped when the room ends (services/bus.board_*). Export is client-side.


def _num(value, lo, hi, default=0.0):
    try:
        return max(lo, min(float(value), hi))
    except (TypeError, ValueError):
        return default


def _colour(value, default="#0f172a"):
    """A hex colour or the default. Free text would land in an SVG attribute on every screen."""
    s = str(value or "")
    if len(s) in (4, 7) and s[0] == "#" and all(c in _HEX for c in s[1:]):
        return s
    return default


def clean_object(payload: dict, *, obj_id: str, author: str, name: str) -> dict | None:
    """Validate one whiteboard object. Returns None when there is nothing to draw.

    Coordinates are NORMALISED (0..1), not pixels, so a board drawn on a 4K display renders in
    the same place on a laptop — the alternative is a whiteboard that only lines up for whoever
    drew it.
    """
    tool = payload.get("tool") if payload.get("tool") in BOARD_TOOLS else "pen"
    obj = {
        "id": obj_id, "tool": tool, "author": author, "author_name": name,
        "colour": _colour(payload.get("colour")),
        "width": _num(payload.get("width"), 1, 40, 3),
        "at": datetime.now(timezone.utc).timestamp(),
    }
    if tool in ("pen", "highlighter"):
        raw = payload.get("points") or []
        pts = [[_num(p[0], 0, 1), _num(p[1], 0, 1)] for p in raw[:MAX_POINTS]
               if isinstance(p, (list, tuple)) and len(p) >= 2]
        if len(pts) < 2:
            return None
        obj["points"] = pts
    elif tool in ("line", "arrow", "rect", "ellipse"):
        obj["x"] = _num(payload.get("x"), 0, 1)
        obj["y"] = _num(payload.get("y"), 0, 1)
        obj["x2"] = _num(payload.get("x2"), 0, 1)
        obj["y2"] = _num(payload.get("y2"), 0, 1)
        if obj["x"] == obj["x2"] and obj["y"] == obj["y2"]:
            return None
    else:  # text | note
        text = mod._text(payload, "text", MAX_TEXT)
        if not text:
            return None
        obj["text"] = text
        obj["x"] = _num(payload.get("x"), 0, 1)
        obj["y"] = _num(payload.get("y"), 0, 1)
    return obj


async def _board_draw(ctx, payload):
    obj = clean_object(payload, obj_id=str(uuid.uuid4()), author=ctx.identity, name=ctx.name)
    if obj is None:
        return []
    stored = await bus.board_add(ctx.event_id, obj)
    if stored is None:
        return "The whiteboard is full — clear it to keep drawing"
    return [("whiteboard", "board.add", stored)]


async def _board_erase(ctx, payload):
    """Delete one object. A speaker may only erase their OWN marks; a moderator may erase any —
    which is also the undo path, because undo is "remove the last thing I drew".
    """
    obj_id = str(payload.get("id") or "")
    if not obj_id:
        return []
    existing = {o["id"]: o for o in await bus.board_all(ctx.event_id)}
    obj = existing.get(obj_id)
    if obj is None:
        return []
    if obj.get("author") != ctx.identity and not ctx.can_moderate:
        return "You can only erase your own marks"
    await bus.board_remove(ctx.event_id, obj_id)
    return [("whiteboard", "board.remove", {"id": obj_id})]


async def _board_clear(ctx, payload):
    """Wipe the board. Audited, unlike a single stroke: it destroys everyone's work."""
    mine_only = not ctx.can_moderate
    if mine_only:
        # A speaker clears their own marks; only a moderator wipes the whole board, because the
        # rest of it belongs to other people.
        removed = 0
        for obj in await bus.board_all(ctx.event_id):
            if obj.get("author") == ctx.identity:
                await bus.board_remove(ctx.event_id, obj["id"])
                removed += 1
        frames = [("whiteboard", "board.reset", {"objects": await bus.board_all(ctx.event_id)})]
    else:
        removed = await bus.board_clear(ctx.event_id)
        frames = [("whiteboard", "board.reset", {"objects": []})]

    act = await mod.tx(lambda db: mod.record(
        db, ctx, "system",
        f"{ctx.name} cleared {'their marks on' if mine_only else 'the whiteboard'} "
        f"({removed} object(s))",
        audit="live.whiteboard.clear", meta={"removed": removed, "own_only": mine_only}))
    return [*frames, ("activity", "activity.new", act)]


# ── private notes ─────────────────────────────────────────────────────────────


async def _notes_save(ctx, payload):
    """Save the caller's own notes onto their assignment row.

    Returned to NOBODY: the frames list is empty on success. Notes are private, and the bus fans
    every returned frame to every subscriber — so returning the text here would publish a
    speaker's private prep to the whole room. The client already has what it typed.
    """
    text = mod._text(payload, "notes", 20000)

    def work(db):
        return crud.save_notes(db, ctx.event_id, ctx.user_id, text)

    saved = await mod.tx(work)
    if not saved:
        return "You aren't assigned to this event"
    return []


# ── technical issues ──────────────────────────────────────────────────────────


ISSUE_KINDS = ("audio", "video", "network", "screen_share", "presentation", "other")


async def _issue_raise(ctx, payload):
    """"Raise a technical issue" — a speaker telling the people running the show that something
    is wrong, without having to type it into the public chat.

    Goes to the CONSOLES only: `activity` is not in the attendee or speaker channel allow-lists,
    so the audience never sees "my microphone is broken". Audited, because a post-mortem asking
    "when did the speaker first report the audio problem" needs an answer.
    """
    kind = payload.get("kind") if payload.get("kind") in ISSUE_KINDS else "other"
    detail = mod._text(payload, "detail", 500)
    # The reporter's own measured figures, so a moderator sees the numbers and not just the
    # complaint. Bounded on the way in like all client telemetry.
    rec = await bus.presence_get(ctx.event_id, ctx.identity)
    measured = {k: rec.get(k) for k in ("quality", "bitrate_kbps", "packet_loss", "rtt_ms", "fps")
                if rec.get(k) is not None}

    act = await mod.tx(lambda db: mod.record(
        db, ctx, "mod", f"{ctx.name} reported a {kind.replace('_', ' ')} problem"
                        + (f": {detail}" if detail else ""),
        audit="live.speaker.issue", target_type="participant", target_id=ctx.identity,
        meta={"kind": kind, "detail": detail, "measured": measured}))
    return [("activity", "activity.new", act),
            ("moderator", "speaker.issue", {
                "identity": ctx.identity, "name": ctx.name, "kind": kind,
                "detail": detail, "measured": measured, "at": act["created_at"],
            })]


# ── snapshot contribution ─────────────────────────────────────────────────────


async def snapshot_extra(ctx) -> dict:
    """Merged into the socket's opening snapshot, so a speaker console paints complete from the
    first frame. Everything here is either the caller's own or already public to the stage."""
    if not ctx.can_speak:
        # Nothing to add for an attendee or a pure moderator. Returning early also keeps the
        # attendee connect path from doing three extra reads it would never use.
        return {}

    state = await bus.state_get(ctx.event_id)
    presence = await bus.presence_get(ctx.event_id, ctx.identity)
    sources = mod.allowed_sources(presence)

    def work(db):
        assets = crud.list_assets(db, ctx.org_id, ctx.event_id)
        notes = crud.notes_for(db, ctx.event_id, ctx.user_id)
        # The questions routed to ME. The full Q&A list is already in the snapshot; this is the
        # speaker's own worklist, so the console doesn't have to know its user id to filter.
        mine = db.scalars(
            select(LiveQuestion).where(
                LiveQuestion.event_id == ctx.event_id,
                LiveQuestion.org_id == ctx.org_id,
                LiveQuestion.assigned_to == ctx.user_id,
            ).order_by(LiveQuestion.created_at.desc()).limit(mod.HISTORY_LIMIT)
        ).all()
        return assets, notes, [mod.question_out(q) for q in mine]

    assets, notes, assigned = await mod.tx(work)

    return {
        "can_speak": True,
        # This connection's own identity. The client needs it to answer "is this question mine",
        # "is this whiteboard mark mine" and "which tile am I" — all of which are keyed on it
        # server-side. It is the caller's own user id, which they already hold in their JWT.
        "identity": ctx.identity,
        "assets": assets,
        "notes": notes,
        "assigned_questions": assigned,
        "presentation": presentation_state(state),
        "whiteboard": await bus.board_all(ctx.event_id),
        # What this speaker is currently allowed to send, and how they have been doing.
        "stage": {
            "on_stage": bool(presence.get("on_stage")) or presence.get("role") in ("host", "speaker"),
            "sources": list(sources),
            "camera_allowed": presence.get("camera_allowed") is not False,
            "share_allowed": livekit.SCREEN_SHARE in sources,
            "muted": bool(presence.get("muted")),
        },
        "speaking": {
            "seconds": round(int(presence.get("speaking_ms") or 0) / 1000),
            "quality": presence.get("quality"),
        },
        "livekit_url": livekit.settings.LIVEKIT_URL or None,
        # THE speaker publish grant — the gap that made this whole console impossible before.
        #
        # Source-scoped, and only for the sources presence says they may use, so an on-stage
        # panellist cannot put their desktop on the main screen until somebody grants share. The
        # token is only the JOIN credential: livekit.set_publish_sources updates the live
        # permission at the SFU, which is what governs an already-connected publisher (a token
        # cannot be revoked once issued).
        #
        # A host already gets an unscoped token from broadcast.snapshot_extra; this one is keyed
        # under different names so the two never fight, and the host console keeps using theirs.
        "publish_identity": broadcast_publisher_identity(ctx.identity),
        "publish_token": livekit.create_stream_token(
            broadcast_publisher_identity(ctx.identity), ctx.room, True, sources=sources,
        ) if (sources and livekit.configured()) else None,
        "publish_sources": list(sources),
    }


def broadcast_publisher_identity(identity: str) -> str:
    """The suffixed publisher identity, imported lazily to keep this module's import direction
    one-way. LiveKit allows one connection per identity per room and drops the older one, and a
    speaker's attendee/playback token uses the bare user id — so without the suffix a speaker
    with the watch page open in another tab would kick their own feed off the stage."""
    from . import broadcast

    return broadcast.publisher_identity(identity)


# ── registration into the shared dispatcher ───────────────────────────────────

ACTIONS = {
    "presentation.start": _presentation_start,
    "presentation.stop": _presentation_stop,
    "presentation.slide": _presentation_slide,
    "presentation.review": _presentation_review,
    "presentation.share": _presentation_share,
    "whiteboard.draw": _board_draw,
    "whiteboard.erase": _board_erase,
    "whiteboard.clear": _board_clear,
    "notes.save": _notes_save,
    "speaker.issue": _issue_raise,
}

# Speaker tier. presentation.review and presentation.share are deliberately ABSENT — approving a
# deck and releasing it to the audience are both organizer decisions, so they fall through to the
# default can_moderate branch in mod.dispatch.
SPEAKER_ACTIONS = {a for a in ACTIONS
                   if a not in ("presentation.review", "presentation.share")} | {
    # Answering and escalating live in moderation.py (they are Q&A), but the tier is decided here
    # so the whole speaker permission set is readable in one place. Both check per-ROW that the
    # question is assigned to the caller.
    "qa.respond", "qa.escalate",
}

mod.ACTIONS.update(ACTIONS)
mod.SPEAKER_ACTIONS.update(SPEAKER_ACTIONS)
mod.SNAPSHOT_EXTRAS.append(snapshot_extra)
