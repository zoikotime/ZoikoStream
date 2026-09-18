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

// ── WHY THIS IS THREE FLAGS AND NOT ONE ──────────────────────────────────────────────────
// It used to be a single `HAS_CONSOLES = !IS_NATIVE`, which removed the platform console,
// the organization console and the billing checkout together, because the first version of
// this app was a viewer-and-studio companion and none of the three had a mobile design.
//
// That bundled three unrelated judgements into one boolean:
//
//   * the ORGANIZATION console is what an org owner opens the app FOR. It is dense, but its
//     shell is already responsive (Sidebar has a drawer and breakpoints) and the rest is
//     layout work, not an impossibility. It ships.
//   * the PLATFORM console is super-admin operator tooling — the densest tables in the
//     codebase, used by a handful of people who are at a desk when they use them. It is
//     sequenced last rather than forbidden.
//   * BILLING CHECKOUT is not a layout question at all. It is a Google Play payments policy
//     question, and the answer does not change with screen size.
//
// Separating them means each can be decided on its own evidence. A single flag could only
// ever be moved all at once.

// The organization console (/organization/*) — dashboard, events, recordings, members,
// audience, sessions, analytics, settings. Ships everywhere.
export const HAS_ORG_CONSOLE = true;

// The platform console (/admin/*). Web only for now; this is a sequencing decision, not a
// permission one — RoleRoute already answers "may this account use it".
export const HAS_ADMIN_CONSOLE = !IS_NATIVE;

// Whether the billing surface may start a payment IN THIS BUILD.
//
// The billing PAGE ships everywhere — plan, usage, invoices, payment method are all readable
// on a phone and an owner checking their plan on the train is a real thing. What this gates
// is narrower: the hand-off to Stripe's hosted checkout.
//
// Google Play's payments policy is why. An Android app that takes a subscription payment
// through a third-party checkout is the exact shape that policy exists to reject, and the
// cost of getting it wrong is the whole app, not this screen. So on native the checkout
// opens in the DEVICE BROWSER (native/bridge.js -> @capacitor/browser, a Custom Tab), which
// is a different act: the purchase happens on the web, in the user's own browser, on the
// origin that already serves it.
export const SUPPORTS_IN_APP_CHECKOUT = !IS_NATIVE;

// Kept as the union of the two console flags so existing call sites that mean "does this
// build carry operator surfaces at all" keep reading correctly. New code should name the
// console it actually means.
export const HAS_CONSOLES = HAS_ORG_CONSOLE || HAS_ADMIN_CONSOLE;

// The public site to send someone to when a mobile build cannot serve the page they want
// (the admin/organization consoles above, and the billing flows that live inside them).
// Absolute, because on mobile there is no origin to be relative to that would reach the web
// app — the WebView's origin is the app's own bundle.
export const WEB_APP_URL = import.meta.env.VITE_WEB_APP_URL || "https://get.zoikostream.com";
