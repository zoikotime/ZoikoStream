import axios from "axios";

// In production the API and the SPA are the same origin (one container — see the Dockerfile),
// so nothing has to be baked in at build time. VITE_API_URL still overrides, and dev falls
// back to the local API on 8001 because Vite serves the client on a different port.
export const API_BASE =
  import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://localhost:8001" : window.location.origin);

// /api namespace: the SPA's own routes (/dashboard, /admin/*, /organization/*) are spelled
// like the server's router prefixes, and same-origin serving makes that a collision.
const api = axios.create({ baseURL: `${API_BASE}/api` });

// Attach the stored token to every request.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export default api;

// Turn an axios error into a readable string for toasts. Must always return a
// string: FastAPI 422s send `detail` as an array of {loc,msg,...} objects, and
// passing a non-string to toast.error() crashes React (blank screen).
export const errMsg = (e, fallback = "Something went wrong") => {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x?.msg).filter(Boolean).join(", ") || fallback;
  // Structured detail: endpoints that need the client to branch send
  // {code, message, ...} — see /auth/login and /auth/verify-email.
  if (d && typeof d === "object" && typeof d.message === "string") return d.message;
  return e?.message || fallback;
};

// The structured `code` from an error response, or null. Lets a caller branch on the
// machine-readable outcome instead of matching prose.
export const errCode = (e) => {
  const d = e?.response?.data?.detail;
  return d && typeof d === "object" && !Array.isArray(d) && d.code ? d.code : null;
};
