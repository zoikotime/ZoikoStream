// In-app landing per role. "/" is the public marketing homepage.
//
// Every role has an app home now. Viewers used to map to null (fall through to the public
// marketing page) because there was nothing for them to land on — the Attendee Dashboard is that
// page: their registered, saved, live and past events in one place.
const HOMES = {
  super_admin: "/admin/dashboard",
  org_admin: "/organization/dashboard",
  host: "/host/dashboard",
  moderator: "/moderator/dashboard",
  viewer: "/attendee/dashboard",
  speaker: "/speaker/dashboard",
};

// Known roles return their mapping (which may be null); unknown roles fall back to /dashboard.
export const roleHome = (role) => (role in HOMES ? HOMES[role] : "/dashboard");
