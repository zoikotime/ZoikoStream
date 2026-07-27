"""Records unique-viewer counts for the org/platform dashboards' total_viewers stat.

This is a vanity metric, not exact concurrent-viewership: a logged-in user or a guest
who registered (viewer_key = their id/email) is deduped across repeat visits, but an
anonymous, unregistered guest has no stable identity to dedupe on, so each successful
token fetch counts as a new view for them. Approximate, but a real signal instead of
the flat 0 / hardcoded numbers this replaces.
"""
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.view import StreamView


def _viewer_key(user: User | None, email: str | None) -> str:
    if user:
        return f"user:{user.id}"
    if email:
        return f"email:{email.strip().lower()}"
    return f"anon:{uuid.uuid4()}"


def record_view(db: Session, stream_id, user: User | None, email: str | None) -> None:
    db.add(StreamView(stream_id=stream_id, viewer_key=_viewer_key(user, email)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # already recorded this viewer for this stream
