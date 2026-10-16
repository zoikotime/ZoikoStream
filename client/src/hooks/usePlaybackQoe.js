import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { API_BASE } from "../apiBase";
import { PlaybackQoeSession } from "../data/playbackQoe";

// Runs ONE playback session's quality measurement (data/playbackQoe.js) and reports it to
// POST /api/playback/qoe (server/app/routers/playback.py).
//
// A session begins the first time the player is `active` with a `token` (GET /events/:id/watch
// hands one to audience members only, alongside the media) and ends when it stops being
// active or the component unmounts. It keeps the token it began with: the watch page re-polls
// and every answer carries a fresh token, but a new token is not a new session — only a new
// page or a new mode (live -> replay) is. Re-renders, reconnects and track resubscriptions
// change the inputs, never the session, so they cannot add a second start.
//
// Volume: nothing is sent until something happened (playback started, or a failure). Then a
// beacon goes out at once for the start and for each new failure kind, every FLUSH_EVERY_MS
// while the totals are moving, when the tab is hidden, and once more when the viewer leaves.
// Each beacon carries the session's CUMULATIVE totals and a rising `seq`, so the server can
// apply it idempotently: a lost beacon is covered by the next, a retried one changes nothing.
//
// Sent as text/plain (a request that needs no CORS preflight), well under the server's 4 KB
// limit. Credentials are omitted — the token is the only authority, and nothing about the
// viewer's account travels with it. The beacons that may be the page's last — the tab being
// hidden (a phone may kill a hidden page without another event), pagehide and the session
// ending — use fetch(keepalive), which outlives the page; where fetch has no real keepalive,
// or refuses the request outright, they fall back to navigator.sendBeacon with the same
// text/plain body. sendBeacon cannot omit cookies, so it is only ever the fallback.

export const FLUSH_EVERY_MS = 60_000;
const FAILURE_FLUSH_DELAY_MS = 1_000;

export const QOE_URL = `${API_BASE}/api/playback/qoe`;
const CONTENT_TYPE = "text/plain;charset=UTF-8";

// Whether this browser's fetch honours `keepalive`. Firefox before 133 accepted the flag and
// dropped it — and had no Request#keepalive attribute, which is what gives that away.
const fetchKeepsAlive = () => typeof fetch === "function" && typeof Request === "function"
  && "keepalive" in Request.prototype;

/** navigator.sendBeacon, true when the browser queued it. */
function sendBeacon(text) {
  try {
    if (typeof navigator === "undefined" || typeof navigator.sendBeacon !== "function") return false;
    return navigator.sendBeacon(QOE_URL, new Blob([text], { type: CONTENT_TYPE })) === true;
  } catch {
    return false;
  }
}

/** Sends one beacon. Returns the fetch's promise, or null when it went as a sendBeacon (fire
 *  and forget: there is no response to read). Throws when nothing could be sent. */
function send(text, keepalive) {
  if (keepalive && !fetchKeepsAlive() && sendBeacon(text)) return null;
  try {
    return fetch(QOE_URL, {
      method: "POST",
      keepalive,
      credentials: "omit",
      headers: { "Content-Type": CONTENT_TYPE },
      body: text,
    });
  } catch (error) {
    if (keepalive && sendBeacon(text)) return null;
    throw error;
  }
}

// `final` = the viewer is leaving (an open stall counts); `keepalive` = the request must
// survive the page going away. Every final flush is a keepalive one.
function flush(run, { final = false, keepalive = final } = {}) {
  if (!run || run.disabled) return;
  const snapshot = run.session.snapshot(performance.now(), { final });
  // Nothing worth a request: a page that never played and never failed.
  if (!snapshot.started && snapshot.failures.length === 0) return;
  const key = JSON.stringify(snapshot);
  if (key === run.lastKey) return;
  run.lastKey = key;
  run.seq += 1;
  let request;
  try {
    request = send(JSON.stringify({ token: run.token, seq: run.seq, ...snapshot }), keepalive);
  } catch {
    run.lastKey = null;
    return;
  }
  if (request === null) return;
  Promise.resolve(request)
    .then((res) => {
      // The server refused this session for good (expired or invalid token, malformed
      // beacon, event gone). 429 is a pace problem, not a refusal.
      if (res && res.status >= 400 && res.status < 500 && res.status !== 429) run.disabled = true;
      else if (res && !res.ok) run.lastKey = null;
    })
    .catch(() => {
      // Network: the next beacon carries the same totals with a higher seq.
      run.lastKey = null;
    });
}

