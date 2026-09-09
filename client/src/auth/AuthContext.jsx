/* eslint-disable react-refresh/only-export-components -- context module exports hooks alongside the provider */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import api, { AUTH_EXPIRED_EVENT } from "../api";

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
// against SECRET_KEY, checks expiry, loads the user and requires `is_active`. Anything else
// is a 401.

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

export const readToken = () => {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private mode / blocked site data. No token means no session, which is the safe answer.
    return null;
  }
};

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
  try {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to do: if storage is unavailable there is nothing stored to clear.
  }
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
  const [state, setState] = useState(() =>
    (readToken() ? { status: "loading", user: null }
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
  // leave the rest of the console believing it is still signed in.
  useEffect(() => {
    const onExpired = () => applyUnauthenticated();
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
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
  const setSession = useCallback(async ({ access_token, user }) => {
    try {
      localStorage.setItem(TOKEN_KEY, access_token);
    } catch {
      // Storage blocked: the session cannot survive a reload, but this visit still works.
    }
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
   * Sign out. Clears the credential and every stale key, then resolves to unauthenticated —
   * which makes the route guards send a protected page to /login on the next render.
   *
   * There is deliberately no server call: the platform issues a stateless JWT with an
   * expiry (security.create_access_token, shortened by the tenant's session_timeout policy)
   * and has no revocation list, so there is nothing to invalidate server-side. That is a
   * real limitation and is reported rather than papered over with a no-op request.
   */
  const logout = useCallback(() => {
    generation.current += 1;      // discard any /auth/me still in flight
    applyUnauthenticated();
    // Explicit, rather than relying on the guard to bounce the current page: a signed-out
    // user should land on the sign-in screen from wherever they were, including from a
    // public page where no guard would fire at all.
    navigate("/login", { replace: true });
  }, [applyUnauthenticated, navigate]);

  const value = useMemo(() => ({
    user: state.user,
    loading: state.status === "loading",
    authenticated: state.status === "authenticated",
    status: state.status,
    setSession,
    logout,
    refresh: validate,
  }), [state, setSession, logout, validate]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
