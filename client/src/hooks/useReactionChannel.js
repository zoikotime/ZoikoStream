// client/src/hooks/useReactionChannel.js
// A tiny publish/subscribe seam between "a reaction envelope arrived on the socket" and
// "an emoji floats over the video".
//
// WHY THIS ISN'T REACT STATE. Reactions are the highest-frequency thing on either live
// surface. If the burst list lived in the page's own state (or in useLiveEvent's reducer),
// the host's Producer Console would re-render once per tap — the monitor, the control deck,
// the KPI row, the whole panel rail — while an audience is reacting. The channel's identity
// is stable for the life of the page, so emitting through it changes nothing React can see;
// the only component that re-renders is components/live/ReactionOverlay.jsx, which
// subscribes and owns the burst list itself.
//
// It is also NOT the transport. The transport is the existing live WebSocket + event bus
// (server/app/services/bus.py, Redis-backed across workers); this only distributes
// envelopes that already arrived from the server, which is what makes a reaction from a
// viewer served by one Cloud Run instance reach a host served by another.
import { useMemo } from "react";

/**
 * Standalone factory — exported for tests and any non-React caller, so an overlay can be
 * driven without rendering a page or standing up a socket.
 *
 * @returns {{
 *   emit: (reaction: object) => void,
 *   subscribe: (fn: (reaction: object) => void) => () => void,
 * }}
 */
export function createReactionChannel() {
  const subscribers = new Set();
  return {
    /** Fan one server-sent reaction payload out to whichever overlays are mounted. */
    emit(reaction) {
      if (!reaction) return;
      for (const fn of subscribers) fn(reaction);
    },
    /** @returns an unsubscribe function, so an unmounting overlay detaches cleanly. */
    subscribe(fn) {
      subscribers.add(fn);
      return () => subscribers.delete(fn);
    },
  };
}

/** One channel per page, created once. The stable identity is the whole point — see above. */
export default function useReactionChannel() {
  return useMemo(() => createReactionChannel(), []);
}
