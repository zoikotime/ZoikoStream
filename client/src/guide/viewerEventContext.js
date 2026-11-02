// On a viewer page, fetch the signed public event context for Guide (server:
// routers/guide_event_context.py) and publish it through pageContext.js, so Guide can answer
// "how long is this event?", "who is hosting?" and the like.
//
// The request carries exactly what the watch page itself presents — the viewer's stored invite
// (`zk_reg_<id>`) or access-link pass (`zk_link_<id>`), plus the platform session if signed
// in — so Guide only ever learns about an event the viewer may already watch. Any failure just
// leaves Guide on generic viewer help.
import api from "../api";
import { setGuideEventContextToken } from "./pageContext";

const WATCH = /^\/events\/([0-9a-f-]{36})\/watch\/?$/i;
const REFRESH_MARGIN_S = 120;
const RETRY_DELAYS_S = [2, 5, 15, 30, 60];

export function viewerEventId(pathname) {
  const m = WATCH.exec(pathname || "");
  return m ? m[1] : null;
}

function stored(key) {
  try {
    return localStorage.getItem(key) || sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

// Returns a cancel function. `onToken` runs whenever a fresh token is published.
export function watchViewerEventContext(eventId, onToken) {
  let timer = null;
  let cancelled = false;
  let attempt = 0;
  const load = () => {
    const reg = stored(`zk_reg_${eventId}`);
    const link = stored(`zk_link_${eventId}`);
    api
      // POST body, not the query string: the credential must never reach a URL (access logs).
      .post(`/guide/event-context/${eventId}`, { ...(reg ? { reg } : {}), ...(link ? { link } : {}) })
      .then(({ data }) => {
        if (cancelled || !data?.token) return;
        attempt = 0;
        setGuideEventContextToken(data.token);
        onToken?.(data.token);
        const wait = Math.max(60, (data.expires_in || 3600) - REFRESH_MARGIN_S) * 1000;
        timer = setTimeout(load, wait);
      })
      .catch(() => {
        if (cancelled) return;
        setGuideEventContextToken(null);
        // A private event's first request can arrive before the watch page has claimed the
        // viewer's registration (403): try again shortly, then back off. A viewer who is
        // genuinely not allowed stops after the last attempt.
        if (attempt < RETRY_DELAYS_S.length) timer = setTimeout(load, RETRY_DELAYS_S[attempt++] * 1000);
      });
  };
  load();
  return () => {
    cancelled = true;
    clearTimeout(timer);
    setGuideEventContextToken(null);
  };
}
