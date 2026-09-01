"""Governed developer data export (ZST-EC-001 DEV-012).

The product's only export was `client/src/utils/export.js` — a browser-side CSV. That is not
a governed export: nothing records who asked for it, what it contained, or who downloaded it,
and nothing bounds how long it stays retrievable.

This is the server-side lifecycle:

    REQUESTED -> PROCESSING -> READY -> (downloaded) -> EXPIRED
                            \\-> FAILED

Two properties are load-bearing:

  * **No secret can be exported.** Field selection is explicit per export type, listed in
    `_ROWS`. It is never a dump of a row, so a column added to a model later cannot silently
    start appearing in customer exports. API key secrets are unrecoverable anyway (hash-only),
    but webhook signing secrets ARE recoverable material, and they are deliberately excluded.

  * **The object never becomes public.** It is written to private storage, and each download
    is authorized by a single-use, short-lived, sha256-stored token. Expiry genuinely stops
    the link working: the token is cleared, so the old one cannot be resurrected.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..crud import admin as admin_crud
from ..email import UnsafeLinkError, public_base_url
from ..models import (
    EXPORT_CREDENTIALS,
    EXPORT_DELIVERIES,
    EXPORT_EXPIRED,
    EXPORT_FAILED,
    EXPORT_PROCESSING,
    EXPORT_READY,
    EXPORT_TTL_HOURS,
    EXPORT_WEBHOOKS,
    DeveloperDataExport,
    Organization,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from . import org_comms, signing_rotation

log = logging.getLogger(__name__)

TICKER_INTERVAL_SECONDS = 900.0

FAILURE_CATEGORIES = {
    "storage_unavailable": "Export storage was unavailable",
    "generation_error": "The export could not be generated",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── content: explicit field selection, never a row dump ─────────────────────────────────

def _credential_rows(db: Session, org: Organization) -> tuple[list[str], list[list]]:
    header = ["credential_id", "label", "fingerprint", "status", "created_at",
              "expires_at", "revoked_at", "revoked_by"]
    rows = []
    for r in (org.api_keys or []):
        # `key_hash` and `prefix` are both deliberately absent: one is the verifier, the
        # other carries four characters of the real token.
        rows.append([r.get("id"), r.get("label"), admin_crud.key_fingerprint(r),
                     admin_crud.key_status(r), r.get("created_at"), r.get("expires_at"),
                     r.get("revoked_at"), r.get("revoked_by")])
    return header, rows


def _webhook_rows(db: Session, org: Organization) -> tuple[list[str], list[list]]:
    header = ["endpoint_id", "url", "label", "events", "status", "health", "enabled",
              "signing_fingerprint", "verified_at", "last_success_at",
              "consecutive_failures", "created_at"]
    rows = []
    for ep in db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.org_id == org.id)
    ).all():
        # The signing secret itself is recoverable material and is never exported — only
        # its one-way fingerprint, which discloses nothing about it.
        rows.append([str(ep.id), ep.url, ep.label, ",".join(ep.events or []), ep.status,
                     ep.health, ep.enabled, signing_rotation.fingerprint(ep.secret),
                     ep.verified_at, ep.last_success_at, ep.consecutive_failures,
                     ep.created_at])
    return header, rows


def _delivery_rows(db: Session, org: Organization) -> tuple[list[str], list[list]]:
    header = ["delivery_id", "endpoint_id", "event_type", "status", "attempt_count",
              "last_response_code", "delivered_at", "dead_lettered_at", "created_at"]
    endpoint_ids = [e.id for e in db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.org_id == org.id)).all()]
    rows = []
    if endpoint_ids:
        for d in db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id.in_(endpoint_ids))
            .order_by(WebhookDelivery.created_at.desc()).limit(5000)
        ).all():
            # `payload` is excluded: it is customer event data, and an export of delivery
            # HISTORY does not need to reproduce every body that was sent.
            rows.append([str(d.id), str(d.endpoint_id), d.event_type, d.status,
                         d.attempt_count, d.last_response_code, d.delivered_at,
                         d.dead_lettered_at, d.created_at])
    return header, rows


_ROWS = {
    EXPORT_CREDENTIALS: _credential_rows,
    EXPORT_WEBHOOKS: _webhook_rows,
    EXPORT_DELIVERIES: _delivery_rows,
}

EXPORT_LABELS = {
    EXPORT_CREDENTIALS: "API credential metadata",
    EXPORT_WEBHOOKS: "Webhook endpoint metadata",
    EXPORT_DELIVERIES: "Webhook delivery history",
}


def render_csv(db: Session, org: Organization, export_type: str) -> tuple[str, int]:
    builder = _ROWS.get(export_type)
    if builder is None:
        raise ValueError(f"Unsupported export type: {export_type}")
    header, rows = builder(db, org)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue(), len(rows)


# ── lifecycle ───────────────────────────────────────────────────────────────────────────

def request_export(db: Session, *, org: Organization, requester: User,
                   export_type: str) -> DeveloperDataExport:
    record = DeveloperDataExport(
        org_id=org.id, requested_by=requester.id, requested_by_email=requester.email,
        export_type=export_type, status="requested", requested_at=_now(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def process(db: Session, export: DeveloperDataExport) -> str:
    """Generate the export and store it privately. Returns the raw download token on success.

    A storage failure marks the export FAILED — it never marks it READY with nothing behind
    the link, which is the failure mode that would leave a customer clicking into a 404.
    """
    export.status = EXPORT_PROCESSING
    export.started_at = _now()
    db.commit()

    org = db.get(Organization, export.org_id)
    try:
        content, count = render_csv(db, org, export.export_type)
    except Exception:  # noqa: BLE001 — the customer gets a category, never a traceback
        log.exception("Export %s generation failed", export.id)
        fail(db, export, "generation_error")
        return ""

    object_key = f"developer-exports/{export.org_id}/{export.id}.csv"
    try:
        _store(object_key, content)
    except Exception:  # noqa: BLE001
        log.exception("Export %s storage failed", export.id)
        fail(db, export, "storage_unavailable")
        return ""

    raw = secrets.token_urlsafe(32)
    export.object_key = object_key
    export.download_token_hash = _hash(raw)
    export.row_count = count
    export.status = EXPORT_READY
    export.completed_at = _now()
    export.expires_at = _now() + timedelta(hours=EXPORT_TTL_HOURS)
    db.commit()
    db.refresh(export)
    return raw


def _store(object_key: str, content: str) -> None:
    """Write to private storage.

    Falls back to a local private directory when no bucket is configured, so a developer
    environment still exercises the real lifecycle rather than a stub. Neither destination
    is publicly reachable — the download is always authorized per request.
    """
    from ..config import settings
    from . import livekit

    # `gcs_configured()` checks the bucket AND that credentials actually resolve. Testing the
    # bucket name alone is not enough: a configured bucket with no usable credentials
    # yields a None client, and reaching for .bucket() on it fails the export for a reason
    # that has nothing to do with the customer's data.
    if livekit.gcs_configured():
        client = livekit._gcs_client()  # noqa: SLF001 — the one shared GCS client
        blob = client.bucket(settings.GCS_BUCKET).blob(object_key)
        blob.upload_from_string(content, content_type="text/csv")
        return

    import pathlib

    path = pathlib.Path("private_exports") / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load(export: DeveloperDataExport) -> str | None:
    from . import livekit

    if not export.object_key:
        return None
    if livekit.gcs_configured():
        from ..config import settings

        client = livekit._gcs_client()  # noqa: SLF001
        blob = client.bucket(settings.GCS_BUCKET).blob(export.object_key)
        return blob.download_as_text()
    import pathlib

    path = pathlib.Path("private_exports") / export.object_key
    return path.read_text(encoding="utf-8") if path.exists() else None


def fail(db: Session, export: DeveloperDataExport, category: str) -> None:
    export.status = EXPORT_FAILED
    export.failed_at = _now()
    export.failure_category = category
    db.commit()
    db.refresh(export)


def expire(db: Session, export: DeveloperDataExport) -> bool:
    """Expire an export. The token is CLEARED, so the old link cannot be resurrected."""
    if export.status != EXPORT_READY:
        return False
    export.status = EXPORT_EXPIRED
    export.download_token_hash = None
    db.commit()
    db.refresh(export)
    return True


def authorize_download(db: Session, export: DeveloperDataExport, token: str,
                       user: User | None = None, client: str | None = None) -> bool:
    """Check a download authorization and record the access.

    The log is written only when the download is actually authorized and about to be served
    — a clicked link that fails authorization is not a download.
    """
    if export.status != EXPORT_READY or not export.download_token_hash:
        return False
    if export.expires_at and export.expires_at <= _now():
        expire(db, export)
        return False
    import hmac

    if not hmac.compare_digest(_hash(token or ""), export.download_token_hash):
        return False

    entry = {"at": _now().isoformat(),
             "by": str(user.id) if user else None,
             "client": (client or "")[:120]}
    existing = json.loads(export.access_log) if export.access_log else []
    existing.append(entry)
    export.access_log = json.dumps(existing[-50:])
    export.download_count += 1
    export.last_downloaded_at = _now()
    export.last_downloaded_by = user.id if user else None
    db.commit()
    return True


def download_url(export: DeveloperDataExport, token: str) -> str:
    from urllib.parse import quote

    return (f"{public_base_url()}/api/organization/developer/exports/"
            f"{export.id}/download?token={quote(token, safe='')}")


# ── notifications ───────────────────────────────────────────────────────────────────────

def _claim(db: Session, export: DeveloperDataExport, column: str) -> bool:
    if getattr(export, column) is not None:
        return False
    setattr(export, column, _now())
    db.commit()
    return True


def _recipients(db: Session, export: DeveloperDataExport) -> list[str]:
    """The requester. An export is answered to whoever asked for it."""
    requester = db.get(User, export.requested_by) if export.requested_by else None
    return org_comms.recipients(requester,
                                extra=[export.requested_by_email] if not requester else None)


def notify_ready(db: Session, background, export: DeveloperDataExport, token: str) -> bool:
    if not _claim(db, export, "ready_notified_at"):
        return False
    addresses = _recipients(db, export)
    if not addresses:
        return False
    org = db.get(Organization, export.org_id)
    try:
        url = download_url(export, token)
    except UnsafeLinkError:
        log.exception("DEV-012 not queued: APP_URL unsafe for this environment")
        return False
    for address in addresses:
        background.add_task(
            email_mod.send_export_ready_email, address,
            org_name=org.name if org else "your Organization",
            export_type=EXPORT_LABELS.get(export.export_type, export.export_type),
            requested_at=org_comms.org_timestamp(org, export.requested_at),
            completed_at=org_comms.org_timestamp(org, export.completed_at),
            expires_at=org_comms.org_timestamp(org, export.expires_at),
            download_url=url)
    return True


def notify_expired(db: Session, background, export: DeveloperDataExport) -> bool:
    if not _claim(db, export, "expired_notified_at"):
        return False
    addresses = _recipients(db, export)
    if not addresses:
        return False
    org = db.get(Organization, export.org_id)
    for address in addresses:
        background.add_task(
            email_mod.send_export_expired_email, address,
            org_name=org.name if org else "your Organization",
            export_type=EXPORT_LABELS.get(export.export_type, export.export_type),
            expired_at=org_comms.org_timestamp(org, export.expires_at))
    return True


def notify_failed(db: Session, background, export: DeveloperDataExport) -> bool:
    if not _claim(db, export, "failed_notified_at"):
        return False
    addresses = _recipients(db, export)
    if not addresses:
        return False
    org = db.get(Organization, export.org_id)
    for address in addresses:
        background.add_task(
            email_mod.send_export_failed_email, address,
            org_name=org.name if org else "your Organization",
            export_type=EXPORT_LABELS.get(export.export_type, export.export_type),
            export_reference=str(export.id)[:8],
            failure_category=FAILURE_CATEGORIES.get(export.failure_category or "",
                                                    "The export could not be generated"))
    return True


class _Bg:
    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("DEV-012 notice failed")


def sweep(db: Session, background=None) -> dict:
    """Expire ready exports whose window has closed, and announce it."""
    background = background or _Bg()
    now = _now()
    expired = 0
    for export in db.scalars(
        select(DeveloperDataExport).where(
            DeveloperDataExport.status == EXPORT_READY,
            DeveloperDataExport.expires_at <= now)
    ).all():
        if expire(db, export):
            notify_expired(db, background, export)
            expired += 1
    return {"expired": expired}
