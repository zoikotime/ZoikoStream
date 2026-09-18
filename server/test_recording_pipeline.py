"""Recording pipeline: credential validation, egress lifecycle, and stuck-row recovery.

── WHAT THE AUDIT FOUND ────────────────────────────────────────────────────────────────
This deployment's GCS_CREDENTIALS_PATH points at a Google Cloud Console *web page* instead
of a mounted service-account key, so gcs_configured() is False and every egress start would
be unenforced. That is an operator fix (mount the key from Secret Manager); what is tested
here is that the code REFUSES it clearly rather than half-working, plus the lifecycle gaps
the audit named: egress_updated was unhandled, and a lost egress_ended left a row stuck at
status="recording" forever.

No credential material is constructed, printed, or asserted on beyond structural fields.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services import broadcast, livekit


@pytest.fixture(autouse=True)
def fresh_credential_cache():
    """_gcs_credentials_json is @lru_cache'd, so it answers from the FIRST call for the life
    of the process and would ignore the settings patched below. Worth knowing beyond the
    tests: changing GCS_CREDENTIALS_PATH in the environment needs a process restart to take
    effect."""
    livekit._gcs_credentials_json.cache_clear()
    yield
    livekit._gcs_credentials_json.cache_clear()


# ── credential validation ──────────────────────────────────────────────────────────────

def test_a_browser_url_is_refused_as_a_credential_path(tmp_path):
    """The exact misconfiguration in this environment."""
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH",
                      "https://console.cloud.google.com/security/secret-manager"):
        assert livekit._gcs_credentials_json() is None
        assert livekit.gcs_configured() is False
        err = livekit.gcs_config_error()
    # Asserting on what gcs_config_error() RETURNS (operator-facing), which words this
    # differently from the log line ("browser URL") for the same condition.
    assert err and "is a URL" in err
    # The remedy is named, so an operator is not left guessing.
    assert "service-account" in err and "path" in err.lower()


def test_a_missing_file_is_refused(tmp_path):
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH", str(tmp_path / "absent.json")):
        assert livekit._gcs_credentials_json() is None
        assert livekit.gcs_configured() is False
        assert livekit.gcs_config_error() is not None


def test_malformed_json_is_refused(tmp_path):
    bad = tmp_path / "key.json"
    bad.write_text("{not json at all", encoding="utf-8")
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH", str(bad)):
        assert livekit._gcs_credentials_json() is None
        assert "not valid JSON" in (livekit.gcs_config_error() or "")


def test_valid_json_that_is_not_a_service_account_is_refused(tmp_path):
    """An ADC user credential parses fine and then fails deep inside egress — caught here."""
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "authorized_user", "client_id": "x"}), encoding="utf-8")
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH", str(key)):
        assert livekit._gcs_credentials_json() is None
        assert "service-account" in (livekit.gcs_config_error() or "")


def test_a_structurally_valid_service_account_is_accepted(tmp_path):
    """Structure only — no real key material anywhere in this suite."""
    key = tmp_path / "key.json"
    key.write_text(json.dumps({
        "type": "service_account",
        "client_email": "recorder@example.iam.gserviceaccount.com",
        "project_id": "example",
    }), encoding="utf-8")
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH", str(key)):
        assert livekit._gcs_credentials_json() is not None
        assert livekit.gcs_configured() is True
        assert livekit.gcs_config_error() is None


def test_a_bucket_without_credentials_is_not_configured():
    with patch.object(livekit.settings, "GCS_BUCKET", "some-bucket"), \
         patch.object(livekit.settings, "GCS_CREDENTIALS_PATH", ""):
        assert livekit.gcs_configured() is False


# ── egress status naming ───────────────────────────────────────────────────────────────

class FakeEnum:
    def __init__(self, name):
        self.name = name


class FakeEgress:
    def __init__(self, status, egress_id="EG_1"):
        self.status = status
        self.egress_id = egress_id


def test_status_is_read_by_name_however_the_sdk_hands_it_over():
    assert broadcast.egress_status_name(FakeEgress(FakeEnum("EGRESS_ACTIVE"))) == "EGRESS_ACTIVE"
    assert broadcast.egress_status_name(FakeEgress("EGRESS_COMPLETE")) == "EGRESS_COMPLETE"


# ── stuck-recording reconciliation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_still_running_egress_is_left_alone():
    """A recording that is genuinely still going must never be touched."""
    finaliser = AsyncMock()
    with patch.object(livekit, "get_egress", AsyncMock(return_value=FakeEgress(FakeEnum("EGRESS_ACTIVE")))), \
         patch.object(broadcast, "record_egress_result", finaliser):
        assert await broadcast.reconcile_stuck_recording("r1", "EG_1") == "active"
    finaliser.assert_not_called()


@pytest.mark.asyncio
async def test_an_unreachable_livekit_leaves_the_row_alone():
    """get_egress returns None for "could not determine" — unconfigured, aged out, or a
    failed call. Marking a row failed on that would be a guess."""
    finaliser = AsyncMock()
    with patch.object(livekit, "get_egress", AsyncMock(return_value=None)), \
         patch.object(broadcast, "record_egress_result", finaliser):
        assert await broadcast.reconcile_stuck_recording("r1", "EG_1") == "unknown"
    finaliser.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["EGRESS_COMPLETE", "EGRESS_FAILED", "EGRESS_ABORTED",
                                    "EGRESS_LIMIT_REACHED"])
async def test_a_terminal_egress_is_finalised_through_the_webhook_path(status):
    """Reconciliation reuses record_egress_result — the SAME writer the webhook uses — so a
    recovered row is indistinguishable from one finalised normally."""
    called = {}

    def fake_record(info):
        called["info"] = info
        return {"ok": True}

    with patch.object(livekit, "get_egress", AsyncMock(return_value=FakeEgress(FakeEnum(status)))), \
         patch.object(broadcast, "record_egress_result", fake_record):
        assert await broadcast.reconcile_stuck_recording("r1", "EG_1") == "finalized"

    assert broadcast.egress_status_name(called["info"]) == status


@pytest.mark.asyncio
async def test_an_unrecognised_status_is_not_treated_as_completion():
    """A status this code has never seen is UNKNOWN, not done."""
    finaliser = AsyncMock()
    with patch.object(livekit, "get_egress", AsyncMock(return_value=FakeEgress(FakeEnum("EGRESS_SOMETHING_NEW")))), \
         patch.object(broadcast, "record_egress_result", finaliser):
        assert await broadcast.reconcile_stuck_recording("r1", "EG_1") == "unknown"
    finaliser.assert_not_called()


def test_the_terminal_and_active_sets_do_not_overlap():
    assert not (broadcast._EGRESS_TERMINAL & broadcast._EGRESS_ACTIVE)


# ── size_bytes must survive a large recording ──────────────────────────────────────────

def test_size_bytes_is_a_64_bit_column():
    """PostgreSQL INTEGER stops at 2,147,483,647 bytes (~2.0 GiB) — about 40 minutes of
    1080p — and the egress_ended webhook writes the real size straight into it."""
    from sqlalchemy import BigInteger

    from app.models import LiveRecording

    col = LiveRecording.__table__.c.size_bytes
    assert isinstance(col.type, BigInteger)
    # A 4 GiB recording is representable.
    assert 4 * 1024**3 > 2**31 - 1
