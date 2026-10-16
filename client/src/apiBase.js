// Where the API lives. Its own module so code that must not depend on the axios instance
// (the player's quality beacon, hooks/usePlaybackQoe.js — test suites mock ./api wholesale)
// can still name the same origin. api.js re-exports it unchanged.
//
// In production the API and the SPA are the same origin (one container — see the Dockerfile),
// so nothing has to be baked in at build time. VITE_API_URL still overrides, and dev falls
// back to the local API because Vite serves the client on a different port.
//
// THE FALLBACK PORT IS 8000, which is what this project actually runs the API on: .env.example
// ships `VITE_API_URL=http://127.0.0.1:8000`, server/README.md documents the docs at :8000, and
// SETUP_GUIDE.md tells you to forward Stripe webhooks to :8000. It read 8001, so the moment
// VITE_API_URL was not loaded — a dev server started before the line was added to .env, since
// Vite reads env files once at startup — every request went to a port with nothing behind it.
// Each one failed at the transport layer, which is indistinguishable from the API being down,
// and the console reported "Couldn't load billing information" on a perfectly healthy backend.
export const API_BASE =
  import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://localhost:8000" : window.location.origin);
