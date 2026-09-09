"""GCS credential configuration, and its independence from LiveKit publishing.

Two concerns, deliberately tested apart, because in production they were conflated:

  A. GCS credentials / recording egress   — was misconfigured (a Console URL)
  B. LiveKit host publishing / viewing    — was NOT caused by A, and these tests prove it

The headline assertions:

    a URL is refused BEFORE any filesystem read   test_a_console_url_is_refused_as_config
    credential contents are never logged          test_credentials_are_never_logged
    go-live has no recording dependency           test_go_live_does_not_depend_on_recording
    "No media is being published" is track-based  test_no_media_message_comes_from_tracks

Run with `python test_storage_config.py` (or pytest).
"""
import json
import logging
import os
import tempfile
import uuid
from unittest.mock import patch

import app.services.livekit as lk
from app.config import settings

# A structurally valid service-account key with obviously fake material. The private key is
# not a real one and grants nothing; it exists so the "never logged" assertions have a
# distinctive needle to search for.
FAKE_SECRET = "THIS-PRIVATE-KEY-MUST-NEVER-BE-LOGGED-4471"
VALID_KEY = {
    "type": "service_account",
    "project_id": "zoiko-stream-test",
    "private_key_id": "abc123",
    "private_key": f"-----BEGIN PRIVATE KEY-----\n{FAKE_SECRET}\n-----END PRIVATE KEY-----\n",
    "client_email": "recorder@zoiko-stream-test.iam.gserviceaccount.com",
    "client_id": "1234567890",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# The exact value found in the broken deployment.
CONSOLE_URL = ("https://console.cloud.google.com/storage/browser/zoiko-stream-recordings"
               ";tab=objects?forceOnBucketsSortingFiltering=true&project=project-4b10b0ae")

RESULTS = []


def run(fn):
    # Every test starts from a clean cache: _gcs_credentials_json is lru_cached, so a value
    # read under one setting would otherwise leak into the next.
    lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
    lk._gcs_client.cache_clear()            # noqa: SLF001
    try:
        fn()
        RESULTS.append((fn.__name__, None))
        print(f"ok  {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((fn.__name__, exc))
        print(f"FAIL {fn.__name__}: {type(exc).__name__}: {exc}")
    finally:
        lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
        lk._gcs_client.cache_clear()            # noqa: SLF001


class configured:
    """Point the settings at a credential value for the body of a `with`."""

    def __init__(self, path, bucket="zoiko-stream-recordings"):
        self.path, self.bucket = path, bucket

    def __enter__(self):
        self._path = settings.GCS_CREDENTIALS_PATH
        self._bucket = settings.GCS_BUCKET
        settings.GCS_CREDENTIALS_PATH = self.path
        settings.GCS_BUCKET = self.bucket
        lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
        lk._gcs_client.cache_clear()            # noqa: SLF001
        return self

    def __exit__(self, *exc):
        settings.GCS_CREDENTIALS_PATH = self._path
        settings.GCS_BUCKET = self._bucket
        lk._gcs_credentials_json.cache_clear()  # noqa: SLF001
        lk._gcs_client.cache_clear()            # noqa: SLF001
        return False


class captured_logs:
    """Everything the livekit service logs inside the block, as one string."""

    def __enter__(self):
        self.records = []
        self.handler = logging.Handler()
        self.handler.emit = self.records.append
        self.logger = logging.getLogger(lk.__name__)
        self.logger.addHandler(self.handler)
        self._level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self._level)
        return False

    def text(self):
        out = []
        for record in self.records:
            try:
                out.append(record.getMessage())
            except Exception:  # noqa: BLE001
                out.append(str(record.msg))
            out.append(" ".join(str(a) for a in (record.args or ())))
        return " ".join(out)


def _key_file(payload, suffix=".json"):
    handle = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    handle.write(payload if isinstance(payload, str) else json.dumps(payload))
    handle.close()
    return handle.name


# ══ PART A — credential configuration ═══════════════════════════════════════════════════

def test_a_console_url_is_refused_as_config():
    """1 - the production failure, and it must not reach the filesystem at all."""
    with configured(CONSOLE_URL):
        with patch.object(lk.Path, "read_text",
                          side_effect=AssertionError("a URL was handed to the filesystem")):
            with captured_logs() as logs:
                assert lk._gcs_credentials_json() is None  # noqa: SLF001
        text = logs.text()
        # Structured, greppable, and it names the mistake rather than the symptom.
        assert "gcs_credentials_invalid" in text
        assert "path_type=url" in text
        assert "recording_enabled=True" in text
        assert "/secrets/gcs-key.json" in text, "the log should say what a correct value is"
        # The old message described the symptom instead of the cause.
        assert "No such file or directory" not in text

        assert lk.gcs_configured() is False
        error = lk.gcs_config_error()
        assert error and "URL" in error
        assert lk.gcs_diagnostics()["gcs_credentials_mode"] == "url"


def test_every_url_scheme_is_refused_but_a_windows_path_is_not():
    for value in ("https://console.cloud.google.com/x", "http://x/y", "gs://bucket/key.json"):
        assert lk._looks_like_url(value) is True, value  # noqa: SLF001
    # A drive letter is not a scheme. Refusing "C:\\keys\\gcs.json" would break local dev.
    for value in ("/secrets/gcs-key.json", "C:\\keys\\gcs.json", "./key.json", "", "key.json"):
        assert lk._looks_like_url(value) is False, value  # noqa: SLF001


def test_a_missing_file_fails_clearly():
    """2."""
    missing = os.path.join(tempfile.gettempdir(), f"absent-{uuid.uuid4().hex}.json")
    with configured(missing):
        with captured_logs() as logs:
            assert lk._gcs_credentials_json() is None  # noqa: SLF001
        text = logs.text()
        assert "gcs_credentials_invalid" in text
        assert "path_type=file" in text
        assert lk.gcs_configured() is False
        assert lk.gcs_diagnostics()["gcs_credentials_mode"] == "missing"
        assert lk.gcs_diagnostics()["gcs_credentials_path_exists"] is False


def test_a_valid_mounted_key_loads():
    """3 - what a correct Cloud Run secret mount looks like."""
    path = _key_file(VALID_KEY)
    try:
        with configured(path):
            raw = lk._gcs_credentials_json()  # noqa: SLF001
            assert raw is not None
            assert json.loads(raw)["client_email"] == VALID_KEY["client_email"]
            assert lk.gcs_configured() is True
            assert lk.gcs_config_error() is None
            diagnostics = lk.gcs_diagnostics()
            assert diagnostics["gcs_credentials_mode"] == "mounted_file"
            assert diagnostics["gcs_credentials_path_exists"] is True
            assert diagnostics["gcs_egress_uploads_available"] is True
            # An identifier, not a credential — this is what the IAM binding is checked
            # against, and it is the ONLY credential-derived value the report exposes.
            assert diagnostics["service_account"] == VALID_KEY["client_email"]
    finally:
        os.unlink(path)


def test_invalid_json_fails_safely():
    """4."""
    path = _key_file("this is not json { ")
    try:
        with configured(path):
            with captured_logs() as logs:
                assert lk._gcs_credentials_json() is None  # noqa: SLF001
            assert "path_type=file_not_json" in logs.text()
            assert lk.gcs_configured() is False
            assert lk.gcs_diagnostics()["gcs_credentials_mode"] == "malformed"
    finally:
        os.unlink(path)


def test_valid_json_that_is_not_a_service_account_key_is_refused():
    """A user ADC credential or OAuth client secret parses fine and then fails mid-egress."""
    path = _key_file({"type": "authorized_user", "client_id": "x", "refresh_token": "y"})
    try:
        with configured(path):
            with captured_logs() as logs:
                assert lk._gcs_credentials_json() is None  # noqa: SLF001
            assert "path_type=file_wrong_kind" in logs.text()
            assert lk.gcs_diagnostics()["gcs_credentials_mode"] == "wrong_kind"
            error = lk.gcs_config_error()
            assert error and "service_account" in error
    finally:
        os.unlink(path)


def test_credentials_are_never_logged():
    """5 - the private key must not reach a log line, on any path."""
    path = _key_file(VALID_KEY)
    try:
        # Success path.
        with configured(path), captured_logs() as logs:
            lk._gcs_credentials_json()  # noqa: SLF001
            lk.gcs_config_error()
            lk.gcs_diagnostics()
        assert FAKE_SECRET not in logs.text()
        assert "BEGIN PRIVATE KEY" not in logs.text()

        # Failure paths, where a naive implementation would echo the file it could not parse.
        broken = _key_file(f"{{ not json, but contains {FAKE_SECRET} ")
        try:
            with configured(broken), captured_logs() as logs2:
                lk._gcs_credentials_json()  # noqa: SLF001
                lk.gcs_config_error()
            assert FAKE_SECRET not in logs2.text(), "the unparseable file body was logged"
        finally:
            os.unlink(broken)

        # And the diagnostic never carries key material in any field.
        with configured(path):
            blob = json.dumps(lk.gcs_diagnostics())
        assert FAKE_SECRET not in blob
        assert "private_key" not in blob
    finally:
        os.unlink(path)


def test_the_diagnostic_reports_mode_without_secrets():
    """9 (report) - safe to log at startup, in every configuration."""
    for value in ("", CONSOLE_URL, "/nope/missing.json"):
        with configured(value):
            diagnostics = lk.gcs_diagnostics()
        assert set(diagnostics) == {
            "gcs_credentials_mode", "gcs_credentials_path_exists",
            "gcs_app_reads_available", "gcs_egress_uploads_available",
            "gcs_bucket_configured", "livekit_configured",
            "livekit_webhooks_verifiable", "service_account",
        }
        for key, val in diagnostics.items():
            assert not isinstance(val, (bytes, bytearray)), key
        assert diagnostics["service_account"] is None
    with configured(""):
        assert lk.gcs_diagnostics()["gcs_credentials_mode"] == "absent"


def test_egress_upload_is_only_attempted_when_configured():
    """8 - no destination means no egress request is built."""
    with configured(CONSOLE_URL):
        assert lk.gcs_configured() is False
    with configured("", bucket=""):
        assert lk.gcs_configured() is False
        assert lk.gcs_config_error() is not None


# ══ PART B — publishing is independent of recording ═════════════════════════════════════

def test_go_live_does_not_depend_on_recording():
    """6, 7 - a bad GCS credential must not stop a host going live.

    Structural, and deliberately so: this asserts there is no code path from the go-live
    handler to a storage check, which is stronger than asserting one particular call
    happens to succeed today.
    """
    import ast
    import inspect

    from app.services import broadcast

    source = inspect.getsource(broadcast._golive)  # noqa: SLF001
    tree = ast.parse(source.lstrip())
    body = tree.body[0]
    if ast.get_docstring(body):
        body.body = body.body[1:]
    code = ast.unparse(body)
    for storage in ("gcs", "GCS", "recording_start", "_recording_start", "egress",
                    "gcs_config_error", "gcs_configured"):
        assert storage not in code, (
            f"the go-live path references {storage} — a storage failure could block "
            "a host from going live")

    # And recording is never started automatically as part of going live. Checked against
    # the CODE with comments stripped: broadcast.py explains in prose that the
    # `auto_start_recording` column was removed precisely because nothing read it, and a
    # bare substring search would flag that explanation rather than a defect.
    import io as _io
    import tokenize

    module_source = inspect.getsource(broadcast)
    stripped = []
    for token in tokenize.generate_tokens(_io.StringIO(module_source).readline):
        if token.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        stripped.append(token.string)
    module_code = " ".join(stripped)
    assert "auto_start_recording" not in module_code, (
        "auto-start recording would couple publishing to storage")


def test_recording_failure_is_recorded_as_recording_state():
    """13 - a failed recording is a recording state, not a broadcast state.

    LiveRecording carries its own status and error. Nothing in that model can express
    "the broadcast is down", which is what keeps the two from being conflated.
    """
    from app.models import LiveRecording

    columns = {c.name for c in LiveRecording.__table__.columns}
    assert {"status", "error", "enforced"} <= columns
    # The recording row has no authority over the event's own status.
    for broadcast_field in ("event_status", "live", "publishing", "degraded"):
        assert broadcast_field not in columns, broadcast_field


def test_no_media_message_comes_from_tracks():
    """9 (tests list) - the degraded reason is track-driven, with no storage input."""
    from app.services.broadcast import health_of

    publishing = {"publishing": 1, "participants": 3, "poor_connections": 0}
    silent = {"publishing": 0, "participants": 3, "poor_connections": 0}

    # A live event with a publisher is healthy - EVEN when recording is known-broken.
    healthy = health_of(publishing, "live", recording_enforced=False)
    assert "No media is being published" not in healthy["issues"]
    assert healthy["level"] != "down", "a recording failure must not read as a media outage"
    # The recording problem IS reported, separately and by name.
    assert "Recording is not being captured" in healthy["issues"]

    # No publisher is what produces the message, regardless of recording state.
    for enforced in (True, False, None):
        down = health_of(silent, "live", recording_enforced=enforced)
        assert "No media is being published" in down["issues"]
        assert down["level"] == "down"

    # And a healthy recording changes nothing about the media verdict either way.
    assert health_of(publishing, "live", recording_enforced=True)["level"] == "ok"


def test_publishing_flag_is_set_only_by_livekit_track_webhooks():
    """The provenance of `publishing`, asserted rather than assumed."""
    import inspect

    from app.routers import live

    assert live._TRACK_EVENTS == {"track_published": True, "track_unpublished": False}  # noqa: SLF001
    source = inspect.getsource(live.livekit_webhook)
    # The only writer of the flag is the track webhook branch.
    assert '{"publishing": _TRACK_EVENTS[kind]}' in source
    assert source.count('"publishing"') == 1, (
        "something else writes the publishing flag — its provenance is no longer single")


# ══ PART B — token grants and room identity ═════════════════════════════════════════════

def test_host_token_can_publish_and_viewer_token_cannot():
    """11, 12 - decoded from the real issued JWTs, not from the call arguments."""
    import base64

    if not lk.configured():
        raise AssertionError("LiveKit must be configured for this environment's test run")

    room = f"event_{uuid.uuid4()}"

    def grants(token):
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["video"]

    host = grants(lk.create_stream_token("host-identity", room, True))
    viewer = grants(lk.create_stream_token("viewer-identity", room, False))

    assert host["roomJoin"] is True and host["room"] == room
    assert host["canPublish"] is True
    assert host["canSubscribe"] is True

    assert viewer["roomJoin"] is True and viewer["room"] == room
    # A viewer must not be able to publish into somebody else's broadcast.
    assert viewer.get("canPublish") in (False, None)
    assert viewer["canSubscribe"] is True

    # 10 - producer and viewer address the SAME room.
    assert host["room"] == viewer["room"]


def test_room_name_round_trips_to_its_event():
    """10 - the mapping both sides rely on."""
    from app.services import moderation

    event_id = uuid.uuid4()
    room = f"event_{event_id}"
    assert str(moderation.event_id_from_room(room)) == str(event_id)
    assert moderation.event_id_from_room("not-a-room") is None


def test_a_hosts_own_console_does_not_read_as_a_separate_participant():
    """A tagged secondary identity resolves back to its owner.

    Directly relevant to the reported symptom: if it did not, a host's own publish would
    land under a phantom identity and health_of() would report "No media is being
    published" over a broadcast that was actually fine.
    """
    identity = str(uuid.uuid4())
    tagged = lk.secondary(identity, "host")
    assert tagged != identity
    assert lk.primary(tagged) == identity
    # Safe on every other identity shape.
    for other in (identity, "guest-123", "viewer-abc", "ingress-1"):
        assert lk.primary(other) == other


TESTS = [
    test_a_console_url_is_refused_as_config,
    test_every_url_scheme_is_refused_but_a_windows_path_is_not,
    test_a_missing_file_fails_clearly,
    test_a_valid_mounted_key_loads,
    test_invalid_json_fails_safely,
    test_valid_json_that_is_not_a_service_account_key_is_refused,
    test_credentials_are_never_logged,
    test_the_diagnostic_reports_mode_without_secrets,
    test_egress_upload_is_only_attempted_when_configured,
    test_go_live_does_not_depend_on_recording,
    test_recording_failure_is_recorded_as_recording_state,
    test_no_media_message_comes_from_tracks,
    test_publishing_flag_is_set_only_by_livekit_track_webhooks,
    test_host_token_can_publish_and_viewer_token_cannot,
    test_room_name_round_trips_to_its_event,
    test_a_hosts_own_console_does_not_read_as_a_separate_participant,
]

if __name__ == "__main__":
    for t in TESTS:
        run(t)
    failed = [n for n, e in RESULTS if e is not None]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    if failed:
        raise SystemExit(1)
