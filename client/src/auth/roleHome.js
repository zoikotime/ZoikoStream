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
  moderator: "/moderator/dashboard",
  viewer: null,
  speaker: "/dashboard", // ponytail: legacy role; -> "/speaker" once that dashboard lands
};

// Known roles return their mapping (which may be null); unknown roles fall back to /dashboard.
export const roleHome = (role) => (role in HOMES ? HOMES[role] : "/dashboard");
