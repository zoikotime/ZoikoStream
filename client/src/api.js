import axios from "axios";
import { API_BASE } from "./apiBase";
import { clearToken, markSessionEnded, readToken, sessionEndReasonOf } from "./auth/sessionStore";

// API_BASE lives in ./apiBase (see there for the fallback-port story); re-exported here for
// every existing importer.
export { API_BASE };

// /api namespace: the SPA's own routes (/dashboard, /admin/*, /organization/*) are spelled
// like the server's router prefixes, and same-origin serving makes that a collision.
// withCredentials: GET /events/:id/watch sets an httpOnly claim cookie the first time a
// private event's invite link is used (routers/events.py) — without this the browser never
// sends or stores it, and the one-device claim silently never engages. Safe cross-origin in
// dev too: the backend's CORS config already pins allow_credentials to specific origins,
// never "*".
// A request that never settles is worse than one that fails: every caller in this app shows a
// pending state while it waits, so a socket the server never answers leaves a button reading
// "Submitting…" or "Redirecting to Stripe…" until the page is reloaded. 30s is well past any
// honest response here (the slowest is Stripe checkout session creation) and well inside a
// person's patience. A timeout surfaces as ECONNABORTED, which errMsg names below.
const api = axios.create({ baseURL: `${API_BASE}/api`, withCredentials: true, timeout: 30_000 });

// Attach the stored token to every request — from wherever "Remember me" put it
// (auth/sessionStore). A request that already names its own credential keeps it.
api.interceptors.request.use((config) => {
  const token = readToken();
  if (token && !config.headers.Authorization) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Broadcast when a request proves the stored session is dead. This module cannot touch React
// state, so AuthContext listens for this and drops `user`, which is what makes the route
// guards react immediately rather than on the next page load.
export const AUTH_EXPIRED_EVENT = "zoiko:auth-expired";

// Endpoints where a 401 is the ANSWER, not a verdict on the caller's session: signing in with
// the wrong password, redeeming a spent verification/reset challenge. Clearing the session on
// those would be wrong in the one place a user is actively trying to establish one.
//
// `/auth/me` is in the list for a different reason: AuthContext owns that call and handles
// its own 401, including the startup case where a 401 is the expected, normal answer. Letting
// the interceptor also fire on it would clear storage underneath the very code deciding what
// to do about it.
//
// `/auth/logout` too: signing out of a session the server already ended is still a sign-out,
// not an expiry to announce. And `/auth/step-up`: its 401 means "that password was wrong",
// which must never end the session the user is re-authenticating within.
const AUTH_ENDPOINT = /^\/auth\/(me|login|logout|step-up|register|forgot-password|reset-password|verify-email|verify-otp|resend-verification|recover)/;

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const { status, config } = { status: error?.response?.status, config: error?.config };
    // Three conditions, all required:
    //   • 401 specifically. A 403 means "authenticated but not allowed" — a real answer about
    //     permissions, and logging someone out for hitting a page above their role would be a
    //     bug, not security.
    //   • The request carried THE token this tab holds now. Without a token at all, a 401
    //     from a public, token-less call (a bad delivery/registration link) would clear a
    //     perfectly good session belonging to whoever happened to be signed in. With an
    //     OLDER one — a request still in flight from a session that has since ended, answered
    //     after the user signed in again — the 401 is about that old session; acting on it
    //     cleared the new sign-in and told every tab it had ended. And once this tab has
    //     already signed out, the remaining 401s of a burst have nothing left to announce.
    //   • Not one of the sign-in endpoints above.
    const sent = config?.headers?.Authorization;
    const current = readToken();
    const aboutCurrentSession = !!sent && !!current && sent === `Bearer ${current}`;
    const path = config?.url || "";
    if (status === 401 && aboutCurrentSession && !AUTH_ENDPOINT.test(path)) {
      clearToken();
      try {
        // Legacy key from the build that treated a stored profile as proof of identity.
        // AuthContext.clearStoredAuth sweeps the rest; this keeps the two in step for the
        // one key that mattered.
        localStorage.removeItem("user");
      } catch { /* private mode / blocked site data — nothing stored to clear */ }
      // The server says WHY (SESSION_EXPIRED idle/absolute, SESSION_REVOKED): kept for the
      // sign-in page, and carried to the other tabs. Never a database or server error.
      const reason = sessionEndReasonOf(error) || "reauth";
      markSessionEnded(reason);
      // No retry and no redirect — this only clears state and announces it. A retry would
      // fail identically (the session is over), and looping is exactly the failure mode a
      // response interceptor invites; the route guards own where to send the user.
      window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT, { detail: { reason } }));
    }
    return Promise.reject(error);
  },
);

export default api;

