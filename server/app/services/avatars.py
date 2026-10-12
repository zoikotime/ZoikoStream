"""Profile photos: the user's OWN avatar (users.avatar_url).

What is stored where:
  users.avatar_url   the path the photo is served from, /api/avatars/<user_id>/<random>.webp.
                     Never the image. NULL means no photo: every surface shows initials.
  object storage     the image, at avatars/<user_id>/<random>.webp — the platform's GCS bucket
                     when it is configured (the same bucket and client as recordings and
                     exports), otherwise the server's private, git-ignored `private_exports/`
                     directory so a developer machine exercises the real lifecycle. Production
                     refuses uploads without GCS: a container's disk is neither durable nor shared.

What an upload has to survive before it is stored (process):
  size        at most MAX_BYTES, checked while the body is read (routers/profile.py) and here.
  type        decided by the file's own bytes (PNG / JPEG / WebP signatures) and confirmed by
              decoding it. The client's MIME type, the filename and its extension are never read.
              SVG, GIF, HTML, scripts, anything else: refused.
  dimensions  a decoded-pixel ceiling (a few KB can declare a gigapixel canvas) and a minimum side.
  content     re-encoded from decoded pixels: orientation applied from EXIF, centre-cropped to a
              square (never stretched), at most SIZE x SIZE, saved as WebP. Re-encoding drops EXIF
              (GPS included) and anything appended to or hidden inside the original file.

The stored name is random per upload, so the URL changes when the photo does (caches never show a
stale one) and cannot be guessed; nothing from the uploaded filename is used anywhere.
"""

from __future__ import annotations

import io
import logging
import pathlib
import re
import secrets
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import settings

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
MIN_SIDE = 32
SIZE = 512
URL_PREFIX = "/api/avatars/"
LOCAL_ROOT = pathlib.Path("private_exports")

_NAME = re.compile(r"^[A-Za-z0-9_-]{32}\.webp$")
_URL = re.compile(
    r"^/api/avatars/(?P<user>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/(?P<name>[A-Za-z0-9_-]{32}\.webp)$"
)

ALLOWED = "Upload a PNG, JPG or WebP image."


class AvatarRejected(ValueError):
    """An upload that will not be stored, with the HTTP status and a message for the user."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AvatarStorageUnavailable(RuntimeError):
    """No durable storage to put a photo in (production without GCS)."""


# ── validation and processing ─────────────────────────────────────────────────────────────

def _sniff(head: bytes) -> str | None:
    """The image format the bytes themselves declare, or None. Only the three we accept."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def process(raw: bytes) -> bytes:
    """Validate an upload and return the square WebP to store, or raise AvatarRejected."""
    if not raw:
        raise AvatarRejected(422, "That file is empty.")
    if len(raw) > MAX_BYTES:
        raise AvatarRejected(413, "That image is larger than 5 MB.")
    kind = _sniff(raw[:16])
    if kind is None:
        raise AvatarRejected(415, ALLOWED)
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            if probe.format != kind:
                raise AvatarRejected(415, ALLOWED)
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise AvatarRejected(422, "That image's dimensions are too large.")
            if min(width, height) < MIN_SIDE:
                raise AvatarRejected(422, f"That image is too small — at least {MIN_SIDE}×{MIN_SIDE} pixels.")
            probe.verify()                       # structural check; the object is spent after it
        with Image.open(io.BytesIO(raw)) as source:
            source.seek(0)                       # an animated image contributes its first frame
            img = ImageOps.exif_transpose(source)
            img = img.convert("RGBA" if _has_alpha(img) else "RGB")
            side = min(SIZE, img.width, img.height)
            img = ImageOps.fit(img, (side, side), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=88, method=4)
            return out.getvalue()
    except AvatarRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise AvatarRejected(422, "That image could not be read. Try a different file.") from exc


# ── storage ───────────────────────────────────────────────────────────────────────────────

def _gcs_bucket():
    """The configured GCS bucket, or None to use local storage. Production never falls back.

    Outside production GCS is used only when explicitly configured (the same rule as
    developer exports), so a developer machine with a stray bucket name and no key writes
    locally. In production any working client counts — the explicit key or the server's own
    identity (Application Default Credentials), exactly as recording reads do."""
    from . import livekit

    if livekit.gcs_configured() or settings.is_production():
        client = livekit._gcs_client() if settings.GCS_BUCKET.strip() else None  # noqa: SLF001
        if client is not None:
            return client.bucket(settings.GCS_BUCKET)
    if settings.is_production():
        raise AvatarStorageUnavailable("profile photos need GCS in production")
    return None


def _key(user_id, name: str) -> str:
    return f"avatars/{user_id}/{name}"


def url_for(user_id, name: str) -> str:
    return f"{URL_PREFIX}{user_id}/{name}"


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def parse(url: str | None) -> tuple[str, str] | None:
    """(user_id, name) for one of OUR avatar paths, or None for anything else."""
    match = _URL.match(url or "")
    return (match["user"], match["name"]) if match else None


def save(user_id: uuid.UUID, data: bytes) -> str:
    """Store a processed photo under a fresh random name; returns the path it is served from."""
    name = f"{secrets.token_urlsafe(24)}.webp"       # 32 URL-safe characters
    key = _key(user_id, name)
    bucket = _gcs_bucket()
    if bucket is not None:
        blob = bucket.blob(key)
        blob.upload_from_string(data, content_type="image/webp")
    else:
        path = LOCAL_ROOT / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return url_for(user_id, name)


def load(user_id: uuid.UUID, name: str) -> bytes | None:
    if not valid_name(name):
        return None
    key = _key(user_id, name)
    try:
        bucket = _gcs_bucket()
    except AvatarStorageUnavailable:
        return None
    if bucket is not None:
        try:
            return bucket.blob(key).download_as_bytes()
        except Exception:  # noqa: BLE001 — a missing or unreadable object is simply "no photo"
            return None
    path = LOCAL_ROOT / key
    return path.read_bytes() if path.is_file() else None


def delete(url: str | None) -> bool:
    """Best-effort removal of a photo this service stored. Anything that is not one of our own
    avatar paths (a shared default, an external URL, a malformed value) is never touched."""
    parsed = parse(url)
    if parsed is None:
        return False
    key = _key(*parsed)
    try:
        bucket = _gcs_bucket()
        if bucket is not None:
            bucket.blob(key).delete()
        else:
            (LOCAL_ROOT / key).unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 — the profile change stands; an orphaned object is harmless
        log.warning("could not delete a replaced profile photo", exc_info=True)
        return False
