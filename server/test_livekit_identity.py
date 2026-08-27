"""Self-check for the LiveKit identity rule: one connection per identity.

A LiveKit room evicts an existing participant when a second one joins with the same
identity. Every token minted for a logged-in user used identity=str(user.id), so a host
running the console while also watching, and every speaker on the Backstage page (which
opens a publisher AND a return-feed subscriber), had two connections kicking each other in
a loop until both retry budgets ran out — "Lost connection to the stream and couldn't
reconnect" on the host and the viewer simultaneously.

What must stay true:
  * no two connections one user can hold AT THE SAME TIME share an identity,
  * the connections that ARE moderation targets keep the bare presence identity, since
    mute / promote-to-speaker / remove address LiveKit by exactly that string,
  * a tagged identity still resolves back to its presence record, or the LiveKit webhook
    would file a host's `publishing` flag under a phantom participant and health would
    report "No media is being published" over a working broadcast.

Pure logic — no database, no Redis, no LiveKit. Run: `python test_livekit_identity.py`
(or pytest)."""
import jwt

from app.config import settings
from app.services import livekit as lk


def _identity_of(token: str) -> str:
    """The `sub` claim is the participant identity LiveKit enforces on."""
    return jwt.decode(token, settings.LIVEKIT_API_SECRET, algorithms=["HS256"],
                      options={"verify_aud": False})["sub"]


def _configure():
    """Token minting needs a key/secret; every environment that actually streams has them
    (services/livekit.configured()), dev and CI don't."""
    settings.LIVEKIT_API_KEY = settings.LIVEKIT_API_KEY or "devkey"
    settings.LIVEKIT_API_SECRET = settings.LIVEKIT_API_SECRET or "devsecret-at-least-32-chars-long!"


def test_tagged_identity_resolves_back_to_its_owner():
    user = "3f1c9a0e-0000-4000-8000-000000000001"
    assert lk.primary(lk.secondary(user, "host")) == user
    assert lk.primary(lk.secondary(user, "monitor")) == user
    # Untagged identities pass through — the webhook maps every participant through this,
    # including plain viewers and ingress endpoints.
    for untagged in (user, f"guest-{user}", f"guest-link-{user}", f"viewer-{user}", f"ingress-{user}"):
        assert lk.primary(untagged) == untagged


def test_concurrent_connections_never_share_an_identity():
    """The actual eviction condition. `presence` is what moderation.Ctx.identity would be."""
    _configure()
    presence = "3f1c9a0e-0000-4000-8000-000000000001"
    room = f"event_{presence}"

    # The three tokens one user can hold at once, minted exactly as their call sites do
    # (services/broadcast.py, services/contributor.py, routers/events.py).
    host_console = lk.create_stream_token(lk.secondary(presence, "host"), room, True)
    backstage_monitor = lk.create_stream_token(lk.secondary(presence, "monitor"), room, False)
    audience = lk.create_stream_token(presence, room, False)

    identities = [_identity_of(t) for t in (host_console, backstage_monitor, audience)]
    assert len(set(identities)) == 3, f"colliding LiveKit identities: {identities}"


def test_moderation_targets_keep_the_bare_identity():
    """Promote to Speaker / Mute / Remove call livekit.py with the PRESENCE identity, so the
    connection they govern must be reachable under it. Tagging the audience token (or the
    contributor's publish token) would silently address a participant that doesn't exist."""
    _configure()
    presence = "3f1c9a0e-0000-4000-8000-000000000001"
    room = f"event_{presence}"
    # routers/events.py's playback token and services/contributor.py's my_publish_token.
    assert _identity_of(lk.create_stream_token(presence, room, False)) == presence
    assert _identity_of(lk.create_stream_token(presence, room, True)) == presence


if __name__ == "__main__":
    test_tagged_identity_resolves_back_to_its_owner()
    test_concurrent_connections_never_share_an_identity()
    test_moderation_targets_keep_the_bare_identity()
    print("ok")
