// No development API address in a deployable bundle. Used by vite.config.js at build time only;
// nothing in the app imports it.
//
// The dev server and `vite build` read the same repo-root .env (vite.config.js: envDir), whose
// VITE_API_URL is the local API — http://127.0.0.1:<port>, as .env.example ships it. Left alone,
// every build on a developer's machine, or on a server whose .env began as a copy of
// .env.example, compiled that address into the bundle: each visitor's browser then called its
// own machine, and nothing in the build said so.
//
// So a loopback VITE_API_URL is development configuration. The dev server (and Vitest) use it;
// a build does not embed it. The bundle falls back to its own origin, which is where production
// serves the API (one origin; see apiBase.js and the Dockerfile). A build that targets a local
// API on purpose, e.g. `vite preview` against a local backend, opts in with
// ZOIKOSTREAM_ALLOW_LOOPBACK_API=1. Then, as a check on the output itself, the build fails if
// any emitted file still contains a loopback URL with a port, wherever it came from.
//
// Loopback only: a LAN address (192.168.x.x) is not recognised, because nothing in this repo
// configures one and a private-network deployment could legitimately use one.

// Hostnames as the WHATWG URL parser normalises them (so "127.1" arrives as "127.0.0.1" and
// "[::1]" keeps its brackets).
const LOOPBACK_HOST = /^(?:localhost|[^/]+\.localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\])$/i;

// A loopback URL WITH a port: the shape of a development API address. Library code mentions a
// bare "http://localhost" (no port) as a URL-parsing fallback, which this deliberately leaves alone.
// `\.?` because a fully qualified "localhost." / "127.0.0.1." reaches the same machine.
const LOOPBACK_URL_WITH_PORT =
  /\b(?:https?|wss?):\/\/(?:localhost|[\w-]+\.localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1?\])\.?:\d+/gi;

const TEXT_ASSET = /\.(?:html|css|js|mjs|json|webmanifest|svg|txt)$/i;

export const ALLOW_LOOPBACK_API_ENV = "ZOIKOSTREAM_ALLOW_LOOPBACK_API";

/** True when `value` is an http(s)/ws(s) URL whose host is this machine. */
export function isLoopbackUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return false;
  try {
    const { hostname } = new URL(raw.includes("://") ? raw : `http://${raw}`);
    return LOOPBACK_HOST.test(hostname.replace(/\.$/, "")); // "localhost." is localhost
  } catch {
    return false;
  }
}

/** Every distinct loopback-with-port URL in `text`. */
export function findLoopbackUrls(text) {
  return [...new Set(String(text ?? "").match(LOOPBACK_URL_WITH_PORT) ?? [])];
}

/** The build plugin: says what was left out, and refuses output that still names a loopback URL. */
export function loopbackApiGuard({ allow = false, droppedApiUrl = null } = {}) {
  return {
    name: "zoiko:no-loopback-api-in-build",
    apply: "build",
    enforce: "post",
    buildStart() {
      if (droppedApiUrl) {
        this.warn(
          `VITE_API_URL=${droppedApiUrl} is a development address, so this build does not embed it: ` +
            `the bundle calls its own origin. To build against a local API on purpose, set ` +
            `${ALLOW_LOOPBACK_API_ENV}=1.`
        );
      }
    },
    generateBundle(_options, bundle) {
      if (allow) return;
      const found = [];
      for (const [name, file] of Object.entries(bundle)) {
        let text = "";
        if (file.type === "chunk") text = file.code;
        else if (typeof file.source === "string") text = file.source;
        else if (TEXT_ASSET.test(name)) text = new TextDecoder().decode(file.source);
        for (const url of findLoopbackUrls(text)) found.push(`${name}: ${url}`);
      }
      if (found.length) {
        this.error(
          `This build would ship a local development address to every visitor:\n  ` +
            found.join("\n  ") +
            `\nProduction calls the API on the page's own origin; do not hard-code a local one. ` +
            `(${ALLOW_LOOPBACK_API_ENV}=1 allows it for a deliberate local build.)`
        );
      }
    },
  };
}
