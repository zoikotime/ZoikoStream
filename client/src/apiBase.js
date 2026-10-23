// Where the API lives. Its own module so code that must not depend on the axios instance
// (the player's quality beacon, hooks/usePlaybackQoe.js — test suites mock ./api wholesale)
// can still name the same origin. api.js re-exports it unchanged.
//
// In production the API and the SPA are the same origin (one container — see the Dockerfile),
// so nothing has to be baked in at build time. VITE_API_URL still overrides, and dev falls
// back to the local API because Vite serves the client on a different port.
//
// THE FALLBACK PORT IS 8001, the port this project runs its local API on (server/run-dev.ps1,
// server/README.md). Port 8000 belongs to another local API on the development machine, so a
// client aimed at :8000 reaches the wrong server, and its CORS refusals look like a dropped
// connection. .env.example ships `VITE_API_URL=http://127.0.0.1:8001` and SETUP_GUIDE.md forwards
// Stripe webhooks to :8001. Keep this fallback equal to that port: when it once differed from
// the API's port, every request went to a port with nothing behind it the moment VITE_API_URL was
// not loaded (Vite reads env files once at startup), and the console reported "Couldn't load
// billing information" on a perfectly healthy backend.
export const API_BASE =
  import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://localhost:8001" : window.location.origin);
