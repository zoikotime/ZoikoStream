// Which public pages show Guide, and the public-only page context each one sends
// (PRD §5.1, §19.1). Nothing here is account- or event-specific: the path is sent without
// its query string (a viewer link may carry an invitation credential) and the server strips
// it again regardless.

const RULES = [
  { match: (p) => p === "/", page_id: "home", page_type: "home" },
  { match: (p) => p === "/contact", page_id: "contact", page_type: "contact" },
  { match: (p) => p === "/status", page_id: "status", page_type: "status" },
  { match: (p) => p === "/trust", page_id: "trust", page_type: "trust" },
  { match: (p) => p === "/privacy", page_id: "privacy", page_type: "privacy" },
  { match: (p) => /^\/events\/[^/]+\/watch\/?$/.test(p), page_id: "viewer", page_type: "viewer" },
];

export function guidePageFor(pathname) {
  const rule = RULES.find((r) => r.match(pathname));
  return rule ? { page_id: rule.page_id, page_type: rule.page_type } : null;
}

export function deviceClass() {
  const w = typeof window !== "undefined" ? window.innerWidth : 1280;
  if (w < 768) return "mobile";
  if (w < 1024) return "tablet";
  return "desktop";
}

export function buildPageContext(pathname, page, { entryPoint = "launcher", eventToken = null } = {}) {
  let timezone;
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    timezone = null;
  }
  return {
    page_id: page.page_id,
    page_type: page.page_type,
    path: pathname || "/",
    locale: "en-US",
    ...(timezone ? { timezone } : {}),
    device_class: deviceClass(),
    entry_point: entryPoint,
    ...(eventToken ? { public_event_context_token: eventToken } : {}),
  };
}

// Public-safe event context hand-off. A viewer page that has a signed
// public_event_context_token (issued by the server for that event) publishes it here; Guide
// never derives event context from the URL or any private API (PR-020, API §18).
let eventToken = null;
const listeners = new Set();
export function setGuideEventContextToken(token) {
  eventToken = token || null;
  listeners.forEach((fn) => fn(eventToken));
}
export const getGuideEventContextToken = () => eventToken;
export function onGuideEventContextToken(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
