"""Viewer playback quality (QoE): the token, the ingest, and the Playback page's figures.

The flow, end to end:

  GET /events/{id}/watch   an AUDIENCE member who is handed media (a live LiveKit token, or a
                           replay URL) also gets `qoe_token`: a signed, short-lived claim naming
                           a fresh playback-session id, the event and the mode. Staff of the
                           organizing organization and a contributor's monitor feed get none,
                           so they can never be counted as viewers.
  the viewer's player      measures its own playback (client/src/data/playbackQoe.js) and sends
                           cumulative totals for that session (POST /api/playback/qoe).
  ingest()                 one idempotent upsert per beacon, keyed on the token's session id.
  summarize()              server-side aggregation for /organization/playback.

Definitions the page relies on:

  playback start   a session whose player actually rendered media. Not a page load, a
                   registration, a room connection or a reconnect. Counted in SESSIONS, not
                   people: one viewer who reloads the page twice is three starts.
  reconnect        continues the session it happened in. The media gap it causes, while the
                   viewer was watching, is rebuffering.
  rebuffer         a stall AFTER playback started, while the viewer wanted playback and could
                   see the player. Initial loading, pauses, hidden tabs, seeks, a broadcaster
                   interruption the server has declared, and the stream ending are not stalls.
  rebuffer ratio   SUM(rebuffer time) / SUM(watch time) over all sessions, where watch time
                   is playing plus stalled. Not an average of per-viewer percentages, which
                   would let one viewer's ten-second visit weigh as much as a two-hour one.
  failures         sessions that hit each normalized reason, at most once per reason per
                   session.

Live (WebRTC) and replay (a file in an HTML5 player) stall for different reasons and are
measured from different signals, so they are always reported separately, never blended.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Event
from ..models.playback import PLAYBACK_FAILURE_REASONS, PLAYBACK_MODES, PlaybackSession
from . import audience as audience_svc

log = logging.getLogger(__name__)

TOKEN_TYPE = "playback-qoe"
TOKEN_TTL = timedelta(hours=24)
# Startup longer than this is not a startup time, it is a page left open. Kept, but not timed.
MAX_STARTUP_MS = 10 * 60 * 1000
# Clock slack between the token being minted and the player's own count of watch time.
ELAPSED_SLACK_MS = 2 * 60 * 1000

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30),
           "90d": timedelta(days=90), "all": None}

# The activity series' bucket per window: hours for a day, days for 7–90 days, weeks for all
# time (retention bounds "all" to PLAYBACK_QOE_RETENTION_DAYS, so it stays a short series).
SERIES_BUCKET = {"24h": "hour", "7d": "day", "30d": "day", "90d": "day", "all": "week"}
_BUCKET_STEP = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}
_SERIES_MAX_POINTS = 400


def _bucket_floor(dt: datetime, unit: str) -> datetime:
    """The UTC start of the bucket holding `dt` — the same boundary as Postgres
    date_trunc(unit, timezone('UTC', created_at)); weeks start on Monday."""
    dt = dt.astimezone(timezone.utc)
    if unit == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday()) if unit == "week" else day

# ── new-session budget (inflation protection) ──────────────────────────────────────────────
#
# /watch hands every audience request a fresh token, and must keep doing so: the watch page
# polls it, and an anonymous viewer has no identity to reuse a session by. What turns a token
# into a counted playback session is its FIRST beacon, so that is where the number of sessions
# is bounded: a /watch poll that mints a token nobody reports with costs nothing, and a script
# replaying /watch + one beacon cannot add unlimited starts, failures or stall time to an event.
#
# Per EVENT and per window, by two buckets, and a new session must fit both:
#   viewer   a credential-backed viewer (registration, invitation, access link, signed-in
#            account — the token's `vk`): reloads and a second device, not a hundred tabs.
#   address  the client's network address, kept only as a keyed hash inside the counter and
#            never stored with the session. Generous on purpose: an office audience behind one
#            NAT address is hundreds of honest viewers.
# A beacon for a session that already exists never counts against either, so a reconnect, a
# retried beacon or the session's own later beacons are unaffected. Counted in the shared
# Redis counter (services/api_usage.count_in_window) so the budget holds across workers; with
# REDIS_URL unset it degrades to per-process, which api_usage.backend() reports.
NEW_SESSION_WINDOW_SECONDS = 600
NEW_SESSIONS_PER_VIEWER = 10
NEW_SESSIONS_PER_ADDRESS = 120

# ingest() outcomes.
APPLIED = "applied"
GONE = "gone"                # the event no longer exists, or was deleted
WRONG_HOST = "wrong_host"    # sent through another organization's custom domain
OVER_BUDGET = "over_budget"  # a new session past the event's new-session budget

# How long a session row is kept (services/playback_qoe.purge_expired, a leader-only daily
# ticker). The rows are pseudonymous (viewer_key) and only ever read as aggregates; the
# Playback page's longest fixed window is 30 days and "all" means "everything retained".
RETENTION_SWEEP_SECONDS = 24 * 60 * 60
PURGE_BATCH = 5000


class BeaconRejected(ValueError):
    """A beacon that cannot be true (more watch time than the token has existed)."""


def _signing_key() -> str:
    # Derived from SECRET_KEY rather than SECRET_KEY itself: a QoE token can never verify as a
    # login or registration token, and neither of those can verify as a QoE token.
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), b"zoikostream/playback-qoe/v1",
                    hashlib.sha256).hexdigest()


def issue_token(event_id: uuid.UUID, mode: str, *, identity: str | None = None,
                registration_id: uuid.UUID | None = None, now: datetime | None = None) -> str:
    """A token for ONE playback session. Called only for audience members (watch_event)."""
    if mode not in PLAYBACK_MODES:
        raise ValueError(f"unknown playback mode {mode!r}")
    now = now or datetime.now(timezone.utc)
    claims = {
        "typ": TOKEN_TYPE,
        "sid": str(uuid.uuid4()),
        "eid": str(event_id),
        "mode": mode,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    # The same opaque key audience_sessions uses, and only for a credential-backed identity:
    # an anonymous viewer's disposable identity would link nothing to anything.
    if identity and audience_svc.identified(identity):
        claims["vk"] = audience_svc.viewer_key(identity)
    if registration_id:
        claims["rid"] = str(registration_id)
    return jwt.encode(claims, _signing_key(), algorithm="HS256")


def read_token(token: str) -> dict | None:
    """The token's claims if it is a valid, unexpired QoE token, else None."""
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except JWTError:
        return None
    if claims.get("typ") != TOKEN_TYPE or claims.get("mode") not in PLAYBACK_MODES:
        return None
    try:
        uuid.UUID(str(claims.get("sid")))
        uuid.UUID(str(claims.get("eid")))
        if claims.get("rid") is not None:
            uuid.UUID(str(claims["rid"]))
        int(claims["iat"])
    except (KeyError, TypeError, ValueError):
        return None
    return claims


