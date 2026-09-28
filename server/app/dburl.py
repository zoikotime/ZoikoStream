"""One PostgreSQL driver for every engine this project creates: psycopg2.

SQLAlchemy 2.1 changed which DBAPI a plain `postgresql://` URL loads, from psycopg2 to
psycopg (version 3). Every URL this project is configured with is written plain (.env, CI,
Cloud Run), and the only PostgreSQL driver installed is psycopg2-binary (requirements.txt and
server/requirements.txt). So once pip resolved SQLAlchemy 2.1, every engine failed at creation
with `ModuleNotFoundError: No module named 'psycopg'` - while CI's service-container probe,
which imports psycopg2 directly, still passed. Naming the driver makes the choice explicit
instead of inheriting it from whichever SQLAlchemy release was installed.

Applied at ENGINE CREATION only. Settings, app/testguard.py and conftest.py keep the URL
exactly as configured, so their database-identity comparisons are unaffected.

No imports from the rest of the app: standalone scripts (clean_dev_fixtures.py,
verify_billing_readiness.py) use this without triggering app/db.py's engine or test guard.
"""

_PLAIN_SCHEMES = ("postgresql://", "postgres://")
PSYCOPG2_SCHEME = "postgresql+psycopg2://"


def engine_url(url: str) -> str:
    """`url` with the psycopg2 driver named explicitly when it names none.

    An explicit driver (`postgresql+psycopg2://`, `postgresql+psycopg://`, ...) and any
    non-PostgreSQL URL are returned unchanged.
    """
    for scheme in _PLAIN_SCHEMES:
        if url[:len(scheme)].lower() == scheme:
            return PSYCOPG2_SCHEME + url[len(scheme):]
    return url
