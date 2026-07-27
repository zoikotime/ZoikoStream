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

  // Patches the stored user (e.g. after switching the active org) without a full
  // re-login — the access token stays valid, only which org/role it resolves to changes.
  const updateUser = (patch) => {
    setUser((prev) => {
      const next = { ...prev, ...patch };
      localStorage.setItem("user", JSON.stringify(next));
      return next;
    });
  };

  return (
    <AuthContext.Provider value={{ user, loading: false, setSession, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}
