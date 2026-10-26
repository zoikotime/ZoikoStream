"""Schema before code on every repository-controlled production deploy path.

A release that adds columns and writes them at once (models/audit_log.py: event_id, outcome,
actor_role on every audited action) must never start against a database without them. The two
deploy paths kept in this repository are read here as data:

  * .github/workflows/deployVM.yml — a `migrate` job (server/create_tables.py from the pushed
    commit) that the `deploy` job needs, failing closed when the database secret is absent.
  * cloudbuild.yaml — the existing `migrate` step that `deploy` waits for.

and create_tables' own verification runs against the local test database, read-only.

NOT covered, because it is not in this repository: /opt/zoikostream/deploy.sh on the VM. What it
deploys and whether it migrates again is verified on the VM, not here.
"""
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Column, Integer, MetaData, String, Table

import create_tables

SERVER = Path(__file__).resolve().parent
ROOT = SERVER.parent


def _yaml(rel: str) -> dict:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vm():
    return _yaml(".github/workflows/deployVM.yml")


def _steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def _run_index(steps: list[dict], needle: str) -> int:
    return next(i for i, s in enumerate(steps) if needle in (s.get("run") or ""))


# ── the VM workflow ──────────────────────────────────────────────────────────────────────

def test_the_vm_deploy_only_runs_after_the_schema_job(vm):
    jobs = vm["jobs"]
    assert "migrate" in jobs, "the VM path must apply the schema itself"
    needs = jobs["deploy"].get("needs")
    assert needs == "migrate" or "migrate" in (needs or []), "deploy must need migrate"
    # Any `if:` on deploy (always(), failure(), !cancelled()) could run it after a failed migrate.
    assert "if" not in jobs["deploy"], "deploy must not override the dependency with a condition"


def test_the_schema_job_runs_create_tables_from_the_pushed_commit(vm):
    steps = _steps(vm["jobs"]["migrate"])
    assert any(str(s.get("uses", "")).startswith("actions/checkout@") for s in steps)
    step = steps[_run_index(steps, "create_tables.py")]
    assert step["run"].strip() == "python create_tables.py", "apply AND verify: the plain entrypoint"
    assert step.get("working-directory") == "server"
    assert step["env"]["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"


def test_the_schema_job_matches_the_production_image(vm):
    steps = _steps(vm["jobs"]["migrate"])
    py = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python@"))
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM python:{py['with']['python-version']}-slim" in dockerfile
    install = steps[_run_index(steps, "pip install")]
    assert install["run"].strip() == "pip install -r requirements.txt"
    assert "working-directory" not in install, "the repo-root requirements.txt, as the image installs"
    assert "COPY requirements.txt ./" in dockerfile


def test_a_missing_database_secret_stops_the_deploy_before_anything_runs(vm):
    steps = _steps(vm["jobs"]["migrate"])
    guard = _run_index(steps, '-z "${DATABASE_URL}"')
    assert "exit 1" in steps[guard]["run"]
    assert steps[guard]["env"]["DATABASE_URL"] == "${{ secrets.DATABASE_URL }}"
    assert guard < _run_index(steps, "create_tables.py")


def test_nothing_in_the_workflow_can_swallow_a_failure(vm):
    for name, job in vm["jobs"].items():
        assert "continue-on-error" not in job, name
        for step in _steps(job):
            assert "continue-on-error" not in step, (name, step.get("name"))
            assert "|| true" not in (step.get("run") or ""), (name, step.get("name"))


def test_production_deploys_are_serialised_and_never_cancelled_midway(vm):
    assert vm["concurrency"]["group"]
    assert vm["concurrency"]["cancel-in-progress"] is False


def test_the_vm_deploy_step_itself_is_unchanged(vm):
    (step,) = _steps(vm["jobs"]["deploy"])
    assert step["uses"] == "appleboy/ssh-action@v1"
    assert step["with"] == {
        "host": "${{ secrets.DEPLOY_HOST }}",
        "username": "${{ secrets.DEPLOY_USER }}",
        "key": "${{ secrets.DEPLOY_SSH_KEY }}",
        "script": "/opt/zoikostream/deploy.sh",
    }


def test_the_workflow_still_deploys_only_from_main(vm):
    trigger = vm.get("on", vm.get(True))          # PyYAML reads a bare `on:` key as True
    assert trigger == {"push": {"branches": ["main"]}}


# ── the Cloud Build path ─────────────────────────────────────────────────────────────────

def test_cloud_build_still_migrates_before_it_deploys():
    steps = {s["id"]: s for s in _yaml("cloudbuild.yaml")["steps"]}
    script = " ".join(steps["migrate"]["args"])
    assert "set -euo pipefail" in script and "python create_tables.py" in script
    assert "migrate" in steps["deploy"]["waitFor"]


# ── create_tables: verification ──────────────────────────────────────────────────────────

def test_the_local_test_schema_is_complete_for_this_code():
    assert create_tables.missing_schema() == []
    create_tables.verify_schema()                 # does not raise


def test_verification_names_each_missing_table_and_column():
    md = MetaData()
    Table("audit_logs", md, Column("id", Integer, primary_key=True), Column("zk_not_a_column", String))
    Table("zk_not_a_table", md, Column("id", Integer, primary_key=True))
    assert create_tables.missing_schema(metadata=md) == ["audit_logs.zk_not_a_column", "zk_not_a_table"]
    with pytest.raises(RuntimeError, match="NOT ready for this code"):
        create_tables.verify_schema(metadata=md)


def test_a_failed_verification_stops_the_run_before_the_guide_step(monkeypatch):
    calls = []
    monkeypatch.setattr(create_tables, "ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(create_tables, "missing_schema", lambda *a, **k: ["audit_logs.event_id"])
    monkeypatch.setattr(create_tables, "ensure_chatbot_schemas", lambda: calls.append("guide"))
    with pytest.raises(RuntimeError):
        create_tables.main([])
    assert calls == ["schema"], "nothing after a failed verification may run"


def test_a_failed_schema_step_is_never_followed_by_verification_or_the_guide(monkeypatch):
    calls = []

    def boom():
        raise RuntimeError("1 schema statement(s) failed")

    monkeypatch.setattr(create_tables, "ensure_schema", boom)
    monkeypatch.setattr(create_tables, "verify_schema", lambda *a, **k: calls.append("verify"))
    monkeypatch.setattr(create_tables, "ensure_chatbot_schemas", lambda: calls.append("guide"))
    with pytest.raises(RuntimeError):
        create_tables.main([])
    assert calls == []


def test_check_mode_only_reads(monkeypatch):
    def must_not_run():
        raise AssertionError("--check must not apply the schema")

    monkeypatch.setattr(create_tables, "ensure_schema", must_not_run)
    monkeypatch.setattr(create_tables, "ensure_chatbot_schemas", must_not_run)
    assert create_tables.main(["--check"]) == 0


def test_the_command_line_exit_codes():
    """The entrypoint a deploy step runs: --check passes (0) on the complete local test schema.
    (conftest has already pointed DATABASE_URL at the local test database for this process.)"""
    ok = subprocess.run([sys.executable, "create_tables.py", "--check"], cwd=SERVER,
                        capture_output=True, text=True, timeout=300)
    assert ok.returncode == 0, ok.stdout[-2000:] + ok.stderr[-2000:]
    assert "Schema verified" in ok.stdout
