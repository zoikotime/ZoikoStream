from livekit import api

from app.config import settings


def create_stream_token(
    identity: str,
    room_name: str,
    can_publish: bool,
    name: str | None = None,
):

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=True
    )


    token = api.AccessToken(
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET
    )


    token.with_identity(identity)
    token.with_grants(grant)
    if name:
        token.with_name(name)


    return token.to_jwt()


async def update_publish_permission(room_name: str, identity: str, can_publish: bool) -> None:
    """Live-upgrade/downgrade an already-connected participant's publish grant, without
    requiring them to reconnect with a new token. Best-effort: swallows a "participant
    not found" error since the caller (routers/stage.py) treats the ON_STAGE/HAND_QUEUE
    state as the source of truth and this is just the fast path for someone already
    connected -- a not-yet-connected invitee picks up the permission from get_viewer_token
    instead once they do join.
    """
    lkapi = api.LiveKitAPI(
        settings.LIVEKIT_URL,
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    )
    try:
        await lkapi.room.update_participant(
            api.UpdateParticipantRequest(
                room=room_name,
                identity=identity,
                permission=api.ParticipantPermission(
                    can_subscribe=True,
                    can_publish=can_publish,
                    can_publish_data=True,
                ),
            )
        )
    except api.TwirpError:
        pass
    finally:
        await lkapi.aclose()