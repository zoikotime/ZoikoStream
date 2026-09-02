// In-app landing per role. "/" is the public marketing homepage.
//
// Viewers intentionally have NO app dashboard (value is null):
//   • invited viewers watch via their event link (AcceptInvitation -> /events/:id/watch);
//   • public viewers join a public event link (/e/:id) with no account at all.
// Callers treat null as "no app home" and fall back to the public site.
const HOMES = {
  super_admin: "/admin/dashboard",
  org_admin: "/organization/dashboard",
  host: "/host/dashboard",
  viewer: null,
  speaker: "/speaker/backstage",
  // "moderator" is a RETIRED role. It is still listed — mapped to the host console, not to
  // the deleted /moderator/dashboard — because a session minted before the role was retired
  // (or a `users.role` row not yet migrated by the backend's retire_moderator_role.py) can
  // still present role="moderator". Without an entry here it would fall through to the
  // generic /dashboard below, stranding someone who holds a perfectly valid host
  // EventAssignment. Routing them at the host console is honest: the server decides what
  // they may actually do there (canHost/canModerate from resolve_ctx), and a legacy
  // moderator assignment resolves to audience management without broadcast control.
  moderator: "/host/dashboard",
};

// Known roles return their mapping (which may be null); unknown roles fall back to /dashboard.
export const roleHome = (role) => (role in HOMES ? HOMES[role] : "/dashboard");
