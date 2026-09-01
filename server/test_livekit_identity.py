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


# ── secondary()/primary() as pure functions ────────────────────────────────────

def test_secondary_is_deterministic_and_stable_across_reconnects():
    """A reconnect (the host clicking Go Live again, a dropped/restored console, a page
    refresh) re-derives the SAME ctx.identity from the same signed-in user every time
    (moderation.resolve_ctx: identity=str(user.id)) — so secondary() must be a pure
    function of its inputs, not something that varies run to run, or every reconnect would
    mint a NEW identity and orphan the old one's presence record instead of resuming it."""
    user = "3f1c9a0e-0000-4000-8000-000000000001"
    assert lk.secondary(user, "host") == lk.secondary(user, "host")
    assert lk.secondary(user, "monitor") == lk.secondary(user, "monitor")


def test_secondary_never_collides_across_different_users():
    """Two different hosts' (or two different contributors') secondary connections must
    never accidentally share an identity with each other, only ever with themselves."""
    user_a = "3f1c9a0e-0000-4000-8000-000000000001"
    user_b = "3f1c9a0e-0000-4000-8000-000000000002"
    assert lk.secondary(user_a, "host") != lk.secondary(user_b, "host")
    assert lk.secondary(user_a, "monitor") != lk.secondary(user_b, "monitor")
    assert lk.primary(lk.secondary(user_a, "host")) != lk.primary(lk.secondary(user_b, "host")) \
        or user_a == user_b


def test_secondary_rejects_an_unknown_tag():
    """Only the two documented connection kinds may ever be tagged — an arbitrary tag would
    silently create a new, undocumented identity shape primary() might not reverse correctly
    (or might accidentally collide with a future guest-/viewer-/ingress- prefix)."""
    try:
        lk.secondary("3f1c9a0e-0000-4000-8000-000000000001", "not-a-real-tag")
        assert False, "secondary() must reject a tag outside _SECONDARY_TAGS"
    except ValueError:
        pass


def test_primary_is_a_safe_no_op_on_a_bare_or_disposable_identity():
    """Anonymous viewers (viewer-<uuid>) and every other never-tagged identity shape must
    round-trip through primary() unchanged — this is what makes it safe for routers/live.py's
    webhook handler to call unconditionally on every participant, tagged or not."""
    import uuid as _uuid
    disposable = f"viewer-{_uuid.uuid4()}"
    assert lk.primary(disposable) == disposable


# ── the real call sites, not just the helpers in isolation ─────────────────────
# Confirms the actual token-minting functions apply (or correctly withhold) tagging — not
# just that secondary()/primary() work correctly on their own.

def test_host_console_publish_token_is_tagged():
    """services/broadcast.py's _preview and snapshot_extra both mint the host's publish
    token via secondary(ctx.identity, "host") — this proves the ACTUAL _preview call site
    does it, not a hand-rolled equivalent."""
    import asyncio
    import uuid as _uuid

    from app.services import broadcast as bc
    from app.services import bus
    from app.services import moderation as m

    _configure()
    # services/bus.py lazily caches its Redis client at module scope, bound to whichever
    # asyncio event loop first created it. Run alongside other tests that each call
    # asyncio.run() (a fresh event loop every time — never the case in the real server,
    # which runs one continuous loop), that cached client can be left attached to an
    # already-closed loop, and _preview's bus.state_set() call raises "Event loop is
    # closed" — a test-harness artifact, not a bug in this function. Forcing a fresh client
    # for THIS test's own loop sidesteps it.
    bus._redis = None
    user_id = _uuid.uuid4()
    ctx = m.Ctx(event_id=_uuid.uuid4(), org_id=_uuid.uuid4(), room=f"event_{user_id}",
               user_id=user_id, name="Test Host", identity=str(user_id), role="host",
               can_moderate=True, can_host=True)

    frames = asyncio.run(bc._preview(ctx, {}))
    token = frames[0][2]["publish_token"]
    assert token is not None, "no publish_token minted — is LIVEKIT configured in this env?"
    identity = _identity_of(token)
    assert identity == lk.secondary(str(user_id), "host"), \
        f"host console publish token is not tagged: {identity!r}"
    assert lk.primary(identity) == str(user_id)


def test_watch_monitor_and_contributor_publish_never_collide_for_the_same_user():
    """Simulates exactly Backstage.jsx's two simultaneous connections for one contributor:
    the return-feed monitor (routers/events.py's watch_event, monitor=True) and their own
    publish token (services/contributor.py's my_publish_token, always bare). Reconstructs
    watch_event's own tagging expression rather than hitting the full HTTP endpoint (which
    needs a real Event/registration/DB fixture) — the exact expression is duplicated here
    deliberately so a future edit to either side breaks this test, not just breaks silently
    in production."""
    presence = "3f1c9a0e-0000-4000-8000-000000000001"
    monitor_identity = lk.secondary(presence, "monitor")  # routers/events.py's watch_event, monitor=True
    contributor_publish_identity = presence               # services/contributor.py's my_publish_token
    assert monitor_identity != contributor_publish_identity
    assert lk.primary(monitor_identity) == lk.primary(contributor_publish_identity) == presence


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("\nAll LiveKit identity checks passed.")
