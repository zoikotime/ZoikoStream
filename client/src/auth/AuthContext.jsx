/* eslint-disable react-refresh/only-export-components -- context module exports a hook alongside the provider */
import { createContext, useContext, useEffect, useState } from "react";
import api, { AUTH_EXPIRED_EVENT } from "../api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

const TOKEN_KEY = "token";
const USER_KEY = "user";

const readStoredUser = () => {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY)) || null;
  } catch {
    return null;
  }
};

const clearStored = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};

// The session is a TOKEN plus a server-validated user, not a localStorage blob.
//
// This used to hydrate `user` from localStorage and stop there, with `loading` hardcoded
// false and no call to the API at all. That made the stored copy the source of truth for
// role-based routing, with two real consequences:
//
//   1. A role that had since changed (or been retired — /moderator/dashboard was reachable
//      purely from a stale stored role long after the account was something else) kept
//      routing the browser on the OLD value, indefinitely, because nothing ever refreshed it.
//   2. An expired or revoked token still looked like a signed-in session. Every guarded route
//      rendered, and the user only found out when individual API calls started failing.
//
// Now: the stored copy is used ONLY as an optimistic first paint, and GET /auth/me decides.
// Its response replaces the stored user, so a role change lands on the next page load.
export function AuthProvider({ children }) {
  // Both initial values are derived at MOUNT rather than corrected in the effect below. A
  // stored `user` with no token is not a session (that is how a half-cleared browser used to
  // look signed in), so it resolves to null right here — which also means the effect never has
  // to call setState synchronously to undo a wrong first guess.
  const [user, setUser] = useState(() =>
    (localStorage.getItem(TOKEN_KEY) ? readStoredUser() : null));
  // Only block on validation when there is actually a token to validate. Starting `true`
  // unconditionally would make a signed-out visitor's first paint of the PUBLIC homepage a
  // spinner (LandingOrDashboard and the route guards all wait on this).
  const [loading, setLoading] = useState(() => !!localStorage.getItem(TOKEN_KEY));

  useEffect(() => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      // Nothing to validate. `user` is already null and `loading` already false from the
      // initializers above, so this only has to sweep the orphaned key — no setState, which
      // is also what keeps this effect free of a synchronous cascading render.
      clearStored();
      return undefined;
    }

    let alive = true;
    api
      .get("/auth/me")
      .then(({ data }) => {
        if (!alive) return;
        localStorage.setItem(USER_KEY, JSON.stringify(data));
        setUser(data);
      })
      .catch((e) => {
        if (!alive) return;
        // 401 is the only answer that means "this session is finished" — the token is
        // invalid, expired, or the account is gone/deactivated. Anything else (network
        // unreachable, 500, the API still booting) is NOT evidence against the session, and
        // signing someone out over a transient blip would be its own bug. Those keep the
        // optimistic user and let the individual failing call surface its own error.
        if (e?.response?.status === 401) {
          clearStored();
          setUser(null);
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // A 401 on ANY later request means the same thing as a 401 here. api.js clears storage and
  // announces it (it has no access to React state); this is what turns that into a re-render,
  // so the route guards see a signed-out session immediately instead of on the next reload.
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  // Store token + user from a login/register response so a refresh keeps the session. The
  // user written here is the server's own response, and the effect above re-validates it on
  // the next load rather than trusting it forever.
  const setSession = ({ access_token, user: nextUser }) => {
    localStorage.setItem(TOKEN_KEY, access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(nextUser));
    setUser(nextUser);
    setLoading(false);
  };

  const logout = () => {
    clearStored();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, setSession, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
