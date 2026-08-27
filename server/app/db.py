import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

log = logging.getLogger(__name__)

# Connection pool, sized deliberately rather than left on SQLAlchemy's defaults (5 + 10).
#
# This matters more here than in a plain request/response app: every WebSocket action goes
# through services.moderation.tx(), which opens a short-lived Session on a worker thread.
# With the defaults, ~15 concurrent live actions exhausted the pool and the rest blocked on
# checkout for pool_timeout seconds — presenting as a hung console, not an error.
#
# DB_MAX_CONNECTIONS is the ceiling this process can hold; main.py sizes the thread pool to
# match, so a thread can never wait on a connection that structurally cannot exist.
# ponytail: tuned for one uvicorn worker against the Supabase pooler. Multiply by the worker
# count when scaling out and check it against the pooler's own limit before raising these.
DB_POOL_SIZE = 20
DB_MAX_OVERFLOW = 10
DB_MAX_CONNECTIONS = DB_POOL_SIZE + DB_MAX_OVERFLOW

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,      # keeps the Supabase pooler connection healthy across idle periods
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_recycle=1800,       # the pooler drops idle connections; recycle before it does
    pool_timeout=10,         # fail fast instead of hanging a socket action for 30s
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Background-ticker leadership (Postgres advisory lock) ────────────────────────────────
#
# The six tickers in main.py used to run in EVERY process. With more than one instance —
# which is the normal state of an autoscaling deployment — each scheduled poll fired N times,
# each pending webhook was delivered N times, and each watermark burn ran N times against the
# same object.
#
# A session-scoped Postgres advisory lock elects exactly one leader. Postgres is the right
# place for it, not Redis: REDIS_URL is optional (services/bus.py degrades to in-process when
# unset) whereas the database is mandatory, so a Redis-based lock would silently stop
# protecting anything in the exact deployment that skipped Redis.
#
# The property that makes this safe is that the lock is bound to the CONNECTION, not to a
# timeout: if the leader crashes, is OOM-killed, or loses its network, Postgres drops the
# connection and releases the lock automatically. There is no lease to expire and no clock to
# agree on, so a follower can take over without ever overlapping the dead leader.

# Arbitrary but stable 64-bit key. Changing it would let an old and a new deployment both
# believe they are leader during a rollout, so it is a constant, never derived from anything.
TICKER_LOCK_KEY = 0x7A01C0DE

_leader_connection = None


def try_acquire_ticker_leadership() -> bool:
    """Attempt to become the single process that runs the background tickers.

    Returns True if this process now holds leadership. Non-blocking: `pg_try_advisory_lock`
    returns false immediately rather than queueing, so a follower starts serving HTTP without
    waiting on the leader.

    Safe to call repeatedly. A process that already holds the lock re-acquires it re-entrantly
    (Postgres counts per session), which is why release below drops it unconditionally rather
    than counting down.
    """
    global _leader_connection
    if _leader_connection is not None:
        return True
    conn = engine.connect()
    try:
        acquired = bool(conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": TICKER_LOCK_KEY}
        ).scalar())
    except Exception:
        conn.close()
        raise
    if not acquired:
        # Hand the connection straight back — a follower must not hold one from the pool for
        # the life of the process just to say "not me".
        conn.close()
        return False
    _leader_connection = conn
    return True


def holds_ticker_leadership() -> bool:
    """Whether this process still holds the lock, verified against Postgres.

    Not a cached boolean on purpose. If the leader's connection died the lock is already gone
    and a follower may have taken over, so a stale flag would mean two processes running the
    same ticker — the exact thing this mechanism exists to prevent. The check is a round-trip
    on the lock's own connection, so a dead connection surfaces as a failure rather than a
    confident lie.
    """
    global _leader_connection
    if _leader_connection is None:
        return False
    try:
        return bool(_leader_connection.execute(
            text(
                "SELECT count(*) > 0 FROM pg_locks "
                "WHERE locktype = 'advisory' AND objid = :k AND pid = pg_backend_pid()"
            ),
            {"k": TICKER_LOCK_KEY},
        ).scalar())
    except Exception as exc:
        log.warning("ticker leadership check failed, treating leadership as lost: %s", exc)
        release_ticker_leadership()
        return False


def release_ticker_leadership() -> None:
    """Give up leadership so another instance can take over promptly.

    Called on clean shutdown. An unclean exit needs no equivalent — closing the socket releases
    the lock, which is the whole reason this uses a connection-bound lock.
    """
    global _leader_connection
    conn, _leader_connection = _leader_connection, None
    if conn is None:
        return
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICKER_LOCK_KEY})
    except Exception as exc:
        log.info("advisory unlock failed (connection likely already gone): %s", exc)
    finally:
        conn.close()
