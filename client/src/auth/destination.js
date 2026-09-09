// Where a signed-in user goes, and whether a saved deep-link may be honoured.
//
// ── THE BUG THIS EXISTS TO PREVENT ───────────────────────────────────────────────────────
// `roleHome` used to map the ACCOUNT role "host" straight to /host/dashboard with no event
// id. The Producer Console then silently attached to whichever of the organization's events
// ranked highest (useLiveEvent's pickBroadcastable), and the backend — correctly — reported
// `can_host: false` for an event this person was never assigned to. The result was every
// host-persona account landing, on every login, in a read-only Producer Console for somebody
// else's event: "No host assigned", "View only", "You aren't assigned to run this event."
//
// ── ACCOUNT ROLE IS NOT EVENT ROLE ───────────────────────────────────────────────────────
// The backend models both, and they are genuinely different things:
//
//   User.role         a persona on the organization — "host", "moderator", "org_admin", ...
//                     85 accounts carry "host". It means "this person produces broadcasts",
//                     NOT "this person runs event X".
//   EventAssignment   who runs a SPECIFIC event. This is what grants broadcast control, and
//                     services/moderation.resolve_ctx computes `can_host` from it.
//
// So a "host" account with no assignment has no console to open, and the honest destination
// is the list of events they were actually put on.
import api from "../api";

// The canonical post-login destination, per ACCOUNT role.
//
// Two rules, and only two:
//
//   super_admin  -> /admin/dashboard
//   anyone else  -> /organization/dashboard
//
// NO event role appears here, and that is the point. `host`, `moderator` and `speaker` are
// EventAssignment roles: they say which broadcast somebody runs, not where their session
// begins. An earlier version mapped the host persona to /events/mine, which then auto-opened
// a single assignment — so a past assignment still became the default landing page, just via
// one more hop. A contributor route is never a default destination.
//
// `billing_admin` and `viewer` land on the organization dashboard too. That is safe and
// backend-consistent: /organization/overview and /console-state authorize with `get_my_org`
// (any member of the organization), not with require_org_admin.
const ACCOUNT_HOME = {
  super_admin: "/admin/dashboard",
};

const ORGANIZATION_HOME = "/organization/dashboard";

export const accountHome = (role) => ACCOUNT_HOME[role] || ORGANIZATION_HOME;

// Console routes that are meaningless without an event, and whose access depends on an
// EventAssignment rather than on the account role. Each maps to the capability the backend
// must confirm before the route may be entered.
export const EVENT_CONSOLES = [
  { path: "/host/dashboard", capability: "can_host" },
  { path: "/moderator/dashboard", capability: "can_moderate" },
  { path: "/speaker/backstage", capability: "can_contribute" },
];

/** The console entry a path describes, or null if it is not an event console. */
export const consoleFor = (pathname) =>
  EVENT_CONSOLES.find((entry) => entry.path === pathname) || null;

/** The ?event=<id> a saved location carries, or null. */
export const eventIdFrom = (search) => {
  try {
    return new URLSearchParams(search || "").get("event");
  } catch {
    return null;
  }
};

/**
 * Ask the server whether this user may open a console for this event.
 *
 * The ONLY source of truth. Never inferred from the account role, and never read from
 * storage — the browser cannot be allowed to answer this about itself.
 *
 * Returns the access object on success, or null when the server says no (or cannot be
 * reached, which is treated as no: failing closed on an authorization question is the only
 * safe direction).
 */
export async function fetchConsoleAccess(eventId) {
  if (!eventId) return null;
  try {
    const { data } = await api.get(`/events/${encodeURIComponent(eventId)}/assignment`);
    return data || null;
  } catch {
    // 401/403/404 (not in your organization) and network failures all mean "not now".
    return null;
  }
}

/**
 * The one authoritative post-login destination.
 *
 * `saved` is the location the user was trying to reach when they were bounced to login
 * (ProtectedRoute/RoleRoute put it in location.state.from). It is a REQUEST, not a
 * permission: a saved event-console URL is honoured only after the backend confirms the
 * assignment, and anything else falls back to the account's own home.
 *
 * Returns { to, reason } — `reason` is non-null when a deep-link was refused, so the caller
 * can say why rather than silently dropping the user somewhere else.
 */
export async function resolvePostLogin(user, saved) {
  const home = accountHome(user?.role) || "/";
  if (!saved?.pathname) return { to: home, reason: null };

  const search = saved.search || "";
  const target = `${saved.pathname}${search}`;
  const entry = consoleFor(saved.pathname);

  if (!entry) {
    // An ordinary saved page. The route's own guard still runs on arrival, so a
    // wrong-role destination bounces there rather than being pre-empted here.
    return { to: target, reason: null };
  }

  const eventId = eventIdFrom(search);
  if (!eventId) {
    // A console with no event is not a destination. This is the case that used to render
    // an arbitrary event's Producer Console.
    return { to: home, reason: null };
  }

  const access = await fetchConsoleAccess(eventId);
  if (access && access[entry.capability]) {
    return { to: target, reason: null };
  }
  return {
    to: home,
    reason: "You aren't assigned to that event, so we've taken you to your dashboard instead.",
  };
}
