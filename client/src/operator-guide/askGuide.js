// Inline "Ask Guide" entry points (ZST-UX-GUIDE-001 §1.1, §5): a page asks Guide a question
// with its object already attached. Deliberately tiny and dependency-free — it is imported by
// console pages, so it must never pull the lazily loaded Guide bundle into them.
//
// The request travels as a window event. Guide may not be loaded yet (it mounts after the page
// is idle), so the request is also parked on window; Guide picks it up when it mounts.
export const OPEN_EVENT = "opguide:open";
const PENDING = "__opguidePending";
const PENDING_TTL_MS = 30_000;

// detail: { question, eventId?, route? }
export function askGuide(detail = {}) {
  const request = { question: detail.question || "", eventId: detail.eventId || null, route: detail.route || null, at: Date.now() };
  window[PENDING] = request;
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: request }));
}

// The parked request, once; a stale one (the user clicked long ago, then navigated) is dropped.
export function takePendingAsk() {
  const request = window[PENDING];
  window[PENDING] = undefined;
  return request && Date.now() - request.at < PENDING_TTL_MS ? request : null;
}
