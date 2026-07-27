"""Shared state for the "raise hand" / "promote to speaker" flow, used by both the
realtime layer (sockets.py) and the REST moderation endpoints (routers/stage.py).

State is in-process and ephemeral (module-level dicts, not persisted) -- consistent
with how this app already treats live-event state (no Redis anywhere else either).
If the server restarts mid-event, hands/speakers are just re-raised/re-promoted.
"""
import uuid

from app.models.user import User

MANAGER_ROLES = ("org_admin", "host", "moderator")

# stream_id -> {identity: display_name}
HAND_QUEUE: dict[str, dict[str, str]] = {}
ON_STAGE: dict[str, dict[str, str]] = {}

MAX_SPEAKERS = 6


def resolve_identity(user: User | None, email: str | None) -> str:
    """The LiveKit participant identity for a given caller -- must be computed the same
    way everywhere (token minting, socket join, promote-by-email) so they all agree on
    who "this person" is.
    """
    if user:
        return str(user.id)
    if email:
        return f"guest:{email.strip().lower()}"
    return f"guest:{uuid.uuid4()}"


def stage_room_for(stream_id) -> str:
    return f"stage:{stream_id}"


def stage_snapshot(stream_id: str) -> dict:
    return {
        "hands": [{"identity": i, "display_name": n} for i, n in HAND_QUEUE.get(stream_id, {}).items()],
        "roster": [{"identity": i, "display_name": n} for i, n in ON_STAGE.get(stream_id, {}).items()],
    }
