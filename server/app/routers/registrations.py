from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.email import send_registration_confirmation_email
from app.models import User
from app.models.registration import Registration
from app.models.stream import Stream
from app.schemas.registration import BulkRegisterIn, BulkRegisterResult, RegistrantIn, RegistrationOut
from app.security import get_current_user, get_optional_user

router = APIRouter(prefix="/streams/{stream_id}/registrations", tags=["Registrations"])

MANAGER_ROLES = ("org_admin", "host")


def _require_manager(user: User) -> None:
    if user.role not in MANAGER_ROLES:
        raise HTTPException(403, "Only organization admins and hosts can manage registrations")


def _get_stream(db: Session, stream_id: str, user: User | None = None) -> Stream:
    stream = db.get(Stream, stream_id)
    if not stream or not stream.visible_to(user):
        raise HTTPException(404, "Event not found")
    return stream


def _get_org_stream(db: Session, user: User, stream_id: str) -> Stream:
    stream = db.scalar(select(Stream).where(Stream.id == stream_id, Stream.org_id == user.org_id))
    if not stream:
        raise HTTPException(404, "Event not found")
    return stream


# SELF-SERVICE REGISTER
# Public, gated by Stream.visible_to() like GET /streams/{id}.
# ponytail: doesn't yet check that the event is actually open for registration --
# registration_required is informational only right now; anyone who can see the event
# can register regardless of that flag.
@router.post("", response_model=RegistrationOut, status_code=201)
def register(
    stream_id: str,
    data: RegistrantIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    stream = _get_stream(db, stream_id, user)
    email = data.email.lower()

    if db.scalar(select(Registration).where(Registration.stream_id == stream_id, Registration.email == email)):
        raise HTTPException(409, "This email is already registered for this event")

    registration = Registration(
        stream_id=stream_id,
        full_name=data.full_name,
        email=email,
        phone=data.phone,
        company=data.company,
        source="self",
    )
    db.add(registration)
    db.commit()
    db.refresh(registration)

    background.add_task(send_registration_confirmation_email, email, data.full_name, stream.title, stream_id)
    return registration


# LIST REGISTRANTS (org-scoped)
@router.get("", response_model=list[RegistrationOut])
def list_registrations(stream_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_manager(user)
    _get_org_stream(db, user, stream_id)

    return db.scalars(
        select(Registration).where(Registration.stream_id == stream_id).order_by(Registration.created_at.desc())
    ).all()


# BULK IMPORT (org-scoped) -- the "invite 100 people" path: paste/import a list instead
# of each attendee filling out the public form themselves. Duplicates (already registered,
# or repeated within the same batch) are skipped rather than erroring the whole import.
@router.post("/bulk", response_model=BulkRegisterResult)
def bulk_register(
    stream_id: str,
    data: BulkRegisterIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_manager(user)
    stream = _get_org_stream(db, user, stream_id)

    existing_emails = set(db.scalars(select(Registration.email).where(Registration.stream_id == stream_id)))

    created = []
    skipped = []
    seen = set()
    for entrant in data.registrants:
        email = entrant.email.lower()
        if email in existing_emails or email in seen:
            skipped.append(email)
            continue
        seen.add(email)
        registration = Registration(
            stream_id=stream_id,
            full_name=entrant.full_name,
            email=email,
            phone=entrant.phone,
            company=entrant.company,
            source="bulk",
        )
        db.add(registration)
        created.append(registration)

    db.commit()
    for registration in created:
        db.refresh(registration)
        background.add_task(
            send_registration_confirmation_email, registration.email, registration.full_name, stream.title, stream_id
        )

    return {"created": created, "skipped": skipped}
