"""Pytest session guards. THE ONLY THING THIS FILE DOES IS STOP THE SUITE HURTING PRODUCTION.

Why it exists: the suites import `from app.db import engine` and write real rows — there is no
transactional-rollback fixture and no separate test database. That is fine against a dedicated
test database and catastrophic against a shared/live one, and nothing stands between the two but
this guard — there is deliberately no "just warn and continue" path, because a warning that
scrolls past in CI output is not a protection anyone can rely on.

Three checks, in order, each a HARD REFUSAL (`SystemExit(4)` before any test can open a
connection):

  1. ENVIRONMENT=production. A production environment file carries the production DATABASE_URL
     beside it, so refusing on the environment marker blocks the realistic accident of running
     the suite on a box configured for prod.

  2. TEST_DATABASE_URL and DATABASE_URL resolving to the SAME database. Guards the accident one
     level closer than (1): someone sets TEST_DATABASE_URL believing it's isolated when it's
     actually a copy-paste of DATABASE_URL (or the same instance under a different role).

  3. TEST_DATABASE_URL missing entirely. This repository's own `.env` sets DATABASE_URL to a
     live Supabase instance and sets no ENVIRONMENT marker at all — so (1) alone does not catch
     it. Without an explicit, distinct TEST_DATABASE_URL there is no supported way to run this
     suite; it no longer falls back to DATABASE_URL under a warning.

TEST_DATABASE_URL, once accepted, REPLACES DATABASE_URL for the whole session. This must happen
before any test module imports `app.db`, which is why it lives at module scope in conftest
rather than in a fixture — `Settings()` reads the environment once, at import.

Deliberately NOT done here: pattern-matching either URL for "prod"/"test" substrings. A
heuristic that guesses which database is which would either block legitimate runs or wave
through a production URL that happens not to say so, and a guard nobody can trust gets disabled.

The decision logic (`_evaluate_guard`) is a pure function of an environment mapping — no I/O, no
process exit — specifically so `test_conftest_guard.py` can exercise every branch without
needing a real database or a subprocess for most cases. The file read it needs lives OUTSIDE it,
in `_effective_environ`, so that purity survives.

WHICH MAPPING it is given matters as much as the logic. It is NOT `os.environ`: this project
keeps DATABASE_URL in `.env`, which pydantic-settings reads later inside Settings(), so
`os.environ` does not contain it and all three checks above were unreachable in ordinary use —
the decision fell through to "neither is set, nothing to refuse" and the suite ran against
whatever `.env` named. `_effective_environ` merges the file under the real environment (env wins,
matching pydantic-settings) so the guard inspects the same values Settings() will.
"""

import os
import sys
from collections import namedtuple
from pathlib import Path
from urllib.parse import urlsplit

# The same single .env app.config reads (server/conftest.py -> repo root is one level up).
ROOT_ENV = Path(__file__).resolve().parents[1] / ".env"

# Values of ENVIRONMENT that mean a real deployment. Duplicated from app.config rather than
# imported: importing app.config here would construct Settings() (and therefore read
# DATABASE_URL) BEFORE the TEST_DATABASE_URL swap below could take effect.
_PRODUCTION_ENVIRONMENTS = ("production", "prod")

# outcome: "allow" (nothing to do, or `database_url` should replace os.environ["DATABASE_URL"])
#        | "refuse" (reason/remedy are set; caller must abort before any test runs)
GuardDecision = namedtuple("GuardDecision", "outcome reason remedy database_url")


def _dotenv_values(path) -> dict:
    """KEY=VALUE pairs from a .env file, or {} if it is absent or unreadable.

    Deliberately minimal — no interpolation, no multi-line values, no `python-dotenv`
    dependency. It exists only to let the guard SEE the same DATABASE_URL that Settings() will
    later read; anything it fails to parse simply falls back to "not set", which makes the
    guard MORE cautious, never less.
    """
    values = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _effective_environ(environ=None, env_file=ROOT_ENV) -> dict:
    """What `Settings()` will ACTUALLY see: the .env file overlaid by the real environment.

    This closes the hole that made every check below unreachable in normal use. The guard read
    `os.environ` alone, but this project keeps DATABASE_URL in `.env` — which pydantic-settings
    reads later, inside Settings(). So `DATABASE_URL` was absent from the mapping the guard
    inspected, the decision fell through to the "neither is set, nothing to refuse" branch, and
    a bare `pytest` ran against whatever `.env` named. While `.env` pointed at the live Supabase
    instance that meant the suite would have written real commercial rows into PRODUCTION —
    precisely the accident check (3) is described as preventing, and precisely what it could not
    see. Only invocations that happened to export TEST_DATABASE_URL were ever protected.

    Precedence matches pydantic-settings exactly: a real environment variable WINS over the
    file, so exporting TEST_DATABASE_URL still works and the redirect below still takes effect.
    """
    environ = os.environ if environ is None else environ
    merged = _dotenv_values(env_file)
    # Only non-empty environment values override the file; an empty string means "unset" here
    # and must not mask a real .env value.
    for key, value in environ.items():
        if value:
            merged[key] = value
    return merged


