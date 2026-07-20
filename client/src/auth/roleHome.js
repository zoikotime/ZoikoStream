// Where each role lands after login. Only the admin dashboard exists today;
// point speaker/viewer at their pages as they get built.
const HOMES = {
  super_admin: "/admin/dashboard",
  org_admin: "/organization/dashboard",
  speaker: "/", // ponytail: -> "/speaker" once that dashboard lands
  viewer: "/", // ponytail: -> "/watch" once that dashboard lands
};

export const roleHome = (role) => HOMES[role] || "/";
