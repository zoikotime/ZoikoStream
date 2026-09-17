// Is this user an organization admin?
//
// A mirror of `_ROLE_RANK` / `require_min_role("org_admin")` in server/app/security.py, and
// only that: a ladder where super_admin clears every gate and an unknown role clears none.
// It exists so a screen can render the right thing BEFORE calling an admin-only endpoint —
// it is never the thing that protects the data. Every endpoint this guards
// (GET /organization/users, /invitations, /security, and the PATCH/DELETE beside them)
// re-checks server-side and still answers 403 to a direct call.
//
// The role must come from the backend-validated session (AuthContext's `user`, populated from
// GET /auth/me). A role read out of localStorage is a value the browser's owner can type, so
// it can decide what a screen LOOKS like and must never decide what anyone can reach.

// Same order and the same numbers as the server's table. -1 for anything unrecognised, so a
// role this build has not heard of is treated as least-privileged rather than most.
const RANK = { viewer: 0, speaker: 1, host: 2, org_admin: 3, super_admin: 4 };

export const roleRank = (role) => RANK[role] ?? -1;

/** True for org_admin and super_admin — exactly who require_org_admin admits. */
export const isOrgAdmin = (user) => roleRank(user?.role) >= RANK.org_admin;
