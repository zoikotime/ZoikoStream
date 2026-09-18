// What kind of shell is this bundle running inside, and what can that shell actually do.
//
// ── WHY A BUILD-TIME FLAG AND NOT A RUNTIME SNIFF ────────────────────────────────────────
// There are two builds of this client, from one source tree:
//
//   web     `npm run build`         served by FastAPI on the app's own origin (see the
//                                   Dockerfile). Every console ships.
//   native  `npm run build:mobile`  copied into the Android app's assets and served by the
//                                   WebView from https://localhost. Viewer, speaker and the
//                                   host studio only.
//
// The difference decides which ROUTES exist, so it has to be known before the router is
// constructed — a runtime sniff would build the full route table and then try to unbuild it.
// `import.meta.env.VITE_PLATFORM` is statically replaced by Vite, so on the web build
// `IS_NATIVE` is the literal `false` and every `IS_NATIVE && ...` branch below is dead code
// the bundler drops. The web bundle is therefore byte-for-byte unaffected by this file.
export const IS_NATIVE = import.meta.env.VITE_PLATFORM === "mobile";

// True only once Capacitor's own bridge has loaded. `IS_NATIVE` says which BUILD this is;
// this says whether the native runtime is actually there to talk to. They differ in exactly
// one place that matters: running the mobile build in a desktop browser (`npm run dev` with
// the flag set) to debug a mobile-only layout, where the bridge is absent and every native
// call must no-op rather than throw.
export const HAS_BRIDGE = () =>
  typeof window !== "undefined" && !!window.Capacitor?.isNativePlatform?.();

// ── CAPABILITIES ─────────────────────────────────────────────────────────────────────────
// Named for the capability, not the platform, so a call site reads as a statement about what
// is possible rather than a guess about where it is running.

// getDisplayMedia. Android's System WebView does not implement it — the call is simply
// absent from navigator.mediaDevices, so a "Share your screen" button on a phone is a
// control that can only ever fail. Native screen capture on Android goes through
// MediaProjection, which is a foreground-service flow with its own consent dialog and no
// getDisplayMedia shim; wiring that to LiveKit is a separate piece of work, so until it
// exists the honest thing is to not offer the control.
export const SUPPORTS_SCREEN_SHARE = !IS_NATIVE;

// Whether this build carries the platform (/admin/*) and organization (/organization/*)
// consoles. It does not on mobile: those are dense operator surfaces — 21 and 17 pages of
// tables, modals and multi-column dashboards — and shipping them to a phone would mean
// shipping screens that cannot be operated on one. Admin work happens on the web app.
export const HAS_CONSOLES = !IS_NATIVE;

// The public site to send someone to when a mobile build cannot serve the page they want
// (the admin/organization consoles above, and the billing flows that live inside them).
// Absolute, because on mobile there is no origin to be relative to that would reach the web
// app — the WebView's origin is the app's own bundle.
export const WEB_APP_URL = import.meta.env.VITE_WEB_APP_URL || "https://get.zoikostream.com";
