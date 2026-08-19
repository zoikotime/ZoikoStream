"""Burns a static, visible text watermark into a video file — the BRD's "policy
watermark" (LE-AC-12), explicitly distinct from the "advanced DRM and forensic
watermarking" the spec defers to Phase 5+ (no per-viewer tracing, no invisible/
steganographic marks, just a plain corner label).

FFmpeg, not a custom LiveKit egress template: a template needs a URL LiveKit's cloud
egress workers can reach even to test, and can't easily vary text per export recipient.
This runs as a local subprocess against a file already on disk (services.livekit.
download_to_temp) — same "external tool, never raises, honest configured() check" posture
as every other optional-infra function in services/livekit.py."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger(__name__)

# drawtext's default behavior — resolve a font via fontconfig when no fontfile= is given —
# turned out to be environment-fragile: a real local burn (not just a unit test) hit
# "Fontconfig error: Cannot load default config file" on an otherwise-working ffmpeg
# install. Pointing at a known path removes that dependency entirely. The Dockerfile
# installs `fonts-dejavu-core` specifically so this path exists in the deployed
# environment; if it's missing (e.g. a dev machine without that package), burn() omits
# fontfile= and falls back to fontconfig's own lookup rather than failing outright.
# Windows dev machines have no fontconfig at all (same error, unconditionally) but do
# always ship arial.ttf, so it's listed too — real local verification of the replay
# watermark pipeline hit this exact failure on Windows before this line was added.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    r"C:\Windows\Fonts\arial.ttf",
)


def _fontfile() -> str | None:
    return next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)


def configured() -> bool:
    return shutil.which("ffmpeg") is not None


def _escape_drawtext(text: str) -> str:
    """ffmpeg's -vf argument is itself a filter GRAPH, not just this one filter's options:
    a bare ',' chains to a next filter and a bare ':' separates this filter's own options,
    so both need escaping or an ordinary sentence ("...confidential, do not...") silently
    truncates the text and feeds the remainder to ffmpeg as a bogus second filter name
    (caught by a real local burn — see test_watermark.py's regression test for exactly
    this string). '\\' and "'" also need escaping — drawtext's own quoting rules. Order
    matters: backslash first, or the escapes just added would themselves get escaped."""
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )


def burn(input_path: str, output_path: str, text: str) -> tuple[bool, str | None]:
    """Re-encodes input_path with `text` stamped bottom-right, over a semi-transparent
    dark backing box so it stays legible on any footage. Video is re-encoded (libx264) —
    drawtext can't be applied as a stream copy — audio is passed through unchanged
    (-c:a copy) since it isn't touched. Returns (success, error); never raises, so a
    ticker iteration can move on to the next pending delivery instead of dying."""
    if not configured():
        return False, "ffmpeg is not installed on this host"

    safe_text = _escape_drawtext(text)
    font = _fontfile()
    font_opt = f"fontfile='{_escape_drawtext(font)}':" if font else ""
    drawtext = (
        f"drawtext={font_opt}text='{safe_text}':fontcolor=white@0.85:fontsize=18:"
        "box=1:boxcolor=black@0.45:boxborderw=8:"
        "x=w-text_w-24:y=h-text_h-24"
    )
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired:
        return False, "Watermarking timed out"
    except Exception as exc:  # noqa: BLE001 — a burn failure must not crash the ticker
        return False, str(exc)[:400]

    if result.returncode != 0:
        log.warning("ffmpeg watermark burn failed (%s): %s", input_path, result.stderr[-2000:])
        return False, (result.stderr or "ffmpeg failed")[-400:]
    return True, None
