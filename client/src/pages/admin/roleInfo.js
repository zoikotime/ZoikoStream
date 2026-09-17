// Mirrors the backend's User.ROLES tuple (server/app/models/user.py). This is the FULL
// vocabulary: it is what the Identity & Access list filters by, so an operator can still find
// every existing Billing Admin, Host, Speaker and Viewer account. Nothing was removed here —
// the backend enum, the database rows and every other role surface are unchanged.
export const ROLES = ["super_admin", "org_admin", "billing_admin", "host", "speaker", "viewer"];

// The subset Identity & Access may ASSIGN, which is deliberately narrower than ROLES.
//
// Billing Admin, Host, Speaker and Viewer are not platform-level appointments — they are
// granted inside an organization (invitations, member management) or per event, which is
// where the forms that set them live and where their authorization is enforced. Offering
// them on the platform console implied the super admin console was the place to hand out an
// org's own membership, and the roles below are the only two that genuinely are platform
// decisions.
//
// Narrowing what can be GRANTED is not the same as retiring a role: UserModal still renders
// an account's existing role when it is one of the four, so editing a Host's name cannot
// silently convert them. See its roleOptions.
export const ASSIGNABLE_PLATFORM_ROLES = ["super_admin", "org_admin"];

export const roleLabel = (r) => r.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// Mirrors STAFF_COMMERCIAL_ROLES — only meaningful on a super_admin row (ZST-LE-COM-001
// Section 25). Unset means unrestricted full-access super_admin, the default for every
// existing account.
export const STAFF_COMMERCIAL_ROLES = ["sales", "finance_ops", "live_ops", "support", "security"];
