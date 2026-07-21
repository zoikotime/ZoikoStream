from livekit import api

from app.config import settings


def create_stream_token(
    identity: str,
    room_name: str,
    can_publish: bool
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


    return token.to_jwt()