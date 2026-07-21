// ponytail: dummy auth for the demo — NO backend. The role is inferred from the email
// so every dashboard is reachable from the single login page. Replace fakeSession() with
// the real POST /auth/login response ({ access_token, user }) when the API lands.

const titleCase = (s) =>
  s.replace(/[._-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()).trim();

// Keyword in the email local-part -> role. First match wins; default org_admin.
// Anchored to start-or-separator so "ghost@" / "supervisor@" don't false-match.
export function roleFromEmail(email) {
  const local = String(email).split("@")[0].toLowerCase();
  if (/(^|[._-])(superadmin|platform)/.test(local)) return "super_admin";
  if (/(^|[._-])host/.test(local)) return "host";
  if (/(^|[._-])(moderator|mod)/.test(local)) return "moderator";
  if (/(^|[._-])(viewer|watch|attendee)/.test(local)) return "viewer";
  return "org_admin";
}

// Build the same { access_token, user } shape the real backend returns, so AuthContext
// and roleHome() don't care that it's fake.
export function fakeSession(email, { role, full_name, organization_name } = {}) {
  const resolvedRole = role || roleFromEmail(email);
  return {
    access_token: `dummy.${resolvedRole}`, // sentinel — not a real JWT
    user: {
      id: `dummy-${resolvedRole}`,
      full_name: full_name || titleCase(email.split("@")[0]) || "ZoikoStream User",
      email,
      role: resolvedRole,
      organization_name: organization_name || "Your Organization",
    },
  };
}

// One runnable check (no test runner in this project): runs on import in dev, dropped in prod.
if (import.meta.env?.DEV) {
  console.assert(roleFromEmail("host@acme.com") === "host", "roleFromEmail host");
  console.assert(roleFromEmail("mod@acme.com") === "moderator", "roleFromEmail moderator");
  console.assert(roleFromEmail("viewer@acme.com") === "viewer", "roleFromEmail viewer");
  console.assert(roleFromEmail("superadmin@zoiko.com") === "super_admin", "roleFromEmail super");
  console.assert(roleFromEmail("ghost@acme.com") === "org_admin", "roleFromEmail no false-match");
  console.assert(roleFromEmail("jane@acme.com") === "org_admin", "roleFromEmail default");
}
