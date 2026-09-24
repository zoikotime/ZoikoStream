"""The second way a test suite could reach a real database, and the interlock that closes it.

── THE HOLE ────────────────────────────────────────────────────────────────────────────
conftest.py refuses a `pytest` run with no TEST_DATABASE_URL. That guard is sound and
test_conftest_guard.py covers it. But conftest is a PYTEST mechanism, and 57 of the 97 suites
here carry an `if __name__ == "__main__"` runner so they can be executed directly:

    python test_event_assignment.py

That never loads conftest. The process took DATABASE_URL from .env like any other — which on
a developer machine is zoiko_stream_dev and on a box configured for production would be
production. It is how 1,124 fixture accounts, 473 fixture organizations and 42,204 analytics
rows ended up in the development database.

app/testguard.py closes it at app/db.py, the one import every suite must pass through. These
pin the decision function directly (it is pure, taking the entry name and the environment)
and then pin the real behaviour through a subprocess, because "the function returns True" and
"the process actually refuses" are different claims and only the second one matters.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import testguard

SERVER = Path(__file__).resolve().parent
DEV_URL = "postgresql://postgres:pw@localhost:5433/zoiko_stream_dev"
PROD_URL = "postgresql://u:p@aws-1-eu-west-2.pooler.supabase.com:6543/postgres"


# ── the decision ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", [
    "test_event_assignment.py", "test_broadcast.py", "test_ops.py",
])
def test_a_test_module_run_as_a_script_is_recognised(entry, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert testguard.is_direct_test_run(entry) is True


@pytest.mark.parametrize("entry", [
    "uvicorn", "seed.py", "create_tables.py", "clean_dev_fixtures.py",
    "manage.py", "", "migrate_plan_names.py",
])
def test_ordinary_programs_are_untouched(entry, monkeypatch):
    """The interlock must be invisible to everything that is not a test module. Application
    startup, the seeder, the migrations and the cleanup script all reach app/db.py too."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert testguard.is_direct_test_run(entry) is False


def test_under_pytest_the_guard_defers_to_conftest(monkeypatch):
    """PYTEST_CURRENT_TEST means conftest already decided. Two guards disagreeing about the
    same process would be worse than one."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "something::test_x (call)")
    assert testguard.is_direct_test_run("test_event_assignment.py") is False


# ── the behaviour, in a real process ───────────────────────────────────────────────────

def _run(code: str, env_extra: dict) -> subprocess.CompletedProcess:
    """Execute `code` in a child whose argv[0] looks like a test module.

    The parent's environment is inherited so the interpreter can still find its packages —
    scrubbing it is what makes test_conftest_guard's own subprocess case fail on this machine
    with a missing `pluggy`. Only the variables under test are overridden.
    """
    env = dict(os.environ)
    env.pop("TEST_DATABASE_URL", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.update(env_extra)
    script = SERVER / "test_zz_guard_probe.py"
    script.write_text(code, encoding="utf-8")
    try:
        return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              cwd=str(SERVER), env=env, timeout=120)
    finally:
        script.unlink(missing_ok=True)


PROBE = (
    "import urllib.parse as up\n"
    "import app.db\n"
    "print('RESOLVED=' + up.urlsplit(str(app.db.engine.url)).path)\n"
)


def test_a_direct_run_without_a_test_database_is_refused():
    """The headline. Importing app.db from a test_*.py script with no TEST_DATABASE_URL must
    stop the process before an engine exists, not warn and continue."""
    r = _run(PROBE, {"DATABASE_URL": DEV_URL})

    assert r.returncode == 4, f"expected a hard refusal, got {r.returncode}: {r.stdout}{r.stderr}"
    assert "TEST RUN REFUSED" in r.stderr
    assert "RESOLVED=" not in r.stdout, "the engine must never be built"


def test_a_direct_run_cannot_reach_the_development_database():
    r = _run(PROBE, {"DATABASE_URL": DEV_URL})
    assert "zoiko_stream_dev" not in r.stdout


def test_a_direct_run_cannot_reach_a_production_database():
    """Same refusal, and for the more important case. conftest's ENVIRONMENT=production check
    protects `pytest` only — a direct run never reached it."""
    r = _run(PROBE, {"DATABASE_URL": PROD_URL})

    assert r.returncode == 4
    assert "supabase" not in r.stdout
    assert "RESOLVED=" not in r.stdout


def test_a_direct_run_with_a_test_database_is_allowed_and_uses_it():
    """Not a blanket ban: a suite run directly against a real test database is a supported
    workflow, and CI relies on it for the 12 suites pytest cannot collect."""
    # From the environment only. A fallback URL here meant a developer's own password lived
    # in the repository, and it would also have let this test quietly pass against a database
    # nobody chose. conftest refuses the whole session without TEST_DATABASE_URL, so under
    # pytest this is always set; the skip covers a direct run of this file.
    test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is not set; nothing to point the child process at")

    r = _run(PROBE, {"DATABASE_URL": DEV_URL, "TEST_DATABASE_URL": test_url})

    assert r.returncode == 0, r.stderr
    assert "RESOLVED=" in r.stdout
    resolved = r.stdout.split("RESOLVED=")[1].strip()
    assert "zoiko_stream_dev" not in resolved
    assert resolved.lstrip("/") == test_url.rsplit("/", 1)[-1]


# ── the cleanup script has its own refusal ─────────────────────────────────────────────

@pytest.mark.parametrize("url,why", [
    ("postgresql://postgres:pw@localhost:5433/zoiko_stream_test", "wrong database"),
    ("postgresql://u:p@aws-1-eu-west-2.pooler.supabase.com:6543/postgres", "production host"),
    ("postgresql://u:p@db.internal:5432/zoiko_stream_dev", "non-local host"),
])
def test_the_dev_cleanup_script_refuses_anything_but_local_dev(url, why):
    """clean_dev_fixtures.py deletes rows. Pointing it somewhere by editing an environment
    variable is exactly the accident worth preventing, so its target is named in code and
    everything else is refused."""
    r = subprocess.run(
        [sys.executable, str(SERVER / "clean_dev_fixtures.py"), "--url", url],
        capture_output=True, text=True, cwd=str(SERVER), timeout=120,
    )
    assert r.returncode == 4, why
    assert "REFUSED" in r.stderr
