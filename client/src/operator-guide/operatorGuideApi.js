// Operator Guide API (server/app/operator_guide/router.py). Uses the platform's own axios
// client, so the signed-in user's bearer token, session-expiry handling and API base are
// exactly the console's — Guide never holds a credential of its own.
import api from "../api";

const BASE = "/operator-guide";

// Only the event id travels with the route: other query parameters are never sent.
export function consoleRoute(pathname, search) {
  const event = new URLSearchParams(search || "").get("event");
  return event ? `${pathname}?event=${encodeURIComponent(event)}` : pathname;
}

export function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return `og-${crypto.randomUUID()}`;
  return `og-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// A Guide error body is {code, message} with user-facing §15 wording; anything else gets the
// generic unavailable message rather than an internal detail.
export function guideErrorMessage(error) {
  const body = error?.response?.data;
  if (body && typeof body.message === "string") return body.message;
  if (error?.response?.status === 401) return "Your session has ended. Sign in again to use Guide.";
  return "Guide is temporarily unavailable. Core ZoikoStream controls remain available.";
}

export const opGuide = {
  bootstrap: (route, eventId) =>
    api.get(`${BASE}/bootstrap`, { params: { route, ...(eventId ? { event_id: eventId } : {}) } })
      .then((r) => r.data),
  events: (q = "") => api.get(`${BASE}/context/events`, { params: { q } }).then((r) => r.data.items),
  attention: () => api.get(`${BASE}/attention`).then((r) => r.data),
  send: (body) => api.post(`${BASE}/messages`, body).then((r) => r.data),
  conversations: (q = "") => api.get(`${BASE}/conversations`, { params: { q } }).then((r) => r.data.items),
  conversation: (id) => api.get(`${BASE}/conversations/${id}`).then((r) => r.data),
  deleteConversation: (id) => api.delete(`${BASE}/conversations/${id}`),
  feedback: (messageId, rating, reason) =>
    api.post(`${BASE}/messages/${messageId}/feedback`, { rating, reason: reason || null }),
  confirm: (proposalId, key) =>
    api.post(`${BASE}/proposals/${proposalId}/confirm`, null, { headers: { "Idempotency-Key": key } })
      .then((r) => r.data),
  cancel: (proposalId) => api.post(`${BASE}/proposals/${proposalId}/cancel`).then((r) => r.data),
  settings: () => api.get(`${BASE}/settings`).then((r) => r.data),
  saveSettings: (body) => api.put(`${BASE}/settings`, body).then((r) => r.data),
  audit: (beforeId) =>
    api.get(`${BASE}/audit`, { params: beforeId ? { before_id: beforeId } : {} }).then((r) => r.data),
};
