import axios from "axios";

// In production the API and the SPA are the same origin (one container — see the Dockerfile),
// so nothing has to be baked in at build time. VITE_API_URL still overrides, and dev falls
// back to the local API on 8001 because Vite serves the client on a different port.
export const API_BASE =
  import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://localhost:8001" : window.location.origin);

// /api namespace: the SPA's own routes (/dashboard, /admin/*, /organization/*) are spelled
// like the server's router prefixes, and same-origin serving makes that a collision.
// withCredentials: GET /events/:id/watch sets an httpOnly claim cookie the first time a
// private event's invite link is used (routers/events.py) — without this the browser never
// sends or stores it, and the one-device claim silently never engages. Safe cross-origin in
// dev too: the backend's CORS config already pins allow_credentials to specific origins,
// never "*".
const api = axios.create({ baseURL: `${API_BASE}/api`, withCredentials: true });

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
  return e?.message || fallback;
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
