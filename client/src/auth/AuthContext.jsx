/* eslint-disable react-refresh/only-export-components -- context module exports hooks alongside the provider */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import api, { AUTH_EXPIRED_EVENT } from "../api";
import SessionExpiryWarning from "./SessionExpiryWarning";
import {
  answerTokenRequests,
  broadcast,
  clearToken,
  hasScopedHint,
  markSessionEnded,
  onBroadcast,
  readToken as readStoredToken,
  requestTokenFromOtherTabs,
  sessionEndReasonOf,
  storeToken,
} from "./sessionStore";
import { useSessionKeeper } from "./useSessionKeeper";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

// Auth state, validated by the SERVER on every page load.
//
// ── WHAT THIS USED TO DO, AND WHY IT WAS A SECURITY BUG ──────────────────────────────────
// The previous version seeded `user` synchronously from `localStorage.getItem("user")` and
// hardcoded `loading: false`. That made a JSON blob in the browser the sole proof of
// authentication: anyone could open devtools, write `{"role":"org_admin"}` into that key and
// land on /organization/dashboard, and — the symptom actually reported — a stale blob left
// over from an old session opened the dashboard with no sign-in at all. The route guards
// were never the problem; they correctly honour `user`/`loading`. They were being handed a
// lie.
//
// ── WHAT IT DOES NOW ─────────────────────────────────────────────────────────────────────
// Three explicit states, and the app starts in the first one:
//
//   loading        true until the server has answered. Guards render a spinner, never
//                  protected content, so nothing flashes before validation completes.
//   authenticated  derived from `user`, which ONLY ever comes from GET /api/auth/me.
//   user           the server's answer. Never read from localStorage into rendered state.
//
// localStorage still holds the bearer token, because that is how api.js authenticates a
// request — but it is a CREDENTIAL CACHE, not an assertion of identity. A token that the
// server rejects is cleared, along with any other stale auth keys.
//
// The backend does the real work: /api/auth/me runs get_current_user, which decodes the JWT
// against SECRET_KEY, loads the user, requires `is_active`, and checks the token's
// SERVER-SIDE SESSION — unrevoked, inside its absolute lifetime, and not idle for longer than
// the idle timeout (server/app/services/auth_sessions.py). Anything else is a 401, so a token
// left in a browser that was closed hours ago is refused here and the user signs in again.
//
// Sessions: useSessionKeeper reports genuine activity and schedules the inactivity warning;
// sessionStore keeps the credential where "Remember me" says (localStorage, or this browser
// session only) and keeps tabs in step — one tab's sign-out or expiry signs out the rest.

const TOKEN_KEY = "token";
// `user` is legacy: previous builds stored the whole profile here and trusted it. It is no
// longer read, and is actively removed wherever auth state is cleared so an old browser
// cannot keep carrying it around.
const STALE_AUTH_KEYS = [
  // Legacy identity blobs a previous build trusted as proof of authentication.
  "user", "isAuthenticated", "organization", "role", "auth",
  // Event/console navigation state. Nothing reads these for authorization — console access
  // is decided by GET /events/{id}/assignment and nothing else — but they are cleared so a
  // browser cannot carry a stale event or contributor role across sessions and so no future
  // change can be tempted to read one.
  "eventRole", "event", "lastEvent", "assignedEvent", "host", "contributorRole",
  "lastRoute", "returnTo", "redirectTo", "pendingRedirect", "intendedRoute",
];

// The stored credential, wherever "Remember me" put it. Private mode / blocked site data reads
// as no token, which is the safe answer.
export const readToken = () => readStoredToken();

/** Drop the legacy keys, leaving the credential alone. */
const clearStaleKeys = () => {
  try {
    for (const key of STALE_AUTH_KEYS) {
      localStorage.removeItem(key);
      sessionStorage.removeItem(key);
    }
  } catch {
    // Nothing to do: if storage is unavailable there is nothing stored to clear.
  }
};

/** Remove every trace of a session from this browser. Safe to call when there is none. */
export const clearStoredAuth = () => {
  clearStaleKeys();
  clearToken();
};

// api.js dispatches this when any request comes back 401 — a session that expires or is
// revoked mid-visit must not leave the console rendering as though it were still valid.
//
// The constant lives in api.js (which is what fires it) and is re-exported here under the
// name this module used, so both halves of the merge agree on ONE event rather than two that
// silently miss each other.
export { AUTH_EXPIRED_EVENT } from "../api";
export const SESSION_EXPIRED_EVENT = AUTH_EXPIRED_EVENT;

