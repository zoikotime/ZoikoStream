// ZoikoStream Guide — public API client (ZST-API-PUB-GUIDE-001).
//
// Deliberately NOT the shared axios instance in src/api.js: that one attaches the signed-in
// user's bearer token to every request, and the public Guide must stay anonymous — a Guide
// session never carries or inherits platform identity (PR-029/030). Plain fetch, cookie
// credentials only (the HttpOnly zs_guide_sid capability), CSRF header on every write.
import { API_BASE } from "../api";

// Dev only: "localhost" and "127.0.0.1" are different SITES to the browser, so a page on one
// talking to an API on the other has its SameSite session cookie withheld and every Guide call
// after session creation fails as 401. When both are loopback, use the page's own hostname
// for the API. Production is same-origin (one container), so this never applies there.
function guideBase() {
  try {
    const api = new URL(API_BASE);
    const loopback = ["localhost", "127.0.0.1"];
    if (import.meta.env.DEV && loopback.includes(api.hostname) && loopback.includes(window.location.hostname)) {
      api.hostname = window.location.hostname;
    }
    return `${api.origin}/api/v1/public-guide`;
  } catch {
    return `${API_BASE}/api/v1/public-guide`;
  }
}

export const GUIDE_BASE = guideBase();

export class GuideError extends Error {
  constructor(status, problem) {
    super(problem?.detail || `Guide request failed (${status})`);
    this.status = status;
    this.code = problem?.code || "GUIDE_NETWORK_ERROR";
    this.problem = problem || {};
    this.retryAfter = problem?.retryAfter ?? null;
  }
}

// Session capability lives in memory only: no conversation data or tokens in localStorage
// (Security/Privacy §8). A reload simply starts a fresh, anonymous session.
let session = null; // { id, csrf }

export const getSession = () => session;
export const clearSession = () => {
  session = null;
};

// Last time the server accepted a call on the current session. Every authenticated session
// call refreshes the server's idle clock (sessions.load_session), so the client can warn before
// idle expiry without a dedicated keep-alive route (Figma §13 "Timeout/session expiry").
let lastActivity = 0;
const activityListeners = new Set();
export const getLastActivity = () => lastActivity;
export function onActivity(fn) {
  activityListeners.add(fn);
  return () => activityListeners.delete(fn);
}
function markActivity() {
  lastActivity = Date.now();
  activityListeners.forEach((fn) => {
    try {
      fn(lastActivity);
    } catch {
      /* listeners never break a request */
    }
  });
}

export const uuid = () =>
  globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) =>
        (+c ^ (Math.random() * 16) >> (+c / 4)).toString(16),
      );

async function request(method, path, { body, headers = {}, stream = false, signal } = {}) {
  const init = {
    method,
    credentials: "include",
    headers: { Accept: stream ? "text/event-stream" : "application/json", ...headers },
    ...(signal ? { signal } : {}),
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (method !== "GET" && session) init.headers["X-ZS-CSRF"] = session.csrf;
  let res;
  try {
    res = await fetch(`${GUIDE_BASE}${path}`, init);
  } catch {
    if (signal?.aborted) throw new GuideError(0, { code: "GUIDE_TIMEOUT", detail: "Guide took too long to answer." });
    throw new GuideError(0, { code: "GUIDE_NETWORK_ERROR", detail: "Guide couldn't be reached." });
  }
  if (res.ok && path.startsWith("/sessions")) markActivity();
  if (res.status === 204 || res.status === 202) return null;
  if (stream && res.ok && (res.headers.get("content-type") || "").includes("text/event-stream")) {
    return res;
  }
  let data;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const retry = Number(res.headers.get("retry-after"));
    throw new GuideError(res.status, { ...(data || {}), retryAfter: Number.isFinite(retry) && retry > 0 ? retry : null });
  }
  return data;
}

const sessionPath = (p) => {
  if (!session) throw new GuideError(401, { code: "GUIDE_SESSION_INVALID" });
  return `/sessions/${session.id}${p}`;
};

export const bootstrap = ({ pageId, pageType }) =>
  request("GET", `/bootstrap?page_id=${encodeURIComponent(pageId)}&page_type=${encodeURIComponent(pageType)}&locale=en-US`);

export async function createSession(pageContext) {
  const data = await request("POST", "/sessions", {
    body: { client_session_nonce: uuid(), page_context: pageContext },
  });
  session = { id: data.session_id, csrf: data.csrf_token };
  return data;
}

export const updateContext = (pageContext) =>
  request("PATCH", sessionPath("/context"), { body: { page_context: pageContext } });

export const endSession = async () => {
  try {
    await request("POST", sessionPath("/end"));
  } finally {
    session = null;
  }
};

export const startSupportFlow = (flowType) =>
  request("POST", sessionPath("/support-flows"), { body: { flow_type: flowType } });

export const submitSupportStep = (flowId, { stepId, optionId, command }) =>
  request("POST", sessionPath(`/support-flows/${flowId}/steps`), {
    body: {
      client_step_id: uuid(),
      step_id: stepId,
      ...(optionId ? { option_id: optionId } : { command }),
    },
  });

