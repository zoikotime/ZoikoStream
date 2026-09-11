// Non-component module-level concerns for the dashboard, kept out of the component files so
// those export components and nothing else — a file that exports anything more loses Fast
// Refresh for the whole file (react-refresh/only-export-components).

// The ranges GET /organization/analytics already accepts: routers/organization.py pins the
// query pattern to ^(7d|30d|90d|12m)$. Three of the four are offered here — "12m" is a
// reporting window rather than a dashboard one, and stays on the full Analytics page.
export const RANGES = [
  { key: "7d", label: "Last 7 days" },
  { key: "30d", label: "Last 30 days" },
  { key: "90d", label: "Last 90 days" },
];

export const DEFAULT_RANGE = "30d";

/**
 * The first name to greet somebody by, from whatever the account actually has.
 *
 * Falls back through full_name -> the local part of the email -> null, so the header never
 * renders "Welcome back, undefined" and never renders a bare email address at 28px. Returns
 * null rather than a placeholder so the caller can drop the name entirely instead of
 * greeting someone as "there".
 */
export function greetingName(user) {
  const full = (user?.full_name || "").trim();
  if (full) return full.split(/\s+/)[0];

  const email = (user?.email || "").trim();
  if (email) {
    // "ada.lovelace@" / "ada_lovelace@" read as a name once split and capitalised;
    // "ada@" is already one.
    const first = email.split("@")[0].split(/[._-]/)[0];
    if (first) return first.charAt(0).toUpperCase() + first.slice(1);
  }
  return null;
}
