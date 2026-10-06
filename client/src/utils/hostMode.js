// Is this page being served on an organization's own custom domain?
//
// server/app/main.py adds two tags to index.html only when the request arrived on an ACTIVE
// customer hostname (server/app/services/custom_domain_routing.py decides that, by exact
// hostname match):
//   <meta name="zk-host-mode" content="custom-domain">
//   <meta name="zk-platform-origin" content="https://get.zoikostream.com">
//
// The server already refuses every non-viewer route on such a hostname. This lets the SPA
// match that after an in-app navigation (a "Back to home" link, say) instead of rendering a
// platform page that could only show errors, and send help/status links to the platform.

const meta = (name) =>
  (typeof document === "undefined" ? null : document.querySelector(`meta[name="${name}"]`)?.getAttribute("content")) || null;

export const isCustomDomain = () => meta("zk-host-mode") === "custom-domain";

/** An absolute platform URL for `path` on a custom domain, else `path` unchanged. */
export const platformHref = (path) => {
  const origin = isCustomDomain() ? meta("zk-platform-origin") : null;
  return origin ? `${origin.replace(/\/+$/, "")}${path}` : path;
};