def _upsert(db: Session, values: dict) -> None:
    table = PlaybackSession.__table__
    stmt = pg_insert(table).values(**values)
    new = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[table.c.id],
        set_={
            "last_seq": new.last_seq,
            "playback_started_at": func.coalesce(table.c.playback_started_at, new.playback_started_at),
            "startup_ms": func.coalesce(table.c.startup_ms, new.startup_ms),
            # Totals are cumulative, so the newer snapshot wins; GREATEST also means a
            # snapshot can never make a session's totals go DOWN.
            "playback_duration_ms": func.greatest(table.c.playback_duration_ms, new.playback_duration_ms),
            "rebuffer_count": func.greatest(table.c.rebuffer_count, new.rebuffer_count),
            "rebuffer_duration_ms": func.greatest(table.c.rebuffer_duration_ms, new.rebuffer_duration_ms),
            "failure_reasons": table.c.failure_reasons.op("||")(new.failure_reasons),
            "ended_at": func.coalesce(table.c.ended_at, new.ended_at),
            "updated_at": new.updated_at,
        },
        # The idempotency rule: only a strictly newer beacon of THIS session applies. A
        # retry, a duplicate or a late-arriving older beacon matches nothing. Atomic in
        # Postgres, so it holds across workers.
        where=table.c.last_seq < new.last_seq,
    )
    db.execute(stmt)


