// Guide analytics (Engineering Wireframe §07, Conversation Flow §30, Figma §15).
//
// One event per occurrence. `event` is the Wireframe §07 contract name where that table defines
// one; the equivalent names used by Conversation Flow §30 / Figma §15 for the same occurrence
// travel along in `aliases` so either taxonomy can be mapped without double counting.
//
// Privacy (CF §30.1, WF §7.1, Figma §15 "Analytics minimization"): payloads carry ids, enums,
// booleans and counts only. No chat text, no lead values, no names/emails/phones, no free-text
// feedback, no session capability. Every parameter is filtered through an allowlist and a
// value-shape check, so a careless call site cannot leak text into general analytics.

export const GUIDE_ANALYTICS_EVENT = "zoikostream:guide-analytics";

// Occurrence -> [primary name, ...aliases from the other specs]
export const EVENTS = {
  launcher_viewed: ["guide_launcher_viewed"],
  opened: ["guide_opened"],
  intent_selected: ["guide_intent_selected"],
  message_sent: ["guide_message_sent", "guide_message_submitted"],
  answer_rendered: ["guide_response_rendered", "guide_answer_completed"],
  knowledge_gap: ["guide_knowledge_gap"],
  restricted: ["guide_restricted"],
  degraded: ["guide_degraded"],
  cta_clicked: ["guide_cta_clicked", "guide_link_clicked"],
  source_opened: ["guide_source_opened"],
  lead_started: ["guide_lead_started", "guide_flow_started", "guide_demo_started"],
  lead_submitted: ["guide_lead_submitted", "guide_demo_submitted"],
  support_flow_started: ["guide_flow_started"],
  support_flow_step: ["guide_flow_step"],
  support_flow_completed: ["guide_viewer_flow_completed", "guide_flow_resolved"],
  escalation_selected: ["guide_escalation_selected", "guide_escalated"],
  feedback_submitted: ["guide_feedback_submitted", "guide_feedback"],
  rate_limited: ["guide_rate_limited"],
  error: ["guide_error"],
  closed: ["guide_closed"],
  session_ended: ["guide_session_ended"],
};

const ALLOWED = new Set([
  "page_id", "page_type", "entry_point", "device_class", "locale", "consent_state",
  "intent_id", "intent_category", "source_intent", "message_id", "response_id", "response_type",
  "source_count", "source_ids", "action_count", "grounded", "cta_id", "cta_type", "destination_id",
  "source_id", "flow_type", "flow_kind", "step_id", "outcome", "has_reference", "consent_given",
  "route_type", "helpful", "reason_code", "error_class", "status", "retry_after_known",
  "duration_bucket", "message_count_bucket", "end_reason", "is_retry",
]);

// Ids and enums only: short, no whitespace, no "@" (never an email address).
const ID_RE = /^[A-Za-z0-9_.:/-]{1,80}$/;

function clean(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "string") return ID_RE.test(value) ? value : undefined;
  if (Array.isArray(value)) {
    const out = value.map(clean).filter((v) => typeof v === "string").slice(0, 10);
    return out.length ? out : undefined;
  }
  return undefined;
}

export function sanitize(params = {}) {
  const out = {};
  for (const [k, v] of Object.entries(params)) {
    if (!ALLOWED.has(k)) continue;
    const c = clean(v);
    if (c !== undefined) out[k] = c;
  }
  return out;
}

export function deviceClass() {
  try {
    return window.matchMedia?.("(max-width: 767px)")?.matches ? "mobile" : "desktop";
  } catch {
    return "desktop";
  }
}

export function bucketSeconds(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 30) return "lt_30s";
  if (s < 120) return "30s_2m";
  if (s < 600) return "2m_10m";
  return "gte_10m";
}

export function bucketCount(n) {
  if (n <= 0) return "0";
  if (n <= 2) return "1_2";
  if (n <= 5) return "3_5";
  if (n <= 10) return "6_10";
  return "gt_10";
}

/** Emit one Guide analytics event. Never throws: analytics can never break Guide (FE-12). */
export function track(occurrence, params) {
  const names = EVENTS[occurrence];
  if (!names) return;
  try {
    const detail = { event: names[0], aliases: names.slice(1), ...sanitize(params) };
    window.dispatchEvent(new CustomEvent(GUIDE_ANALYTICS_EVENT, { detail }));
    if (Array.isArray(window.dataLayer)) window.dataLayer.push({ ...detail });
  } catch {
    /* analytics is best-effort */
  }
}
