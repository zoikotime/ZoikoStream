// Inline "Ask Guide" entry points and the `?guide=` deep link (Engineering Wireframe §2.1).
//
// Deliberately tiny and dependency-free: public pages import it to render an "Ask Guide" CTA
// without pulling the widget, the API client or the auth module into their bundle. A request
// made before the lazy widget has loaded is held here and picked up when it mounts.
//
// Only a non-sensitive quick-action id travels this way — never free text and never anything
// from the page (WF §2.1 "Never place private content in the URL"). A deep link opens Guide
// and, for a typed flow (viewer help, demo, sales), starts it; it never sends a message.

export const GUIDE_OPEN_EVENT = "zoikostream:guide-open";
const AVAILABLE_EVENT = "zoikostream:guide-available";

// Public quick-action ids (URL/CTA vocabulary) -> server intent ids (intent catalog).
export const QUICK_ACTIONS = {
  "product-overview": "I-PRODUCT",
  pricing: "I-PRICING",
  "request-demo": "I-DEMO",
  demo: "I-DEMO",
  "talk-to-sales": "I-SALES",
  sales: "I-SALES",
  "viewer-help": "I-VIEWER",
  "viewer-access": "I-VIEWER-ACCESS",
  "playback-help": "I-PLAYBACK",
  captions: "I-CAPTIONS",
  replay: "I-REPLAY",
};

/** `?guide=` value -> { intentId|null } for "open", a known quick action; null otherwise. */
export function parseGuideParam(value) {
  if (typeof value !== "string") return null;
  const v = value.trim().toLowerCase();
  if (v === "open" || v === "1" || v === "true") return { intentId: null };
  if (Object.hasOwn(QUICK_ACTIONS, v)) return { intentId: QUICK_ACTIONS[v] };
  return null;
}

let pending = null; // { intentId, entryPoint } waiting for the widget to mount

/** Open Guide, optionally on a quick action. entryPoint: "inline_cta" | "deep_link". */
export function askGuide({ quickAction = null, entryPoint = "inline_cta" } = {}) {
  const intentId = quickAction && Object.hasOwn(QUICK_ACTIONS, quickAction) ? QUICK_ACTIONS[quickAction] : null;
  pending = { intentId, entryPoint };
  try {
    window.dispatchEvent(new CustomEvent(GUIDE_OPEN_EVENT, { detail: pending }));
  } catch {
    /* no window: nothing to open */
  }
}

/** The widget takes the held request once, when it is ready to act on it. */
export function takePendingGuideOpen() {
  const p = pending;
  pending = null;
  return p;
}

// Whether Guide can open on this page (bootstrap said enabled). Inline CTAs render nothing
// otherwise, so a page never shows a button that does nothing.
let available = false;
export const isGuideAvailable = () => available;
export function setGuideAvailable(value) {
  if (available === !!value) return;
  available = !!value;
  try {
    window.dispatchEvent(new CustomEvent(AVAILABLE_EVENT));
  } catch {
    /* ignore */
  }
}
export function subscribeGuideAvailable(fn) {
  window.addEventListener(AVAILABLE_EVENT, fn);
  return () => window.removeEventListener(AVAILABLE_EVENT, fn);
}
