"""Public, unauthenticated resolution of a controlled customer export or a released
event report — /deliveries/{token}. No org/auth dependency at all: the bearer token
itself (crud.delivery.find_delivery_by_token) is the only credential, same posture as
routers/events.py's access-link `link` query param, just as its own dedicated surface
since a delivery recipient is never a platform user."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..crud import admin as admin_crud
from ..crud import delivery as crud
from ..db import get_db
from ..models import Event, EventReport
from ..schemas.delivery import DownloadOut, PublicDeliveryOut
from ..services import livekit

router = APIRouter(prefix="/deliveries", tags=["deliveries"])

_NOT_FOUND = "This link is invalid, expired, or has been revoked."


@router.get("/{token}", response_model=PublicDeliveryOut)
def resolve_delivery(token: str, db: Session = Depends(get_db)):
    delivery = crud.find_delivery_by_token(db, token)
    if delivery is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    ev = db.get(Event, delivery.event_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    admin_crud.create_audit_log(
        db, actor=None, action=f"{delivery.kind}.access", target_type="customer_delivery",
        target_id=delivery.id, org_id=delivery.org_id, meta={"event_id": str(delivery.event_id)},
    )

    report_data = None
    if delivery.kind == "report" and delivery.report_id:
        report = db.get(EventReport, delivery.report_id)
        report_data = report.data if report else None

    return PublicDeliveryOut(
        kind=delivery.kind, event_title=ev.title or "Untitled event",
        expires_at=delivery.expires_at, report=report_data,
        watermark_status=delivery.watermark_status if delivery.kind == "export" else None,
    )


@router.post("/{token}/download", response_model=DownloadOut)
def download_export(token: str, db: Session = Depends(get_db)):
    """Export only. Mints a fresh, short-lived signed GCS URL on demand — never persisted,
    never embedded in the resolve response above. This is what keeps a controlled export
    from ever being a durable public download link (BRD LE-AC-18).

    Always signs watermarked_file_key, never the original recording.file_url — an
    unwatermarked delivery would be a compliance gap (BRD "policy watermark", LE-AC-12),
    so this 425s until services/delivery.py's ticker has actually produced that copy."""
    delivery = crud.find_delivery_by_token(db, token)
    if delivery is None or delivery.kind != "export":
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)

    if delivery.watermark_status == "failed":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "This export couldn't be prepared. Contact the organizer for a new link.")
    if delivery.watermark_status != "ready" or not delivery.watermarked_file_key:
        raise HTTPException(status.HTTP_425_TOO_EARLY,
                            "This recording is still being prepared — try again in a few minutes.")

    url = livekit.signed_url(delivery.watermarked_file_key, expires_minutes=30)
    if url is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't prepare the download right now — try again shortly.")

    admin_crud.create_audit_log(
        db, actor=None, action="export.download", target_type="customer_delivery",
        target_id=delivery.id, org_id=delivery.org_id, meta={"event_id": str(delivery.event_id)},
    )
    return DownloadOut(download_url=url)