def _db_identity(url: str) -> tuple:
    """(host, port, path) for a database URL, ignoring credentials and query string — so a
    different role/password on the SAME database still counts as a match. An unparsable URL
    identifies as itself so it can never spuriously equal a different unparsable URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return (url, None, None)
    return ((parts.hostname or "").lower(), parts.port, parts.path.lstrip("/"))


def _evaluate_guard(environ) -> "GuardDecision":
    """Pure decision function over an environment mapping (typically `os.environ`, or a plain
    dict in a test). Never raises, never exits, never touches a socket — module scope below is
    the only place a decision here turns into a refusal or an os.environ mutation."""
    environment = (environ.get("ENVIRONMENT") or "").strip().lower()
    if environment in _PRODUCTION_ENVIRONMENTS:
        return GuardDecision(
            "refuse",
            f"ENVIRONMENT={environment!r} — this process is configured as a PRODUCTION "
            "deployment.\nThe test suite writes real commercial rows (orders, payments, "
            "invoices, capacity\nreservations) to whatever DATABASE_URL points at.",
            "Run the suite with a development/test environment instead:\n"
            "    ENVIRONMENT=test TEST_DATABASE_URL=postgresql://.../zoikostream_test pytest",
            None,
        )

    database_url = (environ.get("DATABASE_URL") or "").strip()
    test_database_url = (environ.get("TEST_DATABASE_URL") or "").strip()

    if test_database_url and database_url and _db_identity(test_database_url) == _db_identity(database_url):
        host, _port, path = _db_identity(test_database_url)
        return GuardDecision(
            "refuse",
            f"TEST_DATABASE_URL resolves to the SAME database as DATABASE_URL ({host}/{path}).\n"
            "A test database that is the live database provides no isolation at all.",
            "Point TEST_DATABASE_URL at a genuinely distinct database — e.g. a same-host "
            "sibling database created for tests only.",
            None,
        )

    if test_database_url:
        return GuardDecision("allow", None, None, test_database_url)

    if database_url:
        return GuardDecision(
            "refuse",
            "TEST_DATABASE_URL is not set. Without it this process would read and WRITE "
            "whatever\ndatabase DATABASE_URL points at.",
            "Set TEST_DATABASE_URL to a dedicated test database before running the suite:\n"
            "    TEST_DATABASE_URL=postgresql://.../zoikostream_test pytest\n"
            "There is no supported way to run this suite against DATABASE_URL directly.",
            None,
        )

    # Neither is set — nothing to redirect and nothing to refuse on database-safety grounds;
    # `Settings()` will raise its own error for a missing DATABASE_URL when something needs it.
    return GuardDecision("allow", None, None, None)


def _refuse(reason: str, remedy: str) -> None:
    """Abort collection before a single test can touch the database."""
    sys.stderr.write(
        "\n"
        "=========================== TEST RUN REFUSED ===========================\n"
        f"{reason}\n\n"
        f"{remedy}\n"
        "========================================================================\n\n"
    )
    raise SystemExit(4)


_decision = _evaluate_guard(_effective_environ(os.environ))
if _decision.outcome == "refuse":
    _refuse(_decision.reason, _decision.remedy)
elif _decision.database_url:
    os.environ["DATABASE_URL"] = _decision.database_url


# ── No real outbound email during the suite ─────────────────────────────────────────────────
#
# app/email.py::_send posts to the Resend API whenever RESEND_API_KEY is set, and this
# repository's .env sets a real one — so a full run genuinely posted to
# https://api.resend.com/emails and logged 422s back from the provider. Tests must not make
# production API calls.
#
# Why patch the transport rather than blank RESEND_API_KEY: _send returns EARLY when the key is
# unset, before it ever reaches httpx.post. Several suites legitimately assert that a send was
# ATTEMPTED by patching that same call and inspecting the payload. Blanking the key would make
# those assertions vacuous — weakening real coverage to buy network silence. Swapping the
# transport keeps every one of those assertions meaningful while guaranteeing no packet leaves
# the machine.
#
# Session-scoped and autouse, restored afterwards. Per-test `patch.object(email_mod.httpx,
# "post", ...)` still wins for that test and restores THIS stub on exit, so ordering cannot
# reopen the network. Production behaviour is untouched.
import pytest  # noqa: E402  (deliberately after the env guard above)


class _StubMailResponse:
    """Mimics the httpx.Response surface app/email.py::_send actually uses."""

    status_code = 200
    text = '{"id": "test-stub-no-network"}'

    def raise_for_status(self):
        return None


@pytest.fixture(autouse=True, scope="session")
def _no_real_outbound_email():
    import app.email as email_mod

    original_post = email_mod.httpx.post
    sent: list[dict] = []

    def _stub_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "payload": json or {}})
        return _StubMailResponse()

    email_mod.httpx.post = _stub_post
    try:
        yield sent
    finally:
        email_mod.httpx.post = original_post