/** Whether the viewer can see the player: the tab is visible and the element is on screen. */
export function usePlayerVisible(ref) {
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden",
  );
  const [onScreen, setOnScreen] = useState(true);
  useEffect(() => {
    const onChange = () => setPageVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  useEffect(() => {
    const el = ref.current;
    // Without IntersectionObserver the element is treated as on screen.
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    // LiveKit's adaptive stream deliberately pauses video nobody can see, so an off-screen
    // player is treated like a hidden tab rather than as a stall.
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) setOnScreen(entry.isIntersecting);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return pageVisible && onScreen;
}

export default function usePlaybackQoe({ token, active, intent, visible, interrupted = false,
                                         seeking = false, flowing, ended = false }) {
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);
  const runRef = useRef(null);
  // The player's latest facts, for the two moments the session must act on them ahead of the
  // facts effect below: the tab being hidden, and the session ending. A layout effect, so it
  // is current before any passive effect or cleanup of the same commit reads it.
  const factsRef = useRef(null);
  useLayoutEffect(() => {
    factsRef.current = { intent, visible, interrupted, seeking, flowing, ended };
  }, [intent, visible, interrupted, seeking, flowing, ended]);
  // The token of the last session that reported anything. A token measures ONE session: the
  // server keys the row on it and ignores a restarted sequence, so a player re-activated on a
  // token it already reported with stays unmeasured rather than blending two runs' totals.
  const reportedTokenRef = useRef(null);
  const live = Boolean(active && token);

  // The session's lifetime.
  useEffect(() => {
    if (!live || tokenRef.current === reportedTokenRef.current) return undefined;
    const run = {
      token: tokenRef.current,
      session: new PlaybackQoeSession(performance.now()),
      seq: 0,
      lastKey: null,
      disabled: false,
      failTimer: null,
    };
    runRef.current = run;
    const interval = setInterval(() => flush(run), FLUSH_EVERY_MS);
    const onVisibility = () => {
      if (document.visibilityState !== "hidden") return;
      // Hidden NOW, before React has rendered it: a hidden page may be frozen or killed with
      // no further event. So the session moves to `hidden` here (closing, and counting, a
      // stall the viewer sat through until they switched away), and its totals go out on a
      // request that outlives the page. The re-render that follows changes nothing.
      if (factsRef.current) run.session.update({ ...factsRef.current, visible: false }, performance.now());
      flush(run, { keepalive: true });
    };
    const onPageHide = () => flush(run, { final: true });
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      clearInterval(interval);
      clearTimeout(run.failTimer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      // The session closes on the facts of the commit that ended it, which the facts effect
      // never gets to apply (this cleanup runs first and detaches the run). A player that stops
      // being active BECAUSE the stream ended — the event completed under a viewer who was
      // mid-reconnect — therefore closes as `ended`, and the stall it was in is not counted.
      if (factsRef.current) run.session.update(factsRef.current, performance.now());
      flush(run, { final: true });
      // A session that never sent anything leaves its token free (React StrictMode's
      // mount-unmount-mount in development is exactly that).
      if (run.seq > 0) reportedTokenRef.current = run.token;
      if (runRef.current === run) runRef.current = null;
    };
  }, [live]);

  // The player's facts. Declared after the lifetime effect so a session created in this
  // commit sees its first inputs in the same commit.
  useEffect(() => {
    const run = runRef.current;
    if (!run) return;
    const started = run.session.update({ intent, visible, interrupted, seeking, flowing, ended },
                                       performance.now());
    if (started || ended) flush(run);
  }, [live, intent, visible, interrupted, seeking, flowing, ended]);

  const fail = useCallback((reason) => {
    const run = runRef.current;
    if (!run || !run.session.fail(reason)) return;
    // Batched briefly: one failure often arrives with another (a play() rejection and a
    // media error), and they belong in one beacon.
    clearTimeout(run.failTimer);
    run.failTimer = setTimeout(() => flush(run), FAILURE_FLUSH_DELAY_MS);
  }, []);

  return { fail };
}
