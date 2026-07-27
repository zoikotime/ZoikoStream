"""In-process live-viewer presence, used by sockets.py to drive the watch page's
"N watching" badge. Same ephemeral, module-level-dict approach as services/stage.py's
HAND_QUEUE/ON_STAGE -- no Redis anywhere else in this app either, so this doesn't
survive a restart or work across multiple server processes.

Keyed by *identity* (see services/stage.resolve_identity), not socket id, and one
identity can hold several sids at once -- a viewer with the watch page's chat panel and
video player both connected (two separate socket.io connections today) still counts
once, and so does the same viewer open in two browser tabs. The count only drops once
every sid for that identity has disconnected.

Org admins/hosts/moderators are deliberately never added here (see sockets.py) --
this is meant to read as "how big is the audience", not "how many sockets are open".
"""

# stream_id -> {identity: {sid, ...}}
_PRESENCE: dict[str, dict[str, set[str]]] = {}


def add_viewer(stream_id: str, identity: str, sid: str) -> int:
    sids = _PRESENCE.setdefault(stream_id, {}).setdefault(identity, set())
    sids.add(sid)
    return viewer_count(stream_id)


def remove_viewer(stream_id: str, identity: str, sid: str) -> int:
    viewers = _PRESENCE.get(stream_id)
    if not viewers or identity not in viewers:
        return viewer_count(stream_id)

    viewers[identity].discard(sid)
    if not viewers[identity]:
        del viewers[identity]
    if not viewers:
        _PRESENCE.pop(stream_id, None)
    return viewer_count(stream_id)


def viewer_count(stream_id: str) -> int:
    return len(_PRESENCE.get(stream_id, {}))
