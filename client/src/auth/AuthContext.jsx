/* eslint-disable react-refresh/only-export-components -- context module exports a hook alongside the provider */
import { createContext, useContext, useEffect, useState } from "react";
import api from "../api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // Only "loading" if there's a token to resolve; otherwise we're done immediately.
  const [loading, setLoading] = useState(() => !!localStorage.getItem("token"));

  // On boot, if we have a token, confirm it's still valid by fetching the user.
  useEffect(() => {
    if (!localStorage.getItem("token")) return;
    api
      .get("/auth/me")
      .then((r) => setUser(r.data))
      .catch(() => localStorage.removeItem("token"))
      .finally(() => setLoading(false));
  }, []);

  // Store token + user from a login/register response.
  const setSession = ({ access_token, user }) => {
    localStorage.setItem("token", access_token);
    setUser(user);
  };

  const logout = () => {
    localStorage.removeItem("token");
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, setSession, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
