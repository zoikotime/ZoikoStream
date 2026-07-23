"""Auto-closes events once their scheduled end time has passed.

There's no separate job queue (Celery/etc.) in this project, so this runs as a plain
asyncio loop started alongside the FastAPI app (see main.py's lifespan). It only flips
DB status -- it does not forcibly disconnect anyone from the LiveKit room, matching how
the manual "End Event" button already behaves (that also only flips status; the host's
own browser is what actually calls room.disconnect()).
"""
import asyncio
import logging
from datetime import datetime, time as dtime, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.db import SessionLocal
from app.models.stream import Stream

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30


def _scheduled_end_utc(stream: Stream) -> datetime | None:
    if not stream.scheduled_date or not stream.end_time:
        return None
    try:
        hour, minute = (int(p) for p in stream.end_time.split(":"))
    except ValueError:
        return None
    try:
        tz = ZoneInfo(stream.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        # e.g. "IST" is ambiguous and not a real IANA zone -- skip rather than guess wrong.
        log.warning(
            "Stream %s has an unrecognized timezone %r -- skipping auto-close check",
            stream.id, stream.timezone,
        )
        return None
    local_end = datetime.combine(stream.scheduled_date, dtime(hour, minute), tzinfo=tz)
    return local_end.astimezone(dt_timezone.utc)


def close_past_due_events() -> int:
    """Runs one sweep. Returns how many events it closed (useful for tests/manual calls)."""
    now = datetime.now(dt_timezone.utc)
    closed = 0
    with SessionLocal() as db:
        candidates = db.scalars(select(Stream).where(Stream.status.in_(["scheduled", "live"]))).all()

        for stream in candidates:
            end_utc = _scheduled_end_utc(stream)
            if end_utc is None or now < end_utc:
                continue

            was_live = stream.status == "live"
            stream.is_live = False
            # Scheduled-but-never-started events didn't happen -- "canceled" is more
            # honest than "completed" for those.
            stream.status = "completed" if was_live else "canceled"
            stream.ended_at = now
            closed += 1
            log.info("Auto-closed event %s (%s) -- past its scheduled end time", stream.id, stream.title)

        if closed:
            db.commit()
    return closed


async def run_auto_close_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(close_past_due_events)
        except Exception:
            log.exception("Auto-close sweep failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
