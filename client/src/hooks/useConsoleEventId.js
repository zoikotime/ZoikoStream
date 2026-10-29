import { useEffect } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";

// The event an event console is for. The Producer Console has two URLs for one view:
//   /host/live/:eventId        while the event's broadcast is live (or paused)
//   /host/dashboard?event=<id> otherwise — preparation, after it ends, and every existing
//                              link, email and bookmark
// (App.jsx). Both name the same event, so the guard and the live connection read it the same
// way.
export default function useConsoleEventId() {
  const { eventId } = useParams();
  const [params] = useSearchParams();
  return eventId || params.get("event");
}

export const hostConsolePath = (eventId, live) => {
  const id = encodeURIComponent(eventId);
  return live ? `/host/live/${id}` : `/host/dashboard?event=${id}`;
};

// Keeps the Producer Console's URL saying whether it is on air (pages/host/Dashboard.jsx).
//
// Waits for `ready` — the live snapshot that says what the broadcast is doing — so a refresh
// mid-broadcast does not bounce through the dashboard URL first, and a direct visit to
// /host/live/<id> for an event that is not on air settles on /host/dashboard?event=<id>
// instead of claiming a live session. Replaces rather than pushes: Back leaves the console
// instead of toggling between two URLs for one view. Other query parameters are kept. Both
// routes render the same console in the same place, so the change does not remount it — the
// socket and the LiveKit room stay connected (hostLiveRoute.test.jsx).
export function useHostConsoleUrl({ ready, onAir }) {
  const eventId = useConsoleEventId();
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (!ready || !eventId) return;
    const rest = new URLSearchParams(location.search);
    rest.delete("event");
    const base = hostConsolePath(eventId, onAir);
    const extra = rest.toString();
    const want = extra ? `${base}${base.includes("?") ? "&" : "?"}${extra}` : base;
    if (`${location.pathname}${location.search}` !== want) navigate(`${want}${location.hash}`, { replace: true });
  }, [ready, onAir, eventId, location.pathname, location.search, location.hash, navigate]);
}
