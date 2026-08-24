"""Public, unauthenticated response shapes for /deliveries/{token} — deliberately separate
from schemas/organization.py's org-admin shapes (different trust boundary: these are what
an external, non-platform recipient sees)."""

from datetime import datetime

from pydantic import BaseModel


class PublicDeliveryOut(BaseModel):
    kind: str  # export | report
    event_title: str
    expires_at: datetime | None = None
    # kind="report" only — the report snapshot itself, rendered directly (small JSON, no
    # separate fetch needed). kind="export" leaves this None; the file comes from a
    # separate POST .../download call that mints a fresh short-lived signed URL.
    report: dict | None = None
    # kind="export" only — pending|ready|failed (models.live.WATERMARK_STATUSES). Lets the
    # landing page show "still preparing" instead of a dead download button.
    watermark_status: str | None = None


class DownloadOut(BaseModel):
    download_url: str
