"""Pytest session guards. THE ONLY THING THIS FILE DOES IS STOP THE SUITE HURTING PRODUCTION.

Why it exists: the suites import `from app.db import engine` and write real rows — there is no
transactional-rollback fixture and no separate test database. That is fine against a dev
database and catastrophic against a production one, and until now nothing stood between the two
but the operator's memory of which `.env` was loaded.

Two protections, in order:

  1. ENVIRONMENT=production is a HARD REFUSAL. A production environment file carries the
     production DATABASE_URL beside it, so refusing on the environment marker is what actually
     blocks the realistic accident (running the suite on a box configured for prod).

  2. TEST_DATABASE_URL, when set, REPLACES DATABASE_URL for the whole session. This must happen
     before any test module imports `app.db`, which is why it lives at module scope in conftest
     rather than in a fixture — `Settings()` reads the environment once, at import.

Deliberately NOT done here: pattern-matching the URL for "prod"/"test" substrings. A heuristic
that guesses which database is which would either block legitimate runs or wave through a
production URL that happens not to say so, and a guard nobody can trust gets disabled.
"""

import os
import sys

# Values of ENVIRONMENT that mean a real deployment. Duplicated from app.config rather than
# imported: importing app.config here would construct Settings() (and therefore read
# DATABASE_URL) BEFORE the TEST_DATABASE_URL swap below could take effect.
_PRODUCTION_ENVIRONMENTS = ("production", "prod")


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


_environment = os.environ.get("ENVIRONMENT", "").strip().lower()
if _environment in _PRODUCTION_ENVIRONMENTS:
    _refuse(
        f"ENVIRONMENT={_environment!r} — this process is configured as a PRODUCTION "
        "deployment.\nThe test suite writes real commercial rows (orders, payments, invoices, "
        "capacity\nreservations) to whatever DATABASE_URL points at.",
        "Run the suite with a development/test environment instead:\n"
        "    ENVIRONMENT=test TEST_DATABASE_URL=postgresql://.../zoikostream_test pytest",
    )

# Redirect to a dedicated test database when one is configured. This is the supported path for
# CI and the only way to get true isolation; without it the suite shares the configured
# database and leaves residue behind (fixtures clean up by id, but a test that errors mid-run
# cannot).
_test_db = os.environ.get("TEST_DATABASE_URL", "").strip()
if _test_db:
    os.environ["DATABASE_URL"] = _test_db
elif os.environ.get("DATABASE_URL", "").strip():
    sys.stderr.write(
        "\n[conftest] TEST_DATABASE_URL is not set — the suite will read and WRITE the "
        "database in DATABASE_URL.\n"
        "           Safe against a dev database; set TEST_DATABASE_URL for isolation in CI.\n\n"
    )