export function AuthProvider({ children }) {
  // Starts LOADING whenever there is a credential to check — the difference between
  // "unauthenticated" and "not yet known" is the whole fix, because a route guard must never
  // treat the second as the first.
  //
  // With NO token there is nothing to validate, so it starts resolved. That is not a
  // shortcut: the absence of a credential is a complete answer, and it is what keeps a
  // signed-out visitor's first paint of the PUBLIC landing page from being a spinner
  // (LandingOrDashboard and every guard wait on `loading`). The dangerous direction — a
  // stored blob being treated as proof — is still impossible: `user` only ever comes from
  // /auth/me.
  //
  // The one other reason to start loading: a browser-session sign-in ("Remember me" off)
  // lives in ONE tab's sessionStorage, so a new tab asks the open ones for it first
  // (sessionStore.requestTokenFromOtherTabs). The hint that makes that worth asking holds no
  // credential, and without it a signed-out visitor still gets an immediate first paint.
  const [state, setState] = useState(() =>
    (readToken() || hasScopedHint() ? { status: "loading", user: null }
                                    : { status: "unauthenticated", user: null }));
  // Guards against a late /auth/me response overwriting a newer login/logout.
  const generation = useRef(0);
  const navigate = useNavigate();

  const applyUnauthenticated = useCallback(() => {
    clearStoredAuth();
    setState({ status: "unauthenticated", user: null });
  }, []);

  /**
   * Ask the server who we are. The ONLY way `user` is ever populated.
   *
   * A 401/403 means the token is missing, expired, malformed, forged, or belongs to a
   * deactivated user — all of which are "not signed in", so stale state is cleared. A
   * network/5xx failure is different: the session may well be valid and the API is simply
   * unreachable, so the token is KEPT and we resolve to unauthenticated for this page load
   * rather than silently signing the user out of a working session.
   */
  const validate = useCallback(async () => {
    const mine = ++generation.current;
    if (!readToken() && hasScopedHint()) {
      // A browser-session sign-in held by another open tab of this browser. With every tab
      // closed nobody answers, and sign-in is required — which is what "Remember me" off means.
      const handed = await requestTokenFromOtherTabs();
      if (mine !== generation.current) return null;
      if (handed) storeToken(handed, { remember: false });
    }
    if (!readToken()) {
      // No credential to present. Clear any legacy keys a previous build may have left.
      if (mine === generation.current) applyUnauthenticated();
      return null;
    }
    try {
      const { data } = await api.get("/auth/me");
      if (mine !== generation.current) return null;
      // Sweep the legacy keys on the SUCCESS path too. They are never read, but a browser
      // that carried one would otherwise keep it for the life of a valid session — and a
      // key that lingers is a key some future change can be tempted to trust.
      clearStaleKeys();
      setState({ status: "authenticated", user: data });
      return data;
    } catch (error) {
      if (mine !== generation.current) return null;
      const status = error?.response?.status;
      if (status === 401 || status === 403) {
        // A reopened browser whose session expired while it was closed lands on /login with
        // the reason, not a bare sign-in form.
        const reason = sessionEndReasonOf(error);
        if (reason) markSessionEnded(reason);
        applyUnauthenticated();
      } else {
        setState({ status: "unauthenticated", user: null });
      }
      return null;
    }
  }, [applyUnauthenticated]);

  // Startup: validate before anything protected can render.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    validate();
  }, [validate]);

  // A 401 anywhere in the app ends the session here too, so one expired request cannot
  // leave the rest of the console believing it is still signed in — and the other tabs are
  // told, so none of them keeps showing privileged data on a session the server has ended.
  useEffect(() => {
    const onExpired = (event) => {
      broadcast({ type: "ended", reason: event?.detail?.reason || "reauth" });
      applyUnauthenticated();
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, [applyUnauthenticated]);

  // Another tab signed out, or found the session ended: this tab ends too (sessionStorage
  // credentials never fire `storage`, so the channel is what reaches them). And this tab
  // hands its browser-session credential to a new tab that asks.
  useEffect(() => {
    const offAnswer = answerTokenRequests();
    const offEnded = onBroadcast((message) => {
      if (message.type !== "ended") return;
      generation.current += 1;   // discard any /auth/me still in flight
      markSessionEnded(message.reason || "reauth");
      applyUnauthenticated();
    });
    return () => {
      offAnswer();
      offEnded();
    };
  }, [applyUnauthenticated]);

  // Signing out in one tab signs out the others. `storage` fires only in OTHER tabs, so
  // this cannot loop, and it re-validates rather than trusting the new value.
  useEffect(() => {
    const onStorage = (event) => {
      if (event.key !== null && event.key !== TOKEN_KEY) return;
      validate();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [validate]);

  /**
   * Adopt the session from a successful /auth/login or invitation-accept response.
   *
   * The token is stored and then CONFIRMED with the server before the user is treated as
   * signed in — so even here, rendered state comes from /auth/me rather than from a payload
   * the client happens to hold. `response.user` is used only as the immediate answer for the
   * caller's redirect.
   */
  //
  // `remember` ("Remember me") decides only where the credential is kept: across browser
  // restarts, or for this browser session. The server's idle and absolute limits apply
  // either way — remembering never makes a session last longer.
  const setSession = useCallback(async ({ access_token, user }, { remember = true } = {}) => {
    // Storage blocked: the session cannot survive a reload, but this visit still works.
    storeToken(access_token, { remember });
    // Clear anything a previous build left behind, now that a real session exists.
    for (const key of STALE_AUTH_KEYS) {
      try {
        localStorage.removeItem(key);
        sessionStorage.removeItem(key);
      } catch { /* storage unavailable */ }
    }
    const confirmed = await validate();
    return confirmed || user || null;
  }, [validate]);

  /**
   * Sign out. Revokes THIS session on the server (POST /auth/logout), so the token is refused
   * from now on even if a copy survives somewhere; then clears the credential and every stale
   * key and resolves to unauthenticated — which makes the route guards send a protected page
   * to /login on the next render. The other tabs are told; the user's other devices are not
   * affected (each sign-in is its own session).
   *
   * The server call is sent with the token in hand, because local state is cleared at once:
   * signing out must not wait on the network, and a session the server has already ended
   * needs no revoking.
   */
  const logout = useCallback(() => {
    generation.current += 1;      // discard any /auth/me still in flight
    const token = readToken();
    applyUnauthenticated();
    broadcast({ type: "ended", reason: "logout" });
    if (token) {
      Promise.resolve(api.post("/auth/logout", null, { headers: { Authorization: `Bearer ${token}` } }))
        .catch(() => { /* already ended, or offline: local sign-out stands */ });
    }
    // Explicit, rather than relying on the guard to bounce the current page: a signed-out
    // user should land on the sign-in screen from wherever they were, including from a
    // public page where no guard would fire at all.
    navigate("/login", { replace: true });
  }, [applyUnauthenticated, navigate]);

  // The SERVER said this session is over (idle, maximum length, signed out elsewhere): say why
  // on the sign-in page, end it in every tab, and let the route guards send protected pages
  // to /login. Public pages simply carry on signed out.
  const endSession = useCallback((reason) => {
    generation.current += 1;
    markSessionEnded(reason);
    broadcast({ type: "ended", reason });
    applyUnauthenticated();
  }, [applyUnauthenticated]);

  const keeper = useSessionKeeper({ authenticated: state.status === "authenticated", onEnded: endSession });

  // Moving around the app is activity. The first render is not (loading a page is not).
  const location = useLocation();
  const seenPath = useRef(location.pathname);
  const { noteNavigation } = keeper;
  useEffect(() => {
    if (seenPath.current === location.pathname) return;
    seenPath.current = location.pathname;
    noteNavigation();
  }, [location.pathname, noteNavigation]);

  const value = useMemo(() => ({
    user: state.user,
    loading: state.status === "loading",
    authenticated: state.status === "authenticated",
    status: state.status,
    setSession,
    logout,
    refresh: validate,
  }), [state, setSession, logout, validate]);

  return (
    <AuthContext.Provider value={value}>
      {children}
      {keeper.warning && state.status === "authenticated" && (
        <SessionExpiryWarning
          expiresAt={keeper.warning.expiresAt}
          onStay={keeper.staySignedIn}
          onSignOut={logout}
        />
      )}
    </AuthContext.Provider>
  );
}
