"""Enforces Stream.registration_required at the points where it actually matters:
joining the live room (routers/streams.py) and joining live chat (sockets.py).
Reading public info about an event (GET /streams/{id}, chat history) stays ungated --
registration is a gate on *participating*, not on deciding whether to register."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.registration import Registration
from app.models.stream import Stream
from app.models.user import User


def is_registered(db: Session, stream: Stream, user: User | None, email: str | None) -> bool:
    """Whether this caller may join a registration_required event.

    Org members watching their own org's event never need to register -- they're
    running it. Everyone else needs a Registration row with a matching email: the
    `email` argument for guests, or the caller's own account email for logged-in
    non-org viewers.
    """
    if not stream.registration_required:
        return True
    if user and user.org_id == stream.org_id:
        return True

    candidate = (email or (user.email if user else None) or "").strip().lower()
    if not candidate:
        return False

    return (
        db.scalar(
            select(Registration.id).where(Registration.stream_id == stream.id, Registration.email == candidate)
        )
        is not None
    )
