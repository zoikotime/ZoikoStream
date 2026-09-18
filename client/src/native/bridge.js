// The native side of the app, reached WITHOUT importing a single Capacitor package.
//
// ── WHY NOT `import { StatusBar } from "@capacitor/status-bar"` ───────────────────────────
// Because there is one source tree and two builds. A static import puts those packages in
// client/package.json, which means the WEB build installs them, bundles their web shims, and
// CI has to resolve them — all so that code can run in an environment the web build is never
// in. Capacitor already injects `window.Capacitor.Plugins.*` into the WebView at runtime, so
// reading from there costs nothing on web (the object is absent, every call below no-ops)
// and needs no dependency on either side. The Android project still lists the plugins as
// real Gradle dependencies — see ZoikoStream-app/package.json — which is what actually
// installs them; this file is only how the SPA addresses them.
//
// Everything here is best-effort by design. A plugin that is missing, a call that rejects, a
// WebView that predates an API — none of it may break the page. The app has to work when the
// shell does not.

import { IS_NATIVE } from "../platform";

/** The Capacitor bridge, or null on the web. */
const bridge = () =>
  (typeof window !== "undefined" && window.Capacitor?.isNativePlatform?.()) ? window.Capacitor : null;

/** A named plugin, or null. Never throws. */
const plugin = (name) => bridge()?.Plugins?.[name] || null;

/** Call a plugin method and swallow everything. Returns undefined on any failure. */
const call = async (name, method, ...args) => {
  const p = plugin(name);
  if (!p || typeof p[method] !== "function") return undefined;
  try {
    return await p[method](...args);
  } catch {
    // A plugin that is absent, out of date, or refuses is not a reason to break the screen
    // it was decorating.
    return undefined;
  }
};

export const isNativeRuntime = () => bridge() !== null;

/**
 * Chrome the shell owns: splash, status bar, keyboard.
 *
 * Called once from main.jsx, before React renders. The splash is hidden HERE rather than on
 * first paint because the alternative — letting it auto-hide on a timer — shows the app
 * mid-mount on a fast device and a blank screen on a slow one. React has mounted by the time
 * this resolves, so what the user sees behind the fade is the real first screen.
 */
export async function initNativeChrome() {
  // The hook every `html.native` rule in index.css hangs off. Set FIRST and synchronously,
  // before the awaits below and before React renders, so the WebView-specific rules (no text
  // selection, no tap highlight, 44px targets) are in place for the first paint rather than
  // applied over one the user has already seen.
  //
  // `IS_NATIVE` is in the condition alongside the runtime check so that running the mobile
  // build in a desktop browser — `VITE_PLATFORM=mobile npm run dev`, which is how the
  // mobile-only layouts get debugged without a device — still gets the mobile styling. The
  // web build cannot reach either half: IS_NATIVE is the literal `false` there, and no
  // browser defines window.Capacitor.
  if (IS_NATIVE || isNativeRuntime()) {
    document.documentElement.classList.add("native");
  }
  if (!isNativeRuntime()) return;

  // Dark chrome with light glyphs. ZoikoStream's shells are dark-first (see theme/), and a
  // status bar that stays light over a dark header is the single most obvious "this is a
  // website in a box" tell.
  await call("StatusBar", "setStyle", { style: "DARK" });
  await call("StatusBar", "setBackgroundColor", { color: "#0B0F19" });
  // NOT overlaid. Android then lays the WebView out below the status bar, every
  // env(safe-area-inset-*) resolves to zero, and no layout has to account for a notch. The
  // alternative buys the watch page ~24px of edge-to-edge video and charges for it in every
  // layout pinned to 100dvh — the studio, the watch page, both shells — where `h-dvh` plus
  // safe-area body padding is an overflowing page rather than an inset one. See the note in
  // index.css.
  await call("StatusBar", "setOverlaysWebView", { overlay: false });

  // `body` resize, not `native`: the studio and the watch page are flex columns pinned to
  // 100dvh, and the `native` mode resizes the WebView itself, which collapses that layout
  // when the chat composer takes focus. Resizing the body lets the same CSS that handles a
  // narrow window handle a raised keyboard.
  await call("Keyboard", "setResizeMode", { mode: "body" });
  await call("Keyboard", "setAccessoryBarVisible", { isVisible: false });

  await call("SplashScreen", "hide", { fadeOutDuration: 200 });
}

/** Close the app. The one thing a browser cannot do, and the correct answer to "back" at the root. */
export const exitApp = () => call("App", "exitApp");

/**
 * Subscribe to Android's hardware/gesture back. Returns an unsubscribe function.
 *
 * The handler receives `{ canGoBack }` — Capacitor's own reading of the WebView history.
 */
export function onBackButton(handler) {
  const p = plugin("App");
  if (!p) return () => {};
  let remove = () => {};
  try {
    const handle = p.addListener("backButton", handler);
    // Capacitor 7 returns a promise for the handle; older shapes return it directly.
    Promise.resolve(handle).then((h) => { remove = () => h?.remove?.(); }).catch(() => {});
  } catch {
    return () => {};
  }
  return () => remove();
}

/**
 * Subscribe to a link opened into the app (an Android App Link, or a custom-scheme URL).
 * Returns an unsubscribe function. The payload is `{ url }`.
 */
export function onAppUrlOpen(handler) {
  const p = plugin("App");
  if (!p) return () => {};
  let remove = () => {};
  try {
    const handle = p.addListener("appUrlOpen", handler);
    Promise.resolve(handle).then((h) => { remove = () => h?.remove?.(); }).catch(() => {});
  } catch {
    return () => {};
  }
  return () => remove();
}

/** The link that COLD-STARTED the app, if any. Null when it was launched from the icon. */
export async function launchUrl() {
  const res = await call("App", "getLaunchUrl");
  return res?.url || null;
}

/**
 * Open a URL OUTSIDE the app, in the device browser.
 *
 * This is the one that matters for store review as much as for usability. The mobile build
 * has no admin or organization console and no billing screens (App.jsx), so every link to
 * one has to leave. Opening them in the WebView instead would mean the app rendering the
 * very consoles that were removed from it — and, for billing, a third-party checkout inside
 * the app, which is what Google Play's payments policy prohibits. `Browser.open` hands off
 * to a Custom Tab; the `window.open` fallback is what the web build does anyway.
 */
export async function openExternal(url) {
  const opened = await call("Browser", "open", { url, presentationStyle: "popover" });
  if (opened === undefined && typeof window !== "undefined") {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}
