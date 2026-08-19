"""Self-check for the watermark burn contract (services/watermark.py). Pure logic — the
ffmpeg command construction and the drawtext escaping — verified by monkey-patching
shutil.which/subprocess.run rather than actually invoking ffmpeg, so this passes in any
environment regardless of whether ffmpeg happens to be installed. A real end-to-end burn
was additionally verified manually against a local ffmpeg install (see session notes) —
not repeatable here since CI/other dev machines can't be assumed to have ffmpeg either.
Run: `python test_watermark.py` (or pytest)."""

import subprocess
from types import SimpleNamespace

from app.services import watermark as w


def test_configured_reflects_shutil_which(monkeypatch):
    monkeypatch.setattr(w.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert w.configured() is True
    monkeypatch.setattr(w.shutil, "which", lambda name: None)
    assert w.configured() is False


def test_burn_refuses_when_not_configured(monkeypatch):
    monkeypatch.setattr(w, "configured", lambda: False)
    ok, error = w.burn("in.mp4", "out.mp4", "hello")
    assert ok is False
    assert "not installed" in error


def test_escape_drawtext_handles_colons_quotes_backslashes(monkeypatch):
    # Colons and quotes are drawtext filter-graph syntax; an unescaped one would end the
    # text early or break the filter chain entirely — this is what makes a recipient's
    # free-text name/email safe to interpolate.
    assert w._escape_drawtext("plain text") == "plain text"
    assert w._escape_drawtext("O'Brien") == "O\\'Brien"
    assert w._escape_drawtext("10:30am") == "10\\:30am"
    assert w._escape_drawtext("back\\slash") == "back\\\\slash"


def test_escape_drawtext_handles_commas(monkeypatch):
    """Regression: a bare ',' chains to a NEXT filter in ffmpeg's -vf graph syntax. This
    exact production string ("...confidential, do not redistribute...",
    services/delivery.py's watermark text) silently truncated at the comma and fed the
    remainder to ffmpeg as a bogus filter name — caught only by a real local burn, not by
    a mocked unit test, which is why this regression test exists at all."""
    assert w._escape_drawtext("confidential, do not redistribute") == "confidential\\, do not redistribute"


def test_burn_invokes_ffmpeg_with_expected_command(monkeypatch):
    monkeypatch.setattr(w, "configured", lambda: True)
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, error = w.burn("/tmp/in.mp4", "/tmp/out.mp4", "Prepared for Jordan Family")
    assert ok is True
    assert error is None
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "/tmp/in.mp4" in cmd
    assert "/tmp/out.mp4" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "drawtext=" in vf
    assert "Prepared for Jordan Family" in vf
    assert "-c:a" in cmd and "copy" in cmd  # audio untouched


def test_burn_reports_ffmpeg_failure(monkeypatch):
    monkeypatch.setattr(w, "configured", lambda: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stderr="Unknown encoder 'libx264'"),
    )
    ok, error = w.burn("in.mp4", "out.mp4", "text")
    assert ok is False
    assert "libx264" in error


if __name__ == "__main__":
    class _MonkeyPatch:
        """Minimal stand-in for pytest's monkeypatch fixture so these run without pytest,
        matching this session's established `python test_x.py` self-check convention."""
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
    print("\nAll watermark checks passed.")
