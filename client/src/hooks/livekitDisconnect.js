import { DisconnectReason } from "livekit-client";

// Disconnect reasons a reconnect attempt cannot fix — shared by useLiveKitPublish and
// useLiveKitViewer, which otherwise retried every Disconnected identically.
//
// DUPLICATE_IDENTITY is the one that mattered: LiveKit allows one connection per identity
// and evicts the older one, so two connections on the same identity each saw a disconnect,
// each reconnected, and each eviction re-triggered the other — burning both retry budgets
// in seconds and leaving BOTH sides on "couldn't reconnect". The server side no longer
// hands out colliding identities (services/livekit.py's secondary()), but two tabs of the
// same watch page still can, and retrying there just kicks the other tab. Say so instead.
const FATAL = {
  [DisconnectReason.DUPLICATE_IDENTITY]:
    "You're already connected to this event in another tab or window. Close it, then reload here.",
  [DisconnectReason.PARTICIPANT_REMOVED]: "You were removed from this event by the host.",
  [DisconnectReason.ROOM_DELETED]: "This broadcast has ended.",
};

export function fatalDisconnect(reason) {
  return FATAL[reason] || null;
}