def _address_key(address: str) -> str:
    # Keyed, so the counter never holds an address that could be reversed by hashing the
    # whole IPv4 space; truncated, because it only has to tell buckets apart.
    return hmac.new(_signing_key().encode("utf-8"), f"qoe-address:{address}".encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def admit_new_session(event_id: uuid.UUID, claims: dict, address: str | None) -> bool:
    """Whether the event's new-session budget has room for one more session from this viewer
    and this address (see NEW_SESSION_WINDOW_SECONDS). Counts the attempt either way."""
    from . import api_usage  # deferred: api_usage pulls in email/org_comms

    window = NEW_SESSION_WINDOW_SECONDS
    viewer_key = claims.get("vk")
    if viewer_key and api_usage.count_in_window(
            "qoe-new-session-viewer", f"{event_id}:{viewer_key}", window) > NEW_SESSIONS_PER_VIEWER:
        return False
    if address and api_usage.count_in_window(
            "qoe-new-session-address", f"{event_id}:{_address_key(address)}", window) > NEW_SESSIONS_PER_ADDRESS:
        return False
    return True


def ingest(db: Session, claims: dict, beacon, *, host_org_id: uuid.UUID | None = None,
           address: str | None = None, now: datetime | None = None) -> str:
    """Apply one beacon. Returns APPLIED, GONE, WRONG_HOST or OVER_BUDGET.

    The organization and event come from the token and the events table, never from the
    beacon. `host_org_id` is the organization whose custom domain the beacon arrived on (None
    on a platform host): a customer's hostname only carries that customer's audience. Raises
    BeaconRejected for a beacon that cannot be true."""
    now = now or datetime.now(timezone.utc)
    issued = datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
    # A player cannot have watched for longer than its session has existed.
    if beacon.playback_ms > (now - issued).total_seconds() * 1000 + ELAPSED_SLACK_MS:
        raise BeaconRejected("playback_ms exceeds the session's age")

    event_id = uuid.UUID(str(claims["eid"]))
    # A deleted event is no longer measured, whether its row is gone or only soft-deleted: a
    # token minted before the delete stops counting the moment the event does.
    org_id = db.scalar(select(Event.org_id).where(Event.id == event_id, Event.deleted_at.is_(None)))
    if org_id is None:
        return GONE
    if host_org_id is not None and org_id != host_org_id:
        return WRONG_HOST

    sid = uuid.UUID(str(claims["sid"]))
    if db.scalar(select(PlaybackSession.id).where(PlaybackSession.id == sid)) is None:
        if not admit_new_session(event_id, claims, address):
            log.info("QoE: new-session budget exhausted for event %s", event_id)
            return OVER_BUDGET

    startup = beacon.startup_ms if beacon.startup_ms is not None and beacon.startup_ms <= MAX_STARTUP_MS else None
    values = {
        "id": sid,
        "org_id": org_id,
        "event_id": event_id,
        "mode": claims["mode"],
        "viewer_key": claims.get("vk"),
        "registration_id": uuid.UUID(str(claims["rid"])) if claims.get("rid") else None,
        "last_seq": beacon.seq,
        "playback_started_at": now if beacon.started else None,
        "startup_ms": startup,
        "playback_duration_ms": beacon.playback_ms,
        "rebuffer_count": beacon.rebuffer_count,
        "rebuffer_duration_ms": beacon.rebuffer_ms,
        "failure_reasons": {reason: True for reason in beacon.failures},
        "ended_at": now if beacon.ended else None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        _upsert(db, values)
        db.commit()
    except IntegrityError:
        # The registration was deleted since the token was minted (or the event, in a race).
        # The playback still happened; keep it without the link to a row that is gone.
        db.rollback()
        if values["registration_id"] is None:
            return GONE
        values["registration_id"] = None
        try:
            _upsert(db, values)
            db.commit()
        except IntegrityError:
            db.rollback()
            return GONE
    return APPLIED


# ── reading ─────────────────────────────────────────────────────────────────────────────

def _mode_figures(row, failures: dict[str, int]) -> dict:
    sessions, starts, watched, stalled, stalls, startup_median, failed = row or (0, 0, 0, 0, 0, None, 0)
    watched, stalled = int(watched or 0), int(stalled or 0)
    return {
        "sessions": int(sessions or 0),
        "playback_starts": int(starts or 0),
        "playback_duration_ms": watched,
        "rebuffer_duration_ms": stalled,
        "rebuffer_count": int(stalls or 0),
        # None, not 0, when nothing was watched: a 0% ratio would claim a clean measurement.
        # Six places, so a real but tiny ratio (under 0.005%) is not rounded to exactly 0 and
        # shown as a clean "0%" — the page renders anything under 0.1% as "<0.1%".
        "rebuffer_ratio": round(stalled / watched, 6) if watched > 0 else None,
        "startup_ms_median": int(startup_median) if startup_median is not None else None,
        "failed_sessions": int(failed or 0),
        "failures": _ranked(failures),
    }


def _ranked(failures: dict[str, int]) -> list[dict]:
    return [{"reason": reason, "count": count}
            for reason, count in sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))]


