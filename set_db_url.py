"""Point this backend at a PostgreSQL instance, safely.

    python set_db_url.py                      # local: localhost:5432/zoikostream
    python set_db_url.py --create-db          # ...and create the database if it is missing
    python set_db_url.py --host 34.29.53.66:5433   # back to the remote one

It asks for the username and password, reads the password with getpass (never echoed),
CONNECTS FIRST, and only rewrites the DATABASE_URL line in .env once that connection has
actually succeeded. A mistyped password therefore leaves your configuration exactly as it was
rather than replacing one broken URL with another.

The password is never printed and never written anywhere except .env.

WHY PERCENT-ENCODING: a PostgreSQL URL separates credentials from the host on "@", so a
password containing "@" splits the URL in the wrong place and libpq tries to resolve the
leftover as a hostname — the observed failure was

    could not translate host name "10>@34.29.53.66" to address

"<", ">", "/", ":" and "#" are reserved in a URI too. Encoding the user and password fixes all
of them and is harmless when they contain nothing special.

Delete this file once the URL is set; it is a one-off convenience, not part of the app.
"""
import argparse
import getpass
import io
import os
import re
import sys
import urllib.parse as urlparse

DEFAULT_HOST = "localhost:5432"
DEFAULT_DB = "zoikostream"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def build_url(user: str, password: str, host: str, db: str) -> str:
    """safe="" so EVERY reserved character is encoded, including @ : / < > and #."""
    return (f"postgresql://{urlparse.quote(user, safe='')}:"
            f"{urlparse.quote(password, safe='')}@{host}/{db}")


def masked(url: str) -> str:
    return re.sub(r"(//)[^@]*(@)", r"\1<redacted>\2", url)


def _connect(url: str, timeout: int = 8):
    """Open one connection and close it. Returns (ok, error_message)."""
    from sqlalchemy import create_engine, text
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://", 1),
                           pool_pre_ping=False, connect_args={"connect_timeout": timeout})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 — the message is the whole point
        first = (str(exc).splitlines() or [""])[0]
        return False, first
    finally:
        engine.dispose()


def _create_database(user: str, password: str, host: str, db: str) -> tuple[bool, str]:
    """CREATE DATABASE via the `postgres` maintenance database on the same server."""
    from sqlalchemy import create_engine, text
    admin_url = build_url(user, password, host, "postgres").replace(
        "postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(admin_url, pool_pre_ping=False, connect_args={"connect_timeout": 8},
                           isolation_level="AUTOCOMMIT")  # CREATE DATABASE cannot run in a tx
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": db}).scalar()
            if exists:
                return True, "already existed"
            # Identifier cannot be parameterised; the name comes from --db, not from input().
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", db):
                return False, f"refusing to create a database with an unsafe name: {db!r}"
            conn.execute(text(f'CREATE DATABASE "{db}"'))
            return True, "created"
    except Exception as exc:  # noqa: BLE001
        return False, (str(exc).splitlines() or [""])[0]
    finally:
        engine.dispose()


def rewrite_env(path: str, url: str) -> bool:
    """Replace the DATABASE_URL line in place, leaving every other line byte-for-byte."""
    if not os.path.exists(path):
        return False
    text_ = io.open(path, encoding="utf-8", newline="").read()
    # A backslash in the replacement would be interpreted as a group reference.
    new, n = re.subn(r"(?m)^DATABASE_URL=.*$",
                     lambda _m: "DATABASE_URL=" + url, text_, count=1)
    if n == 0:
        new = text_.rstrip("\n") + "\nDATABASE_URL=" + url + "\n"
    io.open(path, "w", encoding="utf-8", newline="").write(new)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Point the backend at a PostgreSQL instance")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"host:port (default {DEFAULT_HOST})")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database name (default {DEFAULT_DB})")
    ap.add_argument("--create-db", action="store_true",
                    help="create the database if the server does not have it yet")
    args = ap.parse_args()

    print(f"target: {args.host}/{args.db}\n")
    user = input("database user [postgres]: ").strip() or "postgres"
    if user.startswith("<"):
        print("  that looks like a placeholder, not a username — nothing written.", file=sys.stderr)
        return 2
    password = getpass.getpass("database password (not echoed): ")
    if not password:
        print("  empty password — nothing written.", file=sys.stderr)
        return 2

    url = build_url(user, password, args.host, args.db)

    print("\nchecking the connection BEFORE touching .env ...")
    ok, err = _connect(url)

    if not ok and args.create_db and "does not exist" in (err or ""):
        print(f"  database {args.db!r} does not exist; creating it ...")
        made, detail = _create_database(user, password, args.host, args.db)
        print(f"  {detail}")
        if made:
            ok, err = _connect(url)

    if not ok:
        print(f"  FAILED: {err}", file=sys.stderr)
        print("\n.env was NOT changed — your previous configuration is intact.", file=sys.stderr)
        if "does not exist" in (err or ""):
            print("  re-run with --create-db to create it.", file=sys.stderr)
        return 1

    print(f"  connected to {args.host}/{args.db}")
    if not rewrite_env(ENV_PATH, url):
        print(f"  no .env at {ENV_PATH}", file=sys.stderr)
        return 1

    print(f"\nDATABASE_URL updated -> {masked(url)}")
    print("  password percent-encoded:",
          "yes" if urlparse.quote(password, safe="") != password else "not needed")
    print("\nnext: python create_tables.py   (from the server/ directory, if the schema is new)")
    print("      then restart the backend.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