export const startLeadFlow = (flowKind, sourceIntent) =>
  request("POST", sessionPath("/lead-flows"), {
    body: { flow_kind: flowKind, ...(sourceIntent ? { source_intent: sourceIntent } : {}) },
  });

export const updateLeadFlow = (flowId, fields) =>
  request("PATCH", sessionPath(`/lead-flows/${flowId}`), { body: { fields } });

export const cancelLeadFlow = (flowId) =>
  request("PATCH", sessionPath(`/lead-flows/${flowId}`), { body: { cancel: true } });

export const putConsent = (purpose, status, noticeVersion) =>
  request("PUT", sessionPath(`/consents/${purpose}`), {
    body: { status, notice_version: noticeVersion, source: "guide" },
  });

export const submitLead = (flowId, consentReceiptId, idempotencyKey) =>
  request("POST", sessionPath(`/lead-flows/${flowId}/submit`), {
    body: { consent_receipt_id: consentReceiptId },
    headers: { "Idempotency-Key": idempotencyKey },
  });

export const handoff = (target, reasonCode) =>
  request("POST", sessionPath("/handoffs"), {
    body: { target, ...(reasonCode ? { reason_code: reasonCode } : {}) },
    headers: { "Idempotency-Key": uuid() },
  });

export const sendFeedback = (payload) =>
  request("POST", sessionPath("/feedback"), { body: { client_feedback_id: uuid(), ...payload } });

// ── Messages (SSE with JSON fallback) ──────────────────────────────────────────────────────

// Parses a text/event-stream body into {event, data} objects. Exported for tests.
export function parseSSE(text) {
  const out = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    if (!block.trim()) continue;
    let event = "message";
    const data = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    try {
      out.push({ event, data: data.length ? JSON.parse(data.join("\n")) : null });
    } catch {
      /* malformed event: ignored, never executed (API §9) */
    }
  }
  return out;
}

// No byte from the server for this long = the message timed out (Figma §14 "Message timeout").
export const MESSAGE_TIMEOUT_MS = 60_000;

/**
 * Send a message. `onDelta(text)` receives the growing answer text while it streams;
 * `onAccepted()` fires once the server has accepted the message (an OK response), after which
 * the server holds the answer under this client_message_id and a retry must reuse it.
 * Resolves to { assistant_message, session_state } in the same shape as the JSON API.
 */
export async function sendMessage({ clientMessageId, text, intentHint }, { onDelta, onAccepted } = {}) {
  const body = {
    client_message_id: clientMessageId,
    text,
    ...(intentHint ? { intent_hint: intentHint } : {}),
  };
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  let timer = null;
  const arm = () => {
    if (!controller) return;
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), MESSAGE_TIMEOUT_MS);
  };
  arm();
  try {
    const res = await request("POST", sessionPath("/messages"), { body, stream: true, signal: controller?.signal });
    onAccepted?.();
    if (!(res instanceof Response)) return res; // server answered JSON
    return await readStream(res, { onDelta, arm, controller });
  } finally {
    clearTimeout(timer);
  }
}

async function readStream(res, { onDelta, arm, controller }) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  const msg = { sources: [], actions: [] };
  let completed = null;
  const handle = ({ event, data }) => {
    if (!data) return;
    if (event === "response.started") msg.message_id = data.assistant_message_id;
    else if (event === "response.text.delta") {
      answer += typeof data.delta === "string" ? data.delta : "";
      onDelta?.(answer);
    } else if (event === "response.source") msg.sources.push(data);
    else if (event === "response.action") msg.actions.push(data);
    else if (event === "response.completed") completed = data;
    else if (event === "response.error") throw new GuideError(503, { code: "GUIDE_UNAVAILABLE" });
  };
  for (;;) {
    let chunk;
    try {
      chunk = await reader.read();
    } catch {
      throw new GuideError(0, {
        code: controller?.signal.aborted ? "GUIDE_TIMEOUT" : "GUIDE_STREAM_INTERRUPTED",
        detail: "The answer was interrupted.",
      });
    }
    const { value, done } = chunk;
    arm();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const cut = buffer.lastIndexOf("\n\n");
    if (cut >= 0) {
      parseSSE(buffer.slice(0, cut)).forEach(handle);
      buffer = buffer.slice(cut + 2);
    }
    if (done) break;
  }
  if (buffer.trim()) parseSSE(buffer).forEach(handle);
  if (!completed) throw new GuideError(0, { code: "GUIDE_STREAM_INTERRUPTED", detail: "The answer was interrupted." });
  return {
    session_state: completed.session_state,
    assistant_message: {
      ...msg,
      text: answer,
      answer_class: completed.answer_class,
      grounded: completed.grounded,
      uncertainty: completed.uncertainty,
      notice: completed.notice || undefined,
      resume: completed.resume || undefined,
      suggestions: Array.isArray(completed.suggestions) ? completed.suggestions : [],
    },
  };
}
