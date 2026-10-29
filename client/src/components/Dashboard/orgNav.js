import {
  FiBarChart2, FiBriefcase, FiCalendar, FiCode, FiFilm, FiGrid, FiLifeBuoy, FiUsers,
} from "react-icons/fi";

// The organization rail (ZST-UX-NAV-001 §4, ZST-ENG-DASH-001 §3.2): seven primary
// destinations in this order, and Developers as a conditional eighth. Pure data + a resolver,
// so the rail component only draws and a test can check the contract without rendering.
//
// Every `to` is a route that exists in App.jsx. Children are listed only where a real
// destination exists — the spec's gated children (Invitations, Replay Library, Retention /
// Export, Integrations, Sandbox) have no page behind them yet, and a rail entry that leads
// nowhere is worse than none ("hide or remove a navigation entry where the capability is
// absent"). Profile is not here: it lives in the avatar menu. Streaming Sessions is not here:
// a session is a child of its event and is reached from that event's Live Control.
//
// `match` lists the path prefixes that belong to a group, so a page reached from inside a
// group (Live Inputs from an event's Preparation, the Trust pages from Support) still shows
// where you are. Visibility is display metadata only — every route and API re-checks the role.
export const ADMIN_ROLES = ["org_admin", "super_admin"];
const ANY = null;

export const ORG_NAV = [
  {
    id: "org.home", label: "Overview", to: "/organization/dashboard", icon: FiGrid, roles: ANY,
  },
  {
    id: "events.list", label: "Events", to: "/organization/events", icon: FiCalendar,
    badge: "live_events", roles: ADMIN_ROLES,
    match: ["/organization/events", "/organization/sessions", "/organization/live-inputs"],
    children: [
      { id: "events.all", label: "All events", to: "/organization/events" },
      { id: "events.calendar", label: "Calendar", to: "/organization/events?view=calendar" },
      { id: "events.live", label: "Live now", to: "/organization/events?status=on_air", badge: "live_events" },
    ],
  },
  {
    id: "audience.home", label: "Audience & Access", to: "/organization/audience", icon: FiUsers,
    roles: ADMIN_ROLES, match: ["/organization/audience", "/organization/playback"],
    children: [
      { id: "audience.overview", label: "Audience overview", to: "/organization/audience" },
      { id: "audience.access", label: "Event access", to: "/organization/audience/access", aliases: ["/organization/playback"] },
    ],
  },
  {
    id: "media.list", label: "Media & Recordings", to: "/organization/recordings", icon: FiFilm,
    roles: ADMIN_ROLES,
    children: [
      { id: "media.all", label: "All recordings", to: "/organization/recordings" },
      { id: "media.ready", label: "Replay-ready", to: "/organization/recordings?state=ready" },
      { id: "media.processing", label: "Processing status", to: "/organization/recordings?state=processing" },
    ],
  },
  {
    id: "analytics.home", label: "Analytics", to: "/organization/analytics", icon: FiBarChart2,
    roles: ADMIN_ROLES,
    children: [
      // `aria` names a child whose visible label repeats another item's ("Overview").
      { id: "analytics.overview", label: "Overview", aria: "Analytics overview", to: "/organization/analytics" },
      { id: "analytics.performance", label: "Event performance", to: "/organization/analytics#performance" },
      { id: "analytics.quality", label: "Stream quality", to: "/organization/analytics#quality" },
      { id: "analytics.data", label: "Data quality", to: "/organization/analytics#data-quality" },
    ],
  },
  {
    id: "org.manage", label: "Organization", to: "/organization/manage", icon: FiBriefcase,
    roles: ADMIN_ROLES,
    match: ["/organization/manage", "/organization/users", "/organization/security",
      "/organization/billing", "/organization/settings"],
    children: [
      { id: "org.overview", label: "Overview", aria: "Organization overview", to: "/organization/manage" },
      { id: "org.team", label: "Team & roles", to: "/organization/users" },
      { id: "org.branding", label: "Branding", to: "/organization/settings?tab=branding" },
      { id: "org.security", label: "Security & audit", to: "/organization/security" },
      { id: "org.billing", label: "Plan, usage & billing", to: "/organization/billing" },
      { id: "org.settings", label: "Organization settings", to: "/organization/settings" },
    ],
  },
  {
    id: "support.home", label: "Support", to: "/organization/support", icon: FiLifeBuoy,
    roles: ADMIN_ROLES,
  },
  {
    id: "dev.home", label: "Developers", to: "/organization/developers", icon: FiCode,
    roles: ADMIN_ROLES, feature: "developers",
    match: ["/organization/developers", "/organization/credentials"],
    children: [
      { id: "dev.platform", label: "Developer platform", to: "/organization/developers" },
      { id: "dev.credentials", label: "API credentials", to: "/organization/credentials" },
    ],
  },
];

export const isAdminRole = (role) => ADMIN_ROLES.includes(role);

function split(to) {
  const [base, hash = ""] = to.split("#");
  const [pathname, query = ""] = base.split("?");
  return { pathname, params: new URLSearchParams(query), hash };
}

const underPrefix = (pathname, prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`);

export function groupIsActive(item, pathname) {
  return (item.match || [item.to.split("?")[0]]).some((p) => underPrefix(pathname, p));
}

// The child a location means: same path, every query value (and the section hash) it names
// present, and of those the most specific — so /organization/settings?tab=branding is Branding,
// plain /settings is Organization settings, /organization/events?view=calendar is Calendar, not
// All events, and /organization/analytics#quality is Stream quality, not Overview.
export function activeChildId(item, pathname, search, hash = "") {
  const here = new URLSearchParams(search || "");
  const section = (hash || "").replace(/^#/, "");
  let best = null;
  let bestScore = -1;
  for (const child of item.children || []) {
    const { pathname: p, params, hash: childHash } = split(child.to);
    if (p !== pathname && !(child.aliases || []).includes(pathname)) continue;
    if (childHash && childHash !== section) continue;
    let ok = true;
    let score = childHash ? 1 : 0;
    params.forEach((value, key) => {
      if (here.get(key) === value) score += 1;
      else ok = false;
    });
    if (ok && score > bestScore) {
      best = child.id;
      bestScore = score;
    }
  }
  return best;
}

// What this person is shown. `features` is display metadata from /console-state; a gated item
// still appears while you are ON its page, so a deep link never leaves the rail pointing
// nowhere.
export function resolveOrgNav({ role, features = {}, pathname = "" }) {
  return ORG_NAV.filter((item) => {
    if (item.roles && !item.roles.includes(role)) return false;
    if (item.feature && !features[item.feature] && !groupIsActive(item, pathname)) return false;
    return true;
  });
}