// Cloudflare's own edge errors (HTTP 520-530). These are not answers from ZoikoStream: the
// request died between Cloudflare and our server. Cloudflare sends API clients a JSON body
// whose `detail` is its own prose ("The SSL/TLS handshake between Cloudflare and the origin
// server failed ... missing shared cipher suites ..."), and errMsg used to print that verbatim
// as if the settings themselves had been refused.
//
// Whether the change could have been applied depends on WHERE it failed, so the message says
// so rather than guessing: a refused connection or TLS handshake means the request was never
// delivered; a timeout or garbled reply after connecting means it may have been.
const EDGE_NOT_DELIVERED = new Set([521, 522, 523, 525, 526]);
export const edgeErrorMessage = (e) => {
  const status = e?.response?.status;
  if (!(status >= 520 && status <= 530)) return null;
  if (status === 525 || status === 526) {
    return `The secure connection to the ZoikoStream server could not be established (edge error ${status}). The request was not delivered and nothing was changed. Please try again in a moment.`;
  }
  if (EDGE_NOT_DELIVERED.has(status)) {
    return `The ZoikoStream server could not be reached (edge error ${status}). The request was not delivered and nothing was changed. Please try again in a moment.`;
  }
  return `The ZoikoStream server did not answer normally (edge error ${status}). The change may or may not have been applied — reload the page to check before trying again.`;
};

// Turn an axios error into a readable string for toasts. Must always return a
// string: FastAPI 422s send `detail` as an array of {loc,msg,...} objects, and
// passing a non-string to toast.error() crashes React (blank screen).
// A request that never received a response at all. axios reports both of these as a bare
// `message`, and `Network Error` in particular is the string the browser hands over for ANY
// blocked request — a dropped connection, a DNS failure, and equally a response the browser
// refused to deliver because it carried no CORS headers (which is what an unhandled 500 looked
// like until app/main.py started answering those inside the CORS layer).
//
// Printed verbatim it names the wrong layer and reads as a verdict on the user's own
// connection. Worse, it ends up interpolated into sentences that assert something about their
// account — Billing's "Current subscription detected. Network Error" told people their
// subscription was in trouble when the only fact in evidence was that one request got no
// answer. These say what is actually known: we could not get an answer, and nothing changed.
//
// In a DEVELOPMENT build the unreachable message also names the API address the request went
// to. A dev client pointed at the wrong server — another local project already listening on
// the same port, or a stale VITE_API_URL — fails exactly like a dropped connection, because the
// browser hides the CORS refusal from the page, and that address is the one fact that tells the
// two apart. A production build keeps the plain sentence.
const devApiHint = () =>
  import.meta.env.DEV
    ? ` (Development: the request went to ${API_BASE}. Make sure the ZoikoStream API is the server running there.)`
    : "";

export const transportMessage = (e) => {
  if (e?.response) return null;                 // the server DID answer; not a transport fault
  if (e?.code === "ECONNABORTED" || e?.code === "ETIMEDOUT") {
    return "The server took too long to respond, so nothing was submitted. Please try again.";
  }
  if (e?.code === "ERR_CANCELED") return null;  // we aborted it; the caller decides what to say
  if (e?.request) {
    return `Couldn't reach the ZoikoStream server, so nothing was submitted. Check your connection and try again.${devApiHint()}`;
  }
  return null;
};

export const errMsg = (e, fallback = "Something went wrong") => {
  const edge = edgeErrorMessage(e);
  if (edge) return edge;
  const transport = transportMessage(e);
  if (transport) return transport;
  const data = e?.response?.data;
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x?.msg).filter(Boolean).join(", ") || fallback;
  // Structured detail: endpoints that need the client to branch send
  // {code, message, ...} — see /auth/login, /auth/verify-email, and /organization/users.
  if (d && typeof d === "object") {
    if (typeof d.message === "string") return d.message;
    if (typeof d.detail === "string") return d.detail;
  }
  if (data && typeof data === "object") {
    if (typeof data.message === "string") return data.message;
    if (typeof data.error === "string") return data.error;
  }
  return e?.message || fallback;
};

// The structured `code` from an error response, or null. Lets a caller branch on the
// machine-readable outcome instead of matching prose.
export const errCode = (e) => {
  const data = e?.response?.data;
  const d = data?.detail;
  if (d && typeof d === "object" && !Array.isArray(d) && d.code) return d.code;
  if (data && typeof data === "object" && data.code) return data.code;
  return null;
};

// A failed console load needs a diagnosis, not just "couldn't load X": a 404 means the
// running server predates this endpoint, 401/403 means the session expired, and a missing
// status means the API is unreachable — each sends an operator to a different fix, so an
// ops console (where "why" matters mid-incident) should never collapse them into one
// generic message. `endpointPath` names the route being loaded, e.g. "/admin/roles".
export const diagnoseLoadError = (e, endpointPath) => {
  const status = e?.response?.status;
  if (status === 404) {
    return `The API responded, but doesn’t have ${endpointPath} — the server is running an older build. Restart it to pick up the current code.`;
  }
  if (status === 401 || status === 403) {
    return "Your session isn’t authorised for the platform console. Sign in again as a super admin.";
  }
  if (status) return `The platform API returned ${status}: ${errMsg(e)}`;
  return "The platform API is unreachable — check that the API server is running and that VITE_API_URL points at it.";
};
