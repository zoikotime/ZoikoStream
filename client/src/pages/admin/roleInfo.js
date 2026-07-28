// Mirrors the backend's User.ROLES tuple (server/app/models/user.py).
export const ROLES = ["super_admin", "org_admin", "host", "moderator", "speaker", "viewer"];
export const roleLabel = (r) => r.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
