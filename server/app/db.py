from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

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
