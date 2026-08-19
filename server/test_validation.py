"""Self-check for the pure-logic pieces of dual-recording validation
(services/validation.py). Pure logic, no DB — ffprobe itself is monkey-patched, not
invoked, matching test_watermark.py's approach; a real local burn/probe was additionally
verified manually against the ffmpeg/ffprobe install added this session (see session
notes) — not repeatable here since other environments can't be assumed to have it either.
Run: `python test_validation.py` (or pytest)."""

from types import SimpleNamespace

from app.services import validation as v


def _rec(**kw):
    base = dict(event_id="ev-1", role=None, status="stopped", enforced=True, file_url="rec.mp4",
               validation_status=None, validation_evidence=None)
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeCommit:
    """Stand-in DB session: validate_recording_pair only ever calls db.commit() on the two
    rows it's given directly, never queries — no real Session needed."""
    def commit(self):
        pass


def test_ffprobe_info_returns_none_without_the_binary(monkeypatch):
    monkeypatch.setattr(v.shutil, "which", lambda name: None)
    assert v._ffprobe_info("anything.mp4") is None


def test_ffprobe_info_parses_duration_and_streams(monkeypatch):
    import json as jsonlib
    import subprocess

    monkeypatch.setattr(v.shutil, "which", lambda name: "/usr/bin/ffprobe")
    payload = jsonlib.dumps({
        "format": {"duration": "125.4"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    })
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=payload))
    info = v._ffprobe_info("in.mp4")
    assert info == {"duration_seconds": 125.4, "has_video": True, "has_audio": True}


def test_validate_recording_pair_valid_when_durations_match(monkeypatch):
    monkeypatch.setattr(v.livekit, "download_to_temp", lambda key: f"/tmp/{key}")
    monkeypatch.setattr(v.os, "remove", lambda path: None)
    monkeypatch.setattr(
        v, "_ffprobe_info",
        lambda path: {"duration_seconds": 100.0, "has_video": True, "has_audio": True},
    )
    primary, secondary = _rec(role="primary"), _rec(role="secondary")
    db = _FakeCommit()
    v.validate_recording_pair(db, primary, secondary)
    assert primary.validation_status == "valid"
    assert secondary.validation_status == "valid"
    assert primary.validation_evidence == secondary.validation_evidence
    assert primary.validation_evidence["duration_delta_seconds"] == 0.0
    assert primary.validation_evidence["gap_detection"] == "not implemented"


def test_validate_recording_pair_degraded_on_large_duration_mismatch(monkeypatch):
    monkeypatch.setattr(v.livekit, "download_to_temp", lambda key: f"/tmp/{key}")
    monkeypatch.setattr(v.os, "remove", lambda path: None)
    probes = iter([
        {"duration_seconds": 100.0, "has_video": True, "has_audio": True},
        {"duration_seconds": 60.0, "has_video": True, "has_audio": True},  # 40s off, one path died early
    ])
    monkeypatch.setattr(v, "_ffprobe_info", lambda path: next(probes))
    primary, secondary = _rec(role="primary"), _rec(role="secondary")
    v.validate_recording_pair(_FakeCommit(), primary, secondary)
    assert primary.validation_status == "degraded"
    assert primary.validation_evidence["duration_delta_seconds"] == 40.0


def test_validate_recording_pair_degraded_when_a_stream_is_missing(monkeypatch):
    monkeypatch.setattr(v.livekit, "download_to_temp", lambda key: f"/tmp/{key}")
    monkeypatch.setattr(v.os, "remove", lambda path: None)
    probes = iter([
        {"duration_seconds": 100.0, "has_video": True, "has_audio": True},
        {"duration_seconds": 100.0, "has_video": True, "has_audio": False},  # secondary lost audio
    ])
    monkeypatch.setattr(v, "_ffprobe_info", lambda path: next(probes))
    primary, secondary = _rec(role="primary"), _rec(role="secondary")
    v.validate_recording_pair(_FakeCommit(), primary, secondary)
    assert primary.validation_status == "degraded"


def test_validate_recording_pair_failed_when_download_fails(monkeypatch):
    monkeypatch.setattr(v.livekit, "download_to_temp", lambda key: None)
    primary, secondary = _rec(role="primary"), _rec(role="secondary")
    v.validate_recording_pair(_FakeCommit(), primary, secondary)
    assert primary.validation_status == "failed"
    assert secondary.validation_status == "failed"
    assert "error" in primary.validation_evidence["primary"]


def test_on_recording_captured_skips_dual_path_rows(monkeypatch):
    """Advancement for a dual-path row happens only from process_pending_validations —
    on_recording_captured must not touch the entitlement itself for role != None."""
    calls = []
    monkeypatch.setattr(v.commercial_crud, "get_or_create_replay_entitlement", lambda *a, **k: calls.append(1))
    v.on_recording_captured(_FakeCommit(), _rec(role="primary"))
    assert calls == []


def test_on_recording_captured_ignores_unenforced_or_unstopped(monkeypatch):
    calls = []
    monkeypatch.setattr(v.commercial_crud, "get_or_create_replay_entitlement", lambda *a, **k: calls.append(1))
    v.on_recording_captured(_FakeCommit(), _rec(enforced=False))
    v.on_recording_captured(_FakeCommit(), _rec(status="recording"))
    assert calls == []


if __name__ == "__main__":
    class _MonkeyPatch:
        """Minimal stand-in for pytest's monkeypatch fixture, matching this session's
        established `python test_x.py` self-check convention (see test_watermark.py)."""
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            mp = _MonkeyPatch()
            try:
                _fn(mp)
            finally:
                mp.undo()
            print(f"ok  {_name}")
    print("\nAll validation checks passed.")
