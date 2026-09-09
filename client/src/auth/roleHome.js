// In-app landing per ACCOUNT role.
//
// This module is now a thin re-export of auth/destination.js, which owns the mapping. It
// stays because a dozen call sites import `roleHome` from here, and because the distinction
// it used to blur is worth naming in the place people look first:
//
//   ACCOUNT role      what someone is on the organization — models/user.ROLES.
//                     "host" means "this person produces broadcasts". It does NOT mean
//                     "this person runs event X".
//   EVENT role        EventAssignment. Who runs a SPECIFIC event, and the only thing that
//                     grants broadcast control (services/moderation.resolve_ctx).
//
// `host` used to map straight to /host/dashboard with no event id, so every host-persona
// account landed in a Producer Console for an event picked arbitrarily by the console
// itself. There is now exactly one default per account: super_admin to the platform
// console, everyone else to the organization dashboard. No event role appears in the map,
// because an assignment to one broadcast is not a place to start a session.
//
// ── THE RETIRED "moderator" ROLE ─────────────────────────────────────────────────────────
// The separate moderator console is gone (see App.jsx's LegacyModeratorRedirect), and a
// session minted before the role was retired — or a `users.role` row that
// server/retire_moderator_role.py has not migrated yet — can still present role="moderator".
// The previous map sent that person to the host console so they were not stranded. They are
// not stranded now either, for a better reason: like every other non-platform role they land
// on the organization dashboard, which any member of the organization may open. Nothing has
// to special-case a retired role to keep it working.
//
// `viewer` also changed, deliberately. It used to be null ("no app home", fall back to the
// public site); it is now the organization dashboard, which is safe because
// /organization/overview authorizes with get_my_org — any member — and not with
// require_org_admin. Invited viewers still watch through their event link and public viewers
// still join /e/:id with no account; neither path goes through this map.
export { accountHome as roleHome } from "./destination";
