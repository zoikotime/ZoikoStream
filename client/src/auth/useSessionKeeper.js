import { useCallback, useEffect, useRef, useState } from "react";
import api from "../api";
import { broadcast, onBroadcast, sessionEndReasonOf } from "./sessionStore";

// The one place the console decides what counts as a user being present.
//
// The SERVER enforces the idle and absolute limits on every request
// (server/app/services/auth_sessions.py); nothing here can keep a session alive on its own
// authority. This hook only:
//
//   1. reports GENUINE activity — pointer, key, touch, wheel and in-app navigation in a
//      visible tab — to POST /auth/session/activity, at most once per TOUCH_INTERVAL_MS.
//      Background polling, websocket heartbeats and hidden tabs never report, so an
//      abandoned tab lets its session lapse;
//   2. keeps a live production alive: while someone is genuinely producing — a host console
//      on air, a speaker on stage with their camera or microphone published — the page holds
//      the session (useSessionHold), because a presenter may not touch the mouse for half an
//      hour and must not be signed out mid-event. A hold is a condition the page states, never
//      a timer of its own: it ends the moment the condition does, applies only while the tab
//      is visible unless the page says otherwise, and can never outlast the server's absolute
//      limit (activity cannot move it). Socket heartbeats and polling are not holds;
//   3. schedules the inactivity warning from the server's own deadlines (corrected for a
//      skewed local clock), and when a deadline passes asks the SERVER whether the session
//      is still alive — another tab may have extended it — rather than deciding locally.

export const TOUCH_INTERVAL_MS = 60_000;
export const WARNING_LEAD_MS = 5 * 60_000;
export const HOLD_INTERVAL_MS = 4 * 60_000;
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "touchstart", "wheel"];

// ── live-production holds ────────────────────────────────────────────────────────────────
// Every mounted hold, with whether it may apply while its tab is hidden. One registry per tab;
// the keeper below reads it and decides whether this tab is holding right now.
const holds = new Map();
let nextHoldId = 0;
const holdListeners = new Set();
const notifyHolds = () => {
  for (const listener of holdListeners) listener();
};

/** Whether any mounted hold applies, given whether this tab is visible. */
const anyHold = (visible) => [...holds.values()].some((hold) => visible || hold.whileHidden);

/**
 * Keep the session alive while `active` is true: the page's own statement that its user is
 * genuinely producing right now (a host's broadcast on air, a speaker publishing on stage).
 *
 * `whileHidden` — whether the hold also applies while the tab is hidden. Off by default: a
 * hidden tab is how an abandoned page looks, so its session goes back to the normal idle
 * rules. The host console turns it on because a producer legitimately works from another
 * window (OBS) while their broadcast runs from this one.
 */
export function useSessionHold(active, { whileHidden = false } = {}) {
  useEffect(() => {
    if (!active) return undefined;
    const id = ++nextHoldId;
    holds.set(id, { whileHidden: !!whileHidden });
    notifyHolds();
    return () => {
      holds.delete(id);
      notifyHolds();
    };
  }, [active, whileHidden]);
}

const isVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

