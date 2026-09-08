"""Proves conftest.py's test-database guard: the pure decision function directly, and the
actual module wiring end-to-end via a subprocess (so a bug in how conftest calls
`_evaluate_guard` — not just a bug in the function itself — would also be caught).

Every subprocess case imports `conftest` in isolation and nothing else, so even the "refused"
and "allowed" cases never open a real database connection (app.db is not imported here).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import conftest as guard

SERVER_ROOT = Path(__file__).resolve().parent


# ── Pure decision function — every branch, no subprocess needed ──────────────────────────────

def test_production_environment_is_refused():
    decision = guard._evaluate_guard({
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pw@prod-host/prod_db",
    })
    assert decision.outcome == "refuse"
    assert "PRODUCTION" in decision.reason


def test_production_environment_case_and_alias_insensitive():
    for value in ("Production", " PROD ", "prod"):
        decision = guard._evaluate_guard({"ENVIRONMENT": value, "DATABASE_URL": "postgresql://h/db"})
        assert decision.outcome == "refuse"


def test_missing_test_database_url_is_refused_not_a_warning():
    """The exact scenario this repo is in: DATABASE_URL set, no ENVIRONMENT marker, no
    TEST_DATABASE_URL. Must refuse, not silently fall back to DATABASE_URL."""
    decision = guard._evaluate_guard({"DATABASE_URL": "postgresql://user:pw@live-host/live_db"})
    assert decision.outcome == "refuse"
    assert "TEST_DATABASE_URL is not set" in decision.reason


def test_missing_both_urls_is_allowed_and_inert():
    """Nothing to redirect and nothing database-specific to refuse; Settings() elsewhere is
    responsible for raising on a genuinely missing DATABASE_URL."""
    decision = guard._evaluate_guard({})
    assert decision.outcome == "allow"
    assert decision.database_url is None


def test_same_database_is_refused_even_with_test_database_url_set():
    same = "postgresql://roleA:pw@db.example.com:5432/appdb"
    same_other_role = "postgresql://roleB:pw2@db.example.com:5432/appdb?sslmode=require"
    decision = guard._evaluate_guard({"DATABASE_URL": same, "TEST_DATABASE_URL": same_other_role})
    assert decision.outcome == "refuse"
    assert "SAME database" in decision.reason


def test_same_database_detection_is_case_insensitive_on_host():
    decision = guard._evaluate_guard({
        "DATABASE_URL": "postgresql://u:p@DB.Example.COM/appdb",
        "TEST_DATABASE_URL": "postgresql://u:p@db.example.com/appdb",
    })
    assert decision.outcome == "refuse"


def test_distinct_test_database_is_allowed_and_redirects():
    decision = guard._evaluate_guard({
        "DATABASE_URL": "postgresql://u:p@live-host/live_db",
        "TEST_DATABASE_URL": "postgresql://u:p@live-host/live_db_test",
    })
    assert decision.outcome == "allow"
    assert decision.database_url == "postgresql://u:p@live-host/live_db_test"


def test_distinct_test_database_on_different_host_is_allowed():
    decision = guard._evaluate_guard({
        "DATABASE_URL": "postgresql://u:p@prod-host/appdb",
        "TEST_DATABASE_URL": "postgresql://u:p@ci-host/appdb",
    })
    assert decision.outcome == "allow"


def test_db_identity_ignores_credentials_and_query_string():
    a = guard._db_identity("postgresql://alice:secret1@host:5432/appdb?sslmode=require")
    b = guard._db_identity("postgresql://bob:secret2@host:5432/appdb")
    assert a == b


def test_db_identity_distinguishes_different_databases_on_same_host():
    a = guard._db_identity("postgresql://u:p@host/appdb")
    b = guard._db_identity("postgresql://u:p@host/appdb_test")
    assert a != b


# ── Subprocess-level proof that conftest.py's module scope actually acts on the decision ─────
#
# `python -c "import conftest"` from server/ triggers exactly the same module-level code path
# real pytest collection does, in a throwaway process, with a controlled environment — without
# ever starting pytest recursively.

def _run_conftest_import(env_overrides: dict, extra_code: str = "") -> subprocess.CompletedProcess:
    env = {"PATH": __import__("os").environ.get("PATH", ""), "SystemRoot": __import__("os").environ.get("SystemRoot", "")}
    env.update(env_overrides)
    code = "import conftest" + ("\n" + extra_code if extra_code else "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SERVER_ROOT), env=env, capture_output=True, text=True, timeout=30,
    )


def test_subprocess_refuses_production_environment():
    result = _run_conftest_import({
        "ENVIRONMENT": "production",
        "DATABASE_URL": "postgresql://user:pw@prod-host/prod_db",
    })
    assert result.returncode == 4, result.stderr
    assert "TEST RUN REFUSED" in result.stderr
    assert "PRODUCTION" in result.stderr


def test_subprocess_refuses_missing_test_database_url():
    """End-to-end reproduction of this repository's actual .env shape: a live DATABASE_URL,
    no ENVIRONMENT marker, no TEST_DATABASE_URL — must exit non-zero, not warn-and-continue."""
    result = _run_conftest_import({"DATABASE_URL": "postgresql://user:pw@live-host/live_db"})
    assert result.returncode == 4, result.stderr
    assert "TEST RUN REFUSED" in result.stderr
    assert "TEST_DATABASE_URL is not set" in result.stderr


def test_subprocess_allows_and_swaps_in_distinct_test_database():
    result = _run_conftest_import(
        {
            "DATABASE_URL": "postgresql://user:pw@live-host/live_db",
            "TEST_DATABASE_URL": "postgresql://user:pw@live-host/live_db_test",
        },
        extra_code="import os; print(os.environ['DATABASE_URL'])",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "postgresql://user:pw@live-host/live_db_test"


def test_subprocess_refuses_same_database_masquerading_as_test():
    result = _run_conftest_import({
        "DATABASE_URL": "postgresql://roleA:pw@live-host/live_db",
        "TEST_DATABASE_URL": "postgresql://roleB:pw2@live-host/live_db",
    })
    assert result.returncode == 4, result.stderr
    assert "SAME database" in result.stderr


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_environment_variables_do_not_satisfy_test_database_url(value):
    decision = guard._evaluate_guard({"DATABASE_URL": "postgresql://u:p@host/db", "TEST_DATABASE_URL": value})
    assert decision.outcome == "refuse"


# ══════════════════════════════════════════════════════════════════════════════════════
# The mapping the guard is GIVEN — regression for a hole that made all three checks
# unreachable in ordinary use.
#
# _evaluate_guard was correct; it was being handed os.environ, which on this project does NOT
# contain DATABASE_URL (it lives in .env and is read later by pydantic-settings). So every real
# invocation fell through to the "neither is set" branch and the suite ran against whatever
# .env named — including, at the time, a live Supabase instance.
# ══════════════════════════════════════════════════════════════════════════════════════

def _write_env(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


_PROD = "postgresql://u:p@aws-1-eu-west-2.pooler.supabase.com:6543/postgres"
_LOCAL = "postgresql://u:p@localhost:5432/zoikostream_local"


def test_a_database_url_that_exists_only_in_dotenv_is_seen(tmp_path):
    """THE regression. A bare `pytest` with DATABASE_URL only in .env must be REFUSED, not
    allowed to run against it."""
    env = _write_env(tmp_path, f"DATABASE_URL={_PROD}\n")
    d = guard._evaluate_guard(guard._effective_environ({}, env))
    assert d.outcome == "refuse"
    assert "TEST_DATABASE_URL is not set" in d.reason


def test_production_marker_in_dotenv_is_seen(tmp_path):
    env = _write_env(tmp_path, f"ENVIRONMENT=production\nDATABASE_URL={_PROD}\n")
    d = guard._evaluate_guard(guard._effective_environ({}, env))
    assert d.outcome == "refuse"
    assert "PRODUCTION" in d.reason


def test_same_database_is_detected_across_dotenv_and_environment(tmp_path):
    """The masquerade check must work when one side comes from the file and the other from the
    environment — the normal shape of a local run."""
    env = _write_env(tmp_path, f"DATABASE_URL={_LOCAL}\n")
    d = guard._evaluate_guard(
        guard._effective_environ({"TEST_DATABASE_URL": _LOCAL}, env))
    assert d.outcome == "refuse"
    assert "SAME database" in d.reason


def test_a_distinct_test_database_is_still_allowed_and_redirects(tmp_path):
    env = _write_env(tmp_path, f"DATABASE_URL={_PROD}\n")
    d = guard._evaluate_guard(
        guard._effective_environ({"TEST_DATABASE_URL": _LOCAL}, env))
    assert d.outcome == "allow"
    assert d.database_url == _LOCAL


def test_a_real_environment_variable_wins_over_the_file(tmp_path):
    """Precedence must match pydantic-settings, or the guard would inspect one database while
    Settings() opened another."""
    env = _write_env(tmp_path, f"DATABASE_URL={_PROD}\n")
    merged = guard._effective_environ({"DATABASE_URL": _LOCAL}, env)
    assert merged["DATABASE_URL"] == _LOCAL


def test_an_empty_environment_variable_does_not_mask_the_file(tmp_path):
    """`DATABASE_URL=` exported as empty must not read as "no database configured" — that would
    hand the guard a blind spot in the one direction that matters."""
    env = _write_env(tmp_path, f"DATABASE_URL={_PROD}\n")
    merged = guard._effective_environ({"DATABASE_URL": ""}, env)
    assert merged["DATABASE_URL"] == _PROD


def test_a_missing_env_file_is_not_an_error(tmp_path):
    d = guard._evaluate_guard(
        guard._effective_environ({}, tmp_path / "does-not-exist"))
    assert d.outcome == "allow"


def test_comments_exports_and_quotes_are_parsed(tmp_path):
    env = _write_env(tmp_path, "\n".join([
        "# a comment",
        "",
        f'export DATABASE_URL="{_PROD}"',
        f"OTHER='{_LOCAL}'",
        "MALFORMED_NO_EQUALS",
    ]) + "\n")
    vals = guard._dotenv_values(env)
    assert vals["DATABASE_URL"] == _PROD
    assert vals["OTHER"] == _LOCAL
    assert "MALFORMED_NO_EQUALS" not in vals


def test_a_commented_out_database_url_is_not_read(tmp_path):
    """Exactly the shape of this repo's .env after the production line was commented out — the
    commented value must not be resurrected as if it were live."""
    env = _write_env(tmp_path, f"#DATABASE_URL={_PROD}\nDATABASE_URL={_LOCAL}\n")
    assert guard._dotenv_values(env)["DATABASE_URL"] == _LOCAL


def test_the_module_hands_the_merged_mapping_to_the_decision():
    """Structural: the wiring is the whole fix, so pin it. If someone reverts this to
    os.environ the guard silently goes blind again and every other test here still passes."""
    from _testsupport import code_only
    src = code_only(guard)
    assert "_evaluate_guard(_effective_environ(os.environ))" in src
