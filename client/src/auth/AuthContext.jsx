/* eslint-disable react-refresh/only-export-components -- context module exports a hook alongside the provider */
import { createContext, useContext, useState } from "react";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

// ponytail: dummy auth — the session lives entirely in localStorage, no backend. Hydration
// is synchronous so `loading` is always false. Replace the localStorage read + setSession
// with a real /auth/me fetch + /auth/login response when the API lands.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem("user")) || null;
    } catch {
      return null;
    }
  });

  // Store token + user from a (fake) login/register response so a refresh keeps the session.
  const setSession = ({ access_token, user }) => {
    localStorage.setItem("token", access_token);
    localStorage.setItem("user", JSON.stringify(user));
    setUser(user);
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setUser(null);
  };

  // Merge partial fields (e.g. a new full_name from PATCH /auth/me) into the stored
  // user without touching the token -- unlike setSession, this isn't a fresh login.
  const updateUser = (patch) => {
    setUser((u) => {
      if (!u) return u;
      const next = { ...u, ...patch };
      localStorage.setItem("user", JSON.stringify(next));
      return next;
    });
  };

  return (
    <AuthContext.Provider value={{ user, loading: false, setSession, updateUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
