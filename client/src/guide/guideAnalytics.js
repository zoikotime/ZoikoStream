// Guide analytics (PRD §16, Conversation Flow §30, Figma §15, Engineering Wireframe §07).
//
// One event per occurrence. `event` is the PRD §16 / CF §30 name (the approved product
// taxonomy); where the Wireframe §07 table names the same occurrence differently, that name
// travels along in `aliases` so either taxonomy can be mapped without double counting.
// Meanings follow CF §30 exactly: guide_flow_resolved only when a viewer issue is resolved,
// guide_demo_started only for a demo (not a sales) flow, guide_escalated when an escalation
// route is shown, guide_demo_submitted only for a confirmed lead.
//
// Consent (WF-12 A3, WF §7.1, UX-012, Security/Privacy §26): Guide telemetry is optional
// analytics, so nothing is sent until the site's consent signal says analytics is granted.
// The state is read from the site's consent manager (setGuideAnalyticsConsent, the
// `zoikostream:consent` window event, or a Google Consent Mode update in window.dataLayer);
// Guide never keeps its own copy of the visitor's choice. Global Privacy Control = denied.
//
// Privacy (CF §30.1, WF §7.1, Figma §15 "Analytics minimization"): payloads carry ids, enums,
// booleans, counts and buckets only. No chat text, no lead values, no names/emails/phones, no
// free-text feedback, no session capability. Every parameter is filtered through an allowlist
// and a value-shape check, so a careless call site cannot leak text into general analytics.

export const GUIDE_ANALYTICS_EVENT = "zoikostream:guide-analytics";
export const SITE_CONSENT_EVENT = "zoikostream:consent";

// Occurrence -> [primary name, ...aliases from the other specs]
export const EVENTS = {
  launcher_viewed: ["guide_launcher_impression", "guide_launcher_viewed"],
  opened: ["guide_opened"],
  intent_selected: ["guide_intent_selected"],
  message_sent: ["guide_message_submitted", "guide_message_sent"],
  answer_rendered: ["guide_answer_completed", "guide_response_rendered"],
  knowledge_gap: ["guide_knowledge_gap"],
  restricted: ["guide_restricted"],
  degraded: ["guide_degraded"],
  cta_clicked: ["guide_link_clicked", "guide_cta_clicked"],
  source_opened: ["guide_source_opened"],
  demo_started: ["guide_demo_started", "guide_flow_started", "guide_lead_started"],
  sales_started: ["guide_lead_started", "guide_flow_started"],
  lead_submitted: ["guide_demo_submitted", "guide_lead_submitted"],
  support_flow_started: ["guide_flow_started"],
  support_flow_step: ["guide_flow_step"],
  support_flow_resolved: ["guide_viewer_flow_completed", "guide_flow_resolved"],
  support_flow_escalated: ["guide_viewer_flow_completed", "guide_escalated"],
  escalation_shown: ["guide_escalated"],
  escalation_selected: ["guide_escalation_selected"],
  feedback_submitted: ["guide_feedback", "guide_feedback_submitted"],
  unknown_action: ["guide_unknown_action"],
  rate_limited: ["guide_rate_limited"],
  error: ["guide_error"],
  closed: ["guide_closed"],
  session_ended: ["guide_session_ended"],
};

const ALLOWED = new Set([
  "page_id", "page_type", "entry_point", "device_class", "locale", "consent_state",
  "intent_id", "intent_category", "intent_class", "source_intent", "message_id", "response_id",
  "response_type", "answer_class", "source_count", "source_ids", "action_count", "grounded",
  "latency_bucket", "length_bucket", "missing_topic_code", "cta_id", "cta_type", "destination_id",
  "source_id", "flow_type", "flow_kind", "step_id", "outcome", "resolution_code", "has_reference",
  "consent_given", "route_type", "helpful", "rating", "reason_code", "error_class", "recoverable",
  "status", "retry_after_known", "duration_bucket", "message_count_bucket", "end_reason",
  "is_retry", "action_type",
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

// ── Site consent ────────────────────────────────────────────────────────────────────────
const CONSENT_STATES = new Set(["granted", "denied", "unknown"]);
let explicitConsent = null; // set by the site's consent manager through setGuideAnalyticsConsent

/** The site's consent manager reports the visitor's analytics choice here. */
export function setGuideAnalyticsConsent(state) {
  explicitConsent = CONSENT_STATES.has(state) ? state : null;
}

const normalise = (v) => {
  if (v === true || v === "granted" || v === "accepted" || v === "allow") return "granted";
  if (v === false || v === "denied" || v === "rejected" || v === "deny") return "denied";
  return null;
};

// Google Consent Mode (`gtag("consent", "default"|"update", {analytics_storage})`) is the
// common way a site consent platform publishes its state; the last entry wins.
function consentFromDataLayer() {
  try {
    const dl = Array.isArray(window.dataLayer) ? window.dataLayer : [];
    for (let i = dl.length - 1; i >= 0; i -= 1) {
      const e = dl[i];
      const args = e && typeof e === "object" && "length" in e ? Array.from(e) : null;
      if (args && args[0] === "consent" && args[2] && "analytics_storage" in args[2]) {
        return normalise(args[2].analytics_storage);
      }
    }
  } catch {
    /* unreadable consent layer = no signal */
  }
  return null;
}

/** granted | denied | unknown — Guide sends analytics only when this is "granted". */
export function getGuideAnalyticsConsent() {
  try {
    if (navigator.globalPrivacyControl === true) return "denied";
  } catch {
    /* no navigator */
  }
  if (explicitConsent) return explicitConsent;
  return consentFromDataLayer() || "unknown";
}

if (typeof window !== "undefined") {
  window.addEventListener(SITE_CONSENT_EVENT, (e) => {
    const state = normalise(e?.detail?.analytics);
    explicitConsent = state;
  });
}

// ── Buckets (no raw values leave the browser) ───────────────────────────────────────────
export function deviceClass() {
  try {
    if (window.matchMedia?.("(max-width: 767px)")?.matches) return "mobile";
    if (window.matchMedia?.("(max-width: 1023px)")?.matches) return "tablet";
    return "desktop";
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

/** Answer latency (PRD §16 guide_answer_completed.latency_bucket). */
export function bucketLatency(ms) {
  if (!Number.isFinite(ms) || ms < 0) return undefined;
  if (ms < 1000) return "lt_1s";
  if (ms < 3000) return "1s_3s";
  if (ms < 8000) return "3s_8s";
  if (ms < 15000) return "8s_15s";
  return "gte_15s";
}

/** Message length (PRD §16 guide_message_submitted.length_bucket) — never the text. */
export function bucketLength(n) {
  if (n <= 20) return "1_20";
  if (n <= 80) return "21_80";
  if (n <= 200) return "81_200";
  if (n <= 500) return "201_500";
  return "gt_500";
}

/** Emit one Guide analytics event. Never throws: analytics can never break Guide (FE-12). */
export function track(occurrence, params) {
  const names = EVENTS[occurrence];
  if (!names) return;
  try {
    // Optional analytics stays off until the site's consent manager grants it (UX-012).
    if (getGuideAnalyticsConsent() !== "granted") return;
    const detail = { event: names[0], aliases: names.slice(1), ...sanitize(params) };
    window.dispatchEvent(new CustomEvent(GUIDE_ANALYTICS_EVENT, { detail }));
    if (Array.isArray(window.dataLayer)) window.dataLayer.push({ ...detail });
  } catch {
    /* analytics is best-effort */
  }
}
