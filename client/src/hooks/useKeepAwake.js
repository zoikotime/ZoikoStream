import { useEffect } from "react";

/**
 * Hold a screen wake lock while `active`.
 *
 * ── WHY THIS EXISTS ──────────────────────────────────────────────────────────────────────
 * A phone dims and locks after ~30 seconds of no touch. Watching a 40-minute broadcast is 40
 * minutes of no touch, and so is presenting one. Without this the screen blacks out
 * mid-event — and on the HOST side that is not a cosmetic problem: a locked screen suspends
 * the WebView, which tears down the camera capture and drops the publisher off air.
 * (The Android foreground service in the shell keeps the PROCESS alive; this is what keeps
 * the screen itself from going away underneath it.)
 *
 * navigator.wakeLock is the whole implementation — Android's System WebView has had it since
 * Chrome 84, and it is exactly what a Capacitor plugin for this would call. Absent in some
 * desktop browsers and in any non-secure context, where every branch below no-ops.
 *
 * The lock is released by the browser whenever the document is hidden (app backgrounded,
 * screen locked by the user) and is NOT re-acquired automatically, so the visibility
 * listener re-takes it on return. Without that, one glance at a notification ends the lock
 * for the rest of the session.
 */
export default function useKeepAwake(active) {
  useEffect(() => {
    if (!active || typeof navigator === "undefined" || !navigator.wakeLock) return undefined;

    let sentinel = null;
    let released = false;

    const acquire = async () => {
      if (released || sentinel || document.visibilityState !== "visible") return;
      try {
        sentinel = await navigator.wakeLock.request("screen");
        // The browser drops the lock on its own terms (backgrounding, low battery). Clearing
        // the handle here is what lets the visibility listener below know to ask again.
        sentinel.addEventListener("release", () => { sentinel = null; });
      } catch {
        // Denied, unsupported, or the document went hidden between the check and the call.
        // A screen that dims is a worse experience, not a broken one.
        sentinel = null;
      }
    };

    const onVisibility = () => { if (document.visibilityState === "visible") acquire(); };

    acquire();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      released = true;
      document.removeEventListener("visibilitychange", onVisibility);
      sentinel?.release?.().catch(() => {});
      sentinel = null;
    };
  }, [active]);
}
