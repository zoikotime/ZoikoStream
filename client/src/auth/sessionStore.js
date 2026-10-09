// Where the sign-in credential lives, and how this browser's tabs agree about it.
//
// The SERVER decides whether a session is alive (server/app/services/auth_sessions.py:
// idle timeout, absolute lifetime, sign-out). Nothing here can extend a session — a value
// written into storage by hand is just a token the server will check and refuse. This module
// only decides where the credential is kept, and keeps tabs in step.
//
// "Remember me" decides persistence, never lifetime:
//   on  -> localStorage: survives a browser restart (still subject to idle/absolute limits)
//   off -> sessionStorage: gone when the browser closes. sessionStorage is per TAB, so a new
//          tab asks an open one for the credential over a BroadcastChannel (handoff below);
//          once every tab is closed there is no one left to ask, and sign-in is required.
//
// The channel also carries sign-out/expiry and fresh session deadlines, so one tab's
// sign-out or expiry signs out the others, and activity in one tab keeps the others from
// warning about an inactivity that is not happening.

const TOKEN_KEY = "token";
// localStorage hint: "some tab in this browser holds a browser-session credential". It holds no
// credential; it only tells a new tab that asking the others is worth the wait.
const SCOPED_HINT_KEY = "zs.session-scoped";
// sessionStorage: why this tab's session ended, shown once on the sign-in page.
const END_REASON_KEY = "zs.session-ended";
const CHANNEL_NAME = "zs-session";

export const SESSION_END_MESSAGES = {
  idle: "Your session expired due to inactivity. Please sign in again.",
  absolute: "Your session reached its maximum length. Please sign in again.",
  logout: "You have been signed out. Please sign in again.",
  revoked: "You have been signed out. Please sign in again.",
  reauth: "Please sign in again.",
};

const safe = (fn, fallback = null) => {
  try {
    return fn();
  } catch {
    // Private mode / blocked site data: behave as if nothing is stored.
    return fallback;
  }
};

export const readToken = () =>
  safe(() => localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY));

export const storeToken = (token, { remember = true } = {}) => {
  safe(() => {
    if (remember) {
      localStorage.setItem(TOKEN_KEY, token);
      sessionStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(SCOPED_HINT_KEY);
    } else {
      sessionStorage.setItem(TOKEN_KEY, token);
      localStorage.removeItem(TOKEN_KEY);
      localStorage.setItem(SCOPED_HINT_KEY, "1");
    }
  });
};

export const clearToken = () => {
  safe(() => {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(SCOPED_HINT_KEY);
  });
};

export const hasScopedHint = () => safe(() => localStorage.getItem(SCOPED_HINT_KEY) === "1", false);

// ── why a session ended ──────────────────────────────────────────────────────────────────
export const markSessionEnded = (reason) =>
  safe(() => sessionStorage.setItem(END_REASON_KEY, reason || "reauth"));
export const peekSessionEndReason = () => safe(() => sessionStorage.getItem(END_REASON_KEY));
export const clearSessionEndReason = () => safe(() => sessionStorage.removeItem(END_REASON_KEY));

// The reason carried by a structured 401 (SESSION_EXPIRED / SESSION_REVOKED), or null.
export const sessionEndReasonOf = (error) => {
  const detail = error?.response?.data?.detail;
  if (!detail || typeof detail !== "object") return null;
  if (detail.code === "SESSION_EXPIRED") return detail.reason || "reauth";
  if (detail.code === "SESSION_REVOKED") return "revoked";
  return null;
};

// ── the cross-tab channel ────────────────────────────────────────────────────────────────
let channel = null;
const listeners = new Set();

const getChannel = () => {
  if (channel) return channel;
  if (typeof BroadcastChannel === "undefined") return null;
  channel = new BroadcastChannel(CHANNEL_NAME);
  channel.onmessage = (event) => {
    for (const listener of listeners) listener(event.data || {});
  };
  return channel;
};

export const broadcast = (message) => {
  safe(() => getChannel()?.postMessage(message));
};

export const onBroadcast = (listener) => {
  getChannel();
  listeners.add(listener);
  return () => listeners.delete(listener);
};

// A tab holding a browser-session credential hands it to a new tab of the same browser.
export const answerTokenRequests = () =>
  onBroadcast((message) => {
    if (message.type !== "token-request") return;
    const token = safe(() => sessionStorage.getItem(TOKEN_KEY));
    if (token) broadcast({ type: "token", id: message.id, token });
  });

/** Ask the other tabs for a browser-session credential. Resolves to it, or null. */
export const requestTokenFromOtherTabs = (timeoutMs = 300) =>
  new Promise((resolve) => {
    if (!getChannel()) return resolve(null);
    const id = Math.random().toString(36).slice(2);
    let done = false;
    const finish = (token) => {
      if (done) return;
      done = true;
      off();
      resolve(token);
    };
    const off = onBroadcast((message) => {
      if (message.type === "token" && message.id === id && message.token) finish(message.token);
    });
    broadcast({ type: "token-request", id });
    setTimeout(() => finish(null), timeoutMs);
  });

// Test seam: forget the channel so each test starts clean.
export const __resetSessionChannelForTests = () => {
  safe(() => channel?.close());
  channel = null;
  listeners.clear();
};
