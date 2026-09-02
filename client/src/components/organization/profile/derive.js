// Pure derivations for the Organization & Workspaces page.
//
// Everything here is computed from payloads the org console ALREADY serves
// (/organization/overview, /security, /domain, /users, /invitations, /console-state).
// No endpoint was added for this screen, so anything the platform doesn't measure is
// returned as `null` with a `note` rather than invented — the tiles render the reason.

// ── formatting ───────────────────────────────────────────────────────────────

export const fmtNum = (n) =>
  n == null ? "—" : new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(n);

// Storage arrives from the API in GB; step up to TB once it stops reading well.
export const fmtGB = (gb) => {
  if (gb == null) return "—";
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`;
  if (gb < 1 && gb > 0) return `${Math.round(gb * 1024)} MB`;
  return `${fmtNum(gb)} GB`;
};

export const fmtDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";

export const roleLabel = (role) =>
  ({
    org_admin: "Organization Owner",
    super_admin: "Super Admin",
    host: "Host",
    speaker: "Speaker",
    viewer: "Viewer",
  })[role] || (role ? role.replace(/_/g, " ") : "Member");

// Usage bars turn amber past 75% and rose past 90% — the same thresholds the
// entitlement bars on the Overview page use, so a full quota reads the same everywhere.
export const usageTone = (percent) =>
  percent == null ? "violet" : percent >= 90 ? "rose" : percent >= 75 ? "amber" : "violet";

// ── security posture ─────────────────────────────────────────────────────────

// Session-timeout options are stored as the labels the Settings form offers.
const TIMEOUT_HOURS = { "1 hour": 1, "8 hours": 8, "24 hours": 24, "7 days": 168, Never: Infinity };

// A weighted checklist over the controls this platform actually persists
// (organization.security + the verified domain). Every point is traceable to one
// setting, and the page renders the breakdown so the number is auditable rather
// than a black box.
export const SECURITY_CHECKS = [
  {
    key: "require_2fa",
    label: "Two-factor authentication required",
    weight: 25,
    passed: ({ security }) => Boolean(security?.require_2fa),
    fix: "Turn on 2FA enforcement in Settings › Security.",
  },
  {
    key: "enforce_sso",
    label: "Single sign-on enforced",
    weight: 20,
    passed: ({ security }) => Boolean(security?.enforce_sso),
    fix: "Enforce SSO so members authenticate through your identity provider.",
  },
  {
    key: "password_length",
    label: "Minimum password length of 12+",
    weight: 15,
    passed: ({ security }) => (security?.min_password_length ?? 8) >= 12,
    fix: "Raise the minimum password length to at least 12 characters.",
  },
  {
    key: "session_timeout",
    label: "Sessions expire within 8 hours",
    weight: 15,
    passed: ({ security }) => (TIMEOUT_HOURS[security?.session_timeout] ?? Infinity) <= 8,
    fix: "Shorten the session timeout to 8 hours or less.",
  },
  {
    key: "allowed_domains",
    label: "Sign-in restricted to allowed domains",
    weight: 15,
    passed: ({ security }) => Boolean(security?.allowed_domains?.trim()),
    fix: "List the email domains permitted to join this organization.",
  },
  {
    key: "domain_verified",
    label: "Organization domain verified",
    weight: 10,
    passed: ({ domain }) => Boolean(domain?.domain_verified),
    fix: "Verify your domain in Settings › General.",
  },
];

export function securityPosture({ security, domain }) {
  // Without the security payload (a 403 for a non-admin, or a failed request) there is
  // no score to show — the tile says so instead of scoring an empty object as zero.
  if (!security) {
    return { score: null, level: null, checks: [], passed: 0, total: SECURITY_CHECKS.length };
  }
  const ctx = { security, domain };
  const checks = SECURITY_CHECKS.map((c) => ({ ...c, ok: c.passed(ctx) }));
  const score = checks.reduce((sum, c) => sum + (c.ok ? c.weight : 0), 0);
  const level = score >= 85 ? "Strong" : score >= 60 ? "Moderate" : score >= 35 ? "Needs work" : "At risk";
  return { score, level, checks, passed: checks.filter((c) => c.ok).length, total: checks.length };
}

export const scoreTone = (score) =>
  score == null ? "violet" : score >= 85 ? "emerald" : score >= 60 ? "amber" : "rose";

// ── entitlement helpers ──────────────────────────────────────────────────────

// entitlements.items is a list of {label, used, limit, unit, percent}; a limit of null
// means the subscribed plan carries no ceiling for it, so no bar is drawn.
export const entitlement = (overview, label) =>
  (overview?.entitlements?.items || []).find((i) => i.label === label) || null;

// ── activity feed ────────────────────────────────────────────────────────────

const push = (out, item) => {
  if (item.at) out.push(item);
};

// A real activity feed assembled from rows that carry their own timestamps: members
// joining, invitations sent and accepted, and broadcast sessions starting and ending.
//
// Profile edits and password changes are deliberately absent — this stack keeps no
// audit trail for either, and a fabricated "you updated your profile" line would be a
// lie the timeline can't back up. The panel states that instead.
export function activityFeed({ users = [], invitations = [], sessions = [] }, limit = 8) {
  const out = [];

  for (const u of users) {
    push(out, {
      id: `member-${u.id}`,
      at: u.created_at,
      kind: "member_joined",
      title: `${u.full_name || u.email} joined the organization`,
      detail: roleLabel(u.role),
    });
  }

  for (const inv of invitations) {
    push(out, {
      id: `invite-${inv.id}`,
      at: inv.created_at,
      kind: "invite_sent",
      title: `Invitation sent to ${inv.email}`,
      detail: `${roleLabel(inv.role)}${inv.invited_by ? ` · by ${inv.invited_by}` : ""}`,
    });
    push(out, {
      id: `invite-accepted-${inv.id}`,
      at: inv.accepted_at,
      kind: "invite_accepted",
      title: `${inv.email} accepted their invitation`,
      detail: roleLabel(inv.role),
    });
  }

  for (const s of sessions) {
    push(out, {
      id: `session-${s.id}`,
      at: s.started_at,
      kind: "session_started",
      title: `Stream started · ${s.title}`,
      detail: s.peak_viewers ? `Peak ${fmtNum(s.peak_viewers)} viewers` : s.state,
    });
    push(out, {
      id: `session-ended-${s.id}`,
      at: s.ended_at,
      kind: "session_ended",
      title: `Stream ended · ${s.title}`,
      detail: s.state,
    });
  }

  return out
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, limit);
}
