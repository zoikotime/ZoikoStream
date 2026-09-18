import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { exitApp, launchUrl, onAppUrlOpen, onBackButton } from "./bridge";
import { WEB_APP_URL } from "../platform";

/**
 * The two Android behaviours a WebView does not get for free. Renders nothing; mounted once,
 * inside <BrowserRouter> (it needs the router) and inside <AuthProvider> (so a deep link that
 * lands on a guarded route is handled by that route's own guard, not re-implemented here).
 *
 * On the web build this component is never mounted — see App.jsx — and both listeners below
 * return a no-op unsubscribe anyway, so it is inert even if it were.
 */
export default function NativeShell() {
  const navigate = useNavigate();

  // ── 1. HARDWARE / GESTURE BACK ─────────────────────────────────────────────────────────
  // Android's back is a system-level promise: it goes back, and at the start of the stack it
  // leaves the app. A WebView with no handler does neither — it sits there, which reads as a
  // frozen app and is a routine reason for a one-star review.
  //
  // The decision is made from React Router's own history index rather than from Capacitor's
  // `canGoBack`. They disagree in the case that matters: `canGoBack` reports the WEBVIEW's
  // history, which still counts entries from before a logout wiped the session, so honouring
  // it walks a signed-out user backwards into pages their session can no longer load. `idx`
  // is the position within THIS router's stack, so 0 means "this is where the app started"
  // — which is exactly when back should exit.
  useEffect(() => onBackButton(() => {
    const idx = window.history.state?.idx;
    if (typeof idx === "number" && idx > 0) navigate(-1);
    else exitApp();
  }), [navigate]);

  // ── 2. DEEP LINKS ──────────────────────────────────────────────────────────────────────
  // Android App Links (see the intent filters in AndroidManifest.xml) hand
  // https://get.zoikostream.com/... straight to this app instead of to Chrome. That is the
  // whole point of the mobile build for a viewer: the invitation they were emailed opens the
  // event, not a browser tab asking them to sign in again.
  //
  // Only the PATH is taken, and only from a URL whose origin is one we publish. A link is
  // untrusted input that any app on the device can send us, and navigate() with a foreign
  // absolute URL would be an open redirect into our own shell. Anything else is ignored —
  // the app simply stays where it is, which is the safe failure.
  useEffect(() => {
    const toPath = (url) => {
      try {
        const parsed = new URL(url);
        const allowed = new URL(WEB_APP_URL);
        if (parsed.origin !== allowed.origin) return null;
        return `${parsed.pathname}${parsed.search}${parsed.hash}` || "/";
      } catch {
        return null;
      }
    };

    // A link that COLD-STARTED the app arrives before any listener could exist, so it has to
    // be asked for. `replace` because that link IS the first screen — leaving the launch
    // route underneath it would make one back press go somewhere the user never chose.
    let cancelled = false;
    launchUrl().then((url) => {
      const path = url && toPath(url);
      if (path && !cancelled) navigate(path, { replace: true });
    });

    // A link that arrives while the app is already running: push, so back returns to what
    // they were doing.
    const off = onAppUrlOpen(({ url }) => {
      const path = toPath(url);
      if (path) navigate(path);
    });

    return () => { cancelled = true; off(); };
  }, [navigate]);

  return null;
}