def summarize(db: Session, org_id: uuid.UUID, *, event_id: uuid.UUID | None = None,
              window: str = "all", now: datetime | None = None) -> dict:
    """The Playback page's figures for one organization (optionally one of its events) over
    one window. Every figure uses the same sessions: those that BEGAN inside the window."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}")
    now = now or datetime.now(timezone.utc)
    since = now - WINDOWS[window] if WINDOWS[window] else None

    t = PlaybackSession
    # A deleted event's sessions leave the figures with it, as its analytics do.
    deleted = select(Event.id).where(Event.org_id == org_id, Event.deleted_at.isnot(None))
    where = [t.org_id == org_id, t.created_at <= now, t.event_id.not_in(deleted)]
    if event_id is not None:
        where.append(t.event_id == event_id)
    if since is not None:
        where.append(t.created_at >= since)
    started = t.playback_started_at.isnot(None)
    failed = t.failure_reasons != cast(literal("{}"), JSONB)

    rows = db.execute(
        select(
            t.mode,
            func.count(),
            func.count().filter(started),
            func.sum(t.playback_duration_ms).filter(started),
            func.sum(t.rebuffer_duration_ms).filter(started),
            func.sum(t.rebuffer_count).filter(started),
            # percentile_cont ignores NULLs, i.e. sessions that never started.
            func.percentile_cont(0.5).within_group(t.startup_ms),
            func.count().filter(failed),
        ).where(*where).group_by(t.mode)
    ).all()
    by_mode = {row[0]: row[1:] for row in rows}

    reasons = select(t.mode.label("mode"),
                     func.jsonb_object_keys(t.failure_reasons).label("reason")).where(*where).subquery()
    failures: dict[str, dict[str, int]] = {mode: {} for mode in PLAYBACK_MODES}
    for mode, reason, count in db.execute(
        select(reasons.c.mode, reasons.c.reason, func.count()).group_by(reasons.c.mode, reasons.c.reason)
    ).all():
        # Only the closed vocabulary is ever written; this keeps a hand-edited row out.
        if reason in PLAYBACK_FAILURE_REASONS:
            failures[mode][reason] = int(count)

    modes = {mode: _mode_figures(by_mode.get(mode), failures[mode]) for mode in PLAYBACK_MODES}

    # Activity over the window (ORG-03 "Playback activity"): sessions that BEGAN in each UTC
    # bucket, from the same rows as every other figure, with empty buckets as real zeros.
    unit = SERIES_BUCKET[window]
    bucket = func.date_trunc(unit, func.timezone("UTC", t.created_at)).label("bucket")
    counted = {
        row.bucket.replace(tzinfo=timezone.utc): (int(row.sessions), int(row.starts))
        for row in db.execute(
            select(bucket, func.count().label("sessions"), func.count().filter(started).label("starts"))
            .where(*where).group_by(bucket)
        ).all()
    }
    first = _bucket_floor(since, unit) if since is not None else (min(counted) if counted else _bucket_floor(now, unit))
    points, cursor, last = [], first, _bucket_floor(now, unit)
    while cursor <= last and len(points) < _SERIES_MAX_POINTS:
        sessions, starts = counted.get(cursor, (0, 0))
        points.append({"start": cursor.isoformat(), "sessions": sessions, "playback_starts": starts})
        cursor += _BUCKET_STEP[unit]

    # Sessions per event over the same window (ORG-03 "Access by event"); an event with none
    # is simply absent, which the page reads as 0 for an event it lists.
    by_event = {
        str(eid): int(n)
        for eid, n in db.execute(select(t.event_id, func.count()).where(*where).group_by(t.event_id)).all()
    }

    combined: dict[str, int] = {}
    for mode in PLAYBACK_MODES:
        for reason, count in failures[mode].items():
            combined[reason] = combined.get(reason, 0) + count
    return {
        "window": window,
        "since": since.isoformat() if since else None,
        "until": now.isoformat(),
        "event_id": str(event_id) if event_id else None,
        **modes,
        # Counts add up across modes. Ratios and medians do not, so the total carries none.
        "total": {
            "sessions": sum(m["sessions"] for m in modes.values()),
            "playback_starts": sum(m["playback_starts"] for m in modes.values()),
            "failed_sessions": sum(m["failed_sessions"] for m in modes.values()),
            "failures": _ranked(combined),
        },
        "series": {"bucket": unit, "timezone": "UTC", "points": points},
        "by_event": by_event,
    }


# ── retention ────────────────────────────────────────────────────────────────────────────

def purge_expired(db: Session, *, now: datetime | None = None,
                  retention_days: int | None = None) -> int:
    """Delete session rows older than the retention period. Returns how many were deleted.

    Per organization, in bounded batches, so every DELETE walks ix_playback_sessions_org_created
    (org_id, created_at) instead of scanning the table, and no single statement holds row locks
    on more than PURGE_BATCH rows. retention_days <= 0 disables the purge."""
    from ..models import Organization

    days = settings.PLAYBACK_QOE_RETENTION_DAYS if retention_days is None else retention_days
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    t = PlaybackSession
    deleted = 0
    for org_id in db.scalars(select(Organization.id)).all():
        while True:
            batch = select(t.id).where(t.org_id == org_id, t.created_at < cutoff).limit(PURGE_BATCH)
            n = db.execute(t.__table__.delete().where(t.id.in_(batch.scalar_subquery()))).rowcount or 0
            db.commit()
            deleted += n
            if n < PURGE_BATCH:
                break
    if deleted:
        log.info("QoE retention: deleted %s playback session(s) older than %s days", deleted, days)
    return deleted


async def run_playback_retention_sweeper(interval: float = RETENTION_SWEEP_SECONDS) -> None:
    """Background ticker (main.py, leader process only): purge_expired once a day."""
    import asyncio

    from ..db import SessionLocal

    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                await asyncio.to_thread(purge_expired, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("playback QoE retention sweep failed")
