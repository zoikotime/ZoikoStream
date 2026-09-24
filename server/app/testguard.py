"""Stop a test file writing to the development (or production) database.

conftest.py already refuses a `pytest` run with no TEST_DATABASE_URL, and that guard is
sound. It is also a pytest mechanism: `python test_event_assignment.py` never loads it, and
57 of the 97 suites in this directory carry an `if __name__ == "__main__"` runner precisely
so they can be run that way. Those runs took DATABASE_URL from .env like any other process —
which on a developer machine is zoiko_stream_dev, and on a box configured for production
would be production.

That is how 1,124 fixture accounts, 473 fixture organizations and 42,204 analytics rows ended
up in zoiko_stream_dev. The pytest hole is closed; this closes the other one.

It is called from app/db.py, which is a real cost and worth naming: application code now
knows the test suite exists. The alternative was an import line in 57 files that a 58th would
forget. app/db.py is the single choke point every suite must pass through — there is no way
to reach a Session without importing it — so one interlock that cannot be bypassed beats a
convention that can.

The check is narrow on purpose. It fires only when the ENTRY SCRIPT is named test_*.py, so
nothing about ordinary application startup, a migration, create_tables.py or seed.py changes.
"""
import os
import sys

BANNER = [
    "=========================== TEST RUN REFUSED ===========================",
    "{entry} was run directly, so conftest.py's database guard never loaded.",
    "Without TEST_DATABASE_URL this process would read and WRITE whatever",
    "database DATABASE_URL names - the development or the production one.",
    "",
    "Set a dedicated test database and try again:",
    "    TEST_DATABASE_URL=postgresql://.../zoikostream_test python {entry}",
    "========================================================================",
]


def entry_script() -> str:
    return os.path.basename(sys.argv[0] or "")


def is_direct_test_run(entry: str | None = None) -> bool:
    """A test module executed as a script, outside pytest.

    PYTEST_CURRENT_TEST is set by pytest for the duration of a test, so its presence means
    conftest's guard has already run and this one must not second-guess it.
    """
    entry = entry_script() if entry is None else entry
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return entry.startswith("test_") and entry.endswith(".py")


def resolve(settings) -> None:
    """Swap in TEST_DATABASE_URL for a direct test run, or refuse the run.

    The swap mirrors what conftest does and for the same reason: Settings() has already read
    the environment by the time app/db.py imports, so the engine is built from the value set
    here rather than from .env.
    """
    if not is_direct_test_run():
        return
    entry = entry_script()
    test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if test_url:
        settings.DATABASE_URL = test_url
        return
    message = "\n".join(line.format(entry=entry) for line in BANNER)
    sys.stderr.write("\n" + message + "\n\n")
    raise SystemExit(4)