export function useSessionKeeper({ authenticated, onEnded }) {
  const [warning, setWarning] = useState(null);          // { expiresAt } in local ms
  const deadlines = useRef(null);                         // { idleAt, absoluteAt } in local ms
  const lastReport = useRef(Number.NEGATIVE_INFINITY);   // never reported: the first interaction reports at once
  const pendingReport = useRef(null);
  const timers = useRef([]);
  const onEndedRef = useRef(onEnded);
  useEffect(() => { onEndedRef.current = onEnded; });

  const clearTimers = () => {
    for (const t of timers.current) clearTimeout(t);
    timers.current = [];
  };

  const fail = useCallback((error) => {
    const reason = sessionEndReasonOf(error) || (error?.response?.status === 401 ? "reauth" : null);
    if (reason) onEndedRef.current?.(reason);
    // Anything else (offline, 5xx) is not a verdict on the session: the next deadline or
    // activity report asks again.
  }, []);

  // `check` is declared before `apply` uses it through a ref, so the two can schedule each
  // other without a dependency cycle.
  const checkRef = useRef(() => {});

  const apply = useCallback((status, { fromOtherTab = false } = {}) => {
    if (!status?.idle_expires_at || !status?.server_now) return;
    const skew = Date.parse(status.server_now) - Date.now();
    const idleAt = Date.parse(status.idle_expires_at) - skew;
    const absoluteAt = Date.parse(status.absolute_expires_at) - skew;
    deadlines.current = { idleAt, absoluteAt };
    if (!fromOtherTab) broadcast({ type: "status", status });

    clearTimers();
    const now = Date.now();
    const warnAt = idleAt - WARNING_LEAD_MS;
    if (now < warnAt) {
      setWarning(null);
      timers.current.push(setTimeout(() => setWarning({ expiresAt: idleAt }), warnAt - now));
    } else if (now < idleAt) {
      setWarning({ expiresAt: idleAt });
    }
    // At a deadline the SERVER decides — a little after it, so the server's clock has passed it.
    const nextDeadline = Math.min(idleAt, absoluteAt);
    timers.current.push(setTimeout(() => checkRef.current(), Math.max(0, nextDeadline - now) + 1_000));
  }, []);

  const check = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/session");
      apply(data);
    } catch (error) {
      fail(error);
    }
  }, [apply, fail]);
  useEffect(() => { checkRef.current = check; }, [check]);

  const report = useCallback(async () => {
    lastReport.current = Date.now();
    try {
      const { data } = await api.post("/auth/session/activity");
      apply(data);
      return true;
    } catch (error) {
      fail(error);
      return false;
    }
  }, [apply, fail]);

  // Genuine interaction -> at most one report per interval, the last interaction in an
  // interval reported at its end, so nothing a user does goes unrecorded.
  const onInteraction = useCallback(() => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    const since = Date.now() - lastReport.current;
    if (since >= TOUCH_INTERVAL_MS) {
      report();
    } else if (!pendingReport.current) {
      pendingReport.current = setTimeout(() => {
        pendingReport.current = null;
        report();
      }, TOUCH_INTERVAL_MS - since);
    }
  }, [report]);

  // Start / stop with the session.
  useEffect(() => {
    if (!authenticated) {
      // A stale `warning` is never rendered while signed out (AuthContext checks), and the
      // next session's first status check resets it.
      clearTimers();
      clearTimeout(pendingReport.current);
      pendingReport.current = null;
      deadlines.current = null;
      return undefined;
    }
    // A fresh page load, a reopened browser, a sign-in: ask where the session stands. Not
    // a report — loading a page is not activity. A network call; state changes on its answer.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    check();
    const opts = { capture: true, passive: true };
    for (const type of ACTIVITY_EVENTS) window.addEventListener(type, onInteraction, opts);
    // Coming back to the tab: the session may have ended (or been extended elsewhere) while
    // it was hidden. Ask; do not report.
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    // Another tab's fresh deadlines (its user is active) — no warning here either.
    const off = onBroadcast((message) => {
      if (message.type === "status") apply(message.status, { fromOtherTab: true });
    });
    return () => {
      for (const type of ACTIVITY_EVENTS) window.removeEventListener(type, onInteraction, opts);
      document.removeEventListener("visibilitychange", onVisible);
      off();
      clearTimers();
      clearTimeout(pendingReport.current);
      pendingReport.current = null;
    };
  }, [authenticated, check, apply, onInteraction]);

  // Live-production hold: report on a fixed beat while a hold applies, whatever the pointer is
  // doing. A hold applies while its tab is visible, or also while hidden if the page asked for
  // that (useSessionHold). The beat is well inside the idle window and never faster than
  // TOUCH_INTERVAL_MS, and the server throttles its own writes, so a hold costs a handful of
  // requests an hour. When the hold stops applying the beat stops, and the normal idle window
  // — counted from the last report — takes over, warning included.
  const [visible, setVisible] = useState(isVisible);
  useEffect(() => {
    const onVisibility = () => setVisible(isVisible());
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);
  // A hold starting or ending re-renders this, which re-reads the registry.
  const [, setHoldVersion] = useState(0);
  useEffect(() => {
    const listener = () => setHoldVersion((v) => v + 1);
    holdListeners.add(listener);
    return () => holdListeners.delete(listener);
  }, []);
  const holding = anyHold(visible);
  useEffect(() => {
    if (!authenticated || !holding) return undefined;
    // A network call to an external system; state changes only when it answers. Skipped when
    // a report went out within the interval (a hold flapping on and off must not spam it).
    if (Date.now() - lastReport.current >= TOUCH_INTERVAL_MS) report();
    const beat = setInterval(report, HOLD_INTERVAL_MS);
    return () => clearInterval(beat);
  }, [authenticated, holding, report]);

  // "Stay signed in": only the server can say yes.
  const staySignedIn = useCallback(() => report(), [report]);

  return { warning, staySignedIn, noteNavigation: onInteraction };
}
