"""Object storage for recordings: where the bytes go, and how they come back out.

This module is the piece that was missing. Until now `livekit.start_recording` handed egress a
bare `filepath` with no upload destination, so LiveKit wrote inside its own container — which on
LiveKit Cloud means the request is rejected outright. Every recording therefore came back
`enforced=False` with no file behind it. With an S3-compatible bucket configured, the same call
now carries an upload target and the capture becomes a durable object.

Two jobs, and nothing else:
  * `egress_output(key)` — the destination handed to LiveKit.
  * `signed_url(key, ...)` — a short-lived, tamper-proof GET URL for the player and for downloads.

ponytail: SigV4 is implemented here with stdlib hmac/hashlib rather than pulling in boto3. A
presigned GET is one canonical string and four HMACs; boto3 is ~50 MB of client machinery for a
function that fits on a screen. If this file ever needs multipart uploads, lifecycle rules or
bucket administration, that is the moment to add the SDK — not before.

UNCONFIGURED IS A SUPPORTED STATE. `configured()` returns False, `egress_output` returns None and
`signed_url` returns None. Callers must treat None as "no file exists" and say so; none of them
may invent a URL. That keeps dev and any deployment without a bucket honest instead of broken.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from livekit import api

from app.config import settings

log = logging.getLogger(__name__)

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
# S3 caps a presigned URL at 7 days. Asking for more is silently rejected at fetch time, which
# would look like a broken download rather than a bad configuration.
MAX_TTL = 7 * 24 * 3600


def configured() -> bool:
    return bool(settings.S3_BUCKET and settings.S3_ACCESS_KEY and settings.S3_SECRET_KEY)


# ── keys ──────────────────────────────────────────────────────────────────────

def recording_key(org_id, event_id, recording_id) -> str:
    """Storage key for one recording.

    Org id leads so a bucket policy, a lifecycle rule or a tenant export can be written against a
    prefix. The recording id — not a timestamp — is the leaf: a retry of a failed capture gets its
    own row and therefore its own object, so a retry can never overwrite the file it is retrying.
    """
    return f"recordings/{org_id}/{event_id}/{recording_id}.mp4"


# ── egress destination ────────────────────────────────────────────────────────

def egress_output(key: str, *, filename: str | None = None) -> api.EncodedFileOutput | None:
    """The `file_outputs` entry for a room-composite egress, or None when unconfigured.

    `content_disposition` is set on the OBJECT rather than only on the signed URL so that a file
    fetched by any route (an admin poking at the bucket, a lifecycle copy) still downloads under a
    human filename instead of a uuid.
    """
    if not configured():
        return None
    upload = api.S3Upload(
        access_key=settings.S3_ACCESS_KEY,
        secret=settings.S3_SECRET_KEY,
        region=settings.S3_REGION,
        bucket=settings.S3_BUCKET,
        force_path_style=settings.S3_FORCE_PATH_STYLE,
    )
    if settings.S3_ENDPOINT:
        upload.endpoint = settings.S3_ENDPOINT
    if filename:
        upload.content_disposition = f'attachment; filename="{_ascii_filename(filename)}"'
    out = api.EncodedFileOutput(file_type=api.EncodedFileType.MP4, filepath=key)
    out.s3.CopyFrom(upload)
    return out


def _ascii_filename(name: str) -> str:
    """Header-safe filename. A quote or a newline in a user-supplied title would otherwise end
    the header value early, which is header injection on the download response."""
    cleaned = "".join(c for c in (name or "") if c.isprintable() and c not in '"\\\r\n')
    return cleaned[:120] or "recording.mp4"


# ── presigned GET ─────────────────────────────────────────────────────────────

def _host_and_path(key: str) -> tuple[str, str]:
    """(host, canonical_uri) for the configured addressing style.

    Virtual-hosted (`bucket.s3.region.amazonaws.com/key`) is the AWS default and the only style
    AWS still guarantees. Path style (`host/bucket/key`) is what MinIO and most self-hosted
    gateways need, which is why it is a setting and not a guess.
    """
    endpoint = (settings.S3_ENDPOINT or f"https://{_SERVICE}.{settings.S3_REGION}.amazonaws.com").rstrip("/")
    host = endpoint.split("://", 1)[-1]
    # Each path segment is encoded, but the separators are not — quote(safe="/") does exactly that.
    encoded = quote(key.lstrip("/"), safe="/")
    if settings.S3_FORCE_PATH_STYLE or settings.S3_ENDPOINT:
        return host, f"/{settings.S3_BUCKET}/{encoded}"
    return f"{settings.S3_BUCKET}.{host}", f"/{encoded}"


def _signing_key(date_stamp: str) -> bytes:
    def sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    return sign(sign(sign(sign(f"AWS4{settings.S3_SECRET_KEY}".encode(), date_stamp),
                          settings.S3_REGION), _SERVICE), "aws4_request")


def signed_url(
    key: str,
    *,
    ttl: int | None = None,
    download_name: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """A presigned GET URL, or None when storage is unconfigured or the key is empty.

    `download_name` forces a save-as filename via `response-content-disposition`. That parameter is
    part of the SIGNED query string, so a recipient cannot edit the filename — or any other
    parameter — without invalidating the signature. That is the whole point of signing rather than
    handing out a bucket-public URL: the URL grants one object, one method, for one window.

    `now` is injectable for the self-check only; production always signs against the real clock.
    """
    if not configured() or not key:
        return None

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = stamp.strftime("%Y%m%d")
    host, canonical_uri = _host_and_path(key)
    expires = max(1, min(int(ttl or settings.SIGNED_URL_TTL), MAX_TTL))

    params = {
        "X-Amz-Algorithm": _ALGORITHM,
        "X-Amz-Credential": f"{settings.S3_ACCESS_KEY}/{date_stamp}/{settings.S3_REGION}/{_SERVICE}/aws4_request",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    if download_name:
        params["response-content-disposition"] = f'attachment; filename="{_ascii_filename(download_name)}"'

    # Sorted by key, and every value encoded with the unreserved set only — S3 rejects a
    # signature computed over a differently-encoded query string, so this ordering and this
    # `safe` set are both load-bearing.
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items())
    )
    canonical_request = "\n".join([
        "GET", canonical_uri, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        _ALGORITHM, amz_date, f"{date_stamp}/{settings.S3_REGION}/{_SERVICE}/aws4_request",
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(_signing_key(date_stamp), string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


# ── deletion ──────────────────────────────────────────────────────────────────

def delete_object(key: str) -> tuple[bool, str | None]:
    """Best-effort DELETE of one object. Returns (deleted, error).

    Used when a recording is PURGED from the recycle bin — a soft delete deliberately leaves the
    object alone so a restore is a database write and nothing more.

    ponytail: signs and sends the request with urllib rather than adding boto3 for one verb. A
    failure here is reported, never swallowed into a silent success: an admin who was told "purged"
    while the bytes remain in the bucket has been lied to about a data-deletion request.
    """
    if not configured() or not key:
        return False, "Object storage is not configured"

    import urllib.error
    import urllib.request

    stamp = datetime.now(timezone.utc)
    amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = stamp.strftime("%Y%m%d")
    host, canonical_uri = _host_and_path(key)
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["DELETE", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    string_to_sign = "\n".join([
        _ALGORITHM, amz_date, f"{date_stamp}/{settings.S3_REGION}/{_SERVICE}/aws4_request",
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(_signing_key(date_stamp), string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"{_ALGORITHM} Credential={settings.S3_ACCESS_KEY}/{date_stamp}/{settings.S3_REGION}/"
        f"{_SERVICE}/aws4_request, SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urllib.request.Request(
        f"https://{host}{canonical_uri}",
        method="DELETE",
        headers={"Host": host, "x-amz-date": amz_date,
                 "x-amz-content-sha256": payload_hash, "Authorization": authorization},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            # S3 answers 204 on success and on an already-absent key; both are "gone".
            return resp.status in (200, 204), None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True, None
        log.warning("s3 delete failed for %s: %s", key, exc)
        return False, f"Storage refused the delete ({exc.code})"
    except Exception as exc:  # noqa: BLE001 — network failure must not 500 a purge request
        log.warning("s3 delete failed for %s: %s", key, exc)
        return False, str(exc)[:200]


def demo() -> None:
    """Self-check for the signing logic — the only part here with edge cases worth a guard.

    Runs against fixed credentials and a fixed clock so the signature is deterministic. It cannot
    prove S3 accepts the signature (that needs a real bucket); it proves the canonical form is
    stable, the parameters are all signed, and the encoding rules that break SigV4 in practice
    are applied.
    """
    from app.config import settings as s

    saved = (s.S3_BUCKET, s.S3_REGION, s.S3_ACCESS_KEY, s.S3_SECRET_KEY,
             s.S3_ENDPOINT, s.S3_FORCE_PATH_STYLE)
    s.S3_BUCKET, s.S3_REGION = "zoiko-media", "eu-west-1"
    s.S3_ACCESS_KEY, s.S3_SECRET_KEY = "AKIAEXAMPLE", "secretexample"
    s.S3_ENDPOINT, s.S3_FORCE_PATH_STYLE = "", False
    clock = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)

    try:
        assert configured()
        url = signed_url("recordings/o/e/r.mp4", ttl=600, now=clock)

        # Virtual-hosted host, and the key path is NOT double-encoded.
        assert url.startswith("https://zoiko-media.s3.eu-west-1.amazonaws.com/recordings/o/e/r.mp4?"), url
        assert "%2F" not in url.split("?")[0]
        # Credential's slashes ARE encoded, inside the query string.
        assert "X-Amz-Credential=AKIAEXAMPLE%2F20260804%2Feu-west-1%2Fs3%2Faws4_request" in url
        assert "X-Amz-Expires=600" in url and "X-Amz-Date=20260804T120000Z" in url
        # Deterministic for a fixed clock, and different for a different key.
        assert signed_url("recordings/o/e/r.mp4", ttl=600, now=clock) == url
        assert signed_url("recordings/o/e/other.mp4", ttl=600, now=clock) != url
        # Changing only the TTL changes the signature — i.e. expiry is inside the signature and
        # cannot be extended by editing the URL.
        assert signed_url("recordings/o/e/r.mp4", ttl=601, now=clock) != url

        # response-content-disposition is signed too, so the save-as name is not editable.
        named = signed_url("k.mp4", download_name='Q3 "Town Hall".mp4', ttl=60, now=clock)
        assert "response-content-disposition" in named
        assert named.split("X-Amz-Signature=")[1] != url.split("X-Amz-Signature=")[1]

        # Path style and a custom endpoint put the bucket in the path instead of the host.
        s.S3_ENDPOINT, s.S3_FORCE_PATH_STYLE = "https://minio.internal:9000", True
        assert signed_url("k.mp4", now=clock).startswith("https://minio.internal:9000/zoiko-media/k.mp4?")

        # TTL is clamped to what S3 will accept rather than passed through.
        assert f"X-Amz-Expires={MAX_TTL}" in signed_url("k.mp4", ttl=10**9, now=clock)

        # Header injection through a title cannot escape the disposition value.
        assert '"' not in _ascii_filename('a"b\r\nX-Evil: 1')

        # Unconfigured must yield None, never a URL that 404s or leaks a key name.
        s.S3_BUCKET = ""
        assert not configured() and signed_url("k.mp4") is None and egress_output("k.mp4") is None
    finally:
        (s.S3_BUCKET, s.S3_REGION, s.S3_ACCESS_KEY, s.S3_SECRET_KEY,
         s.S3_ENDPOINT, s.S3_FORCE_PATH_STYLE) = saved

    print("ok  storage signing self-check")


if __name__ == "__main__":
    demo()
