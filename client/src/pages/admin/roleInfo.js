// Mirrors the backend's User.ROLES tuple (server/app/models/user.py).
export const ROLES = ["super_admin", "org_admin", "billing_admin", "host", "moderator", "speaker", "viewer"];
export const roleLabel = (r) => r.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// Mirrors STAFF_COMMERCIAL_ROLES — only meaningful on a super_admin row (ZST-LE-COM-001
// Section 25). Unset means unrestricted full-access super_admin, the default for every
// existing account.
export const STAFF_COMMERCIAL_ROLES = ["sales", "finance_ops", "live_ops", "support", "security"];
