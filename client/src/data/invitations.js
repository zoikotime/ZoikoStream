// Presentation vocabulary for invitations. Formatters + display maps only — every figure and
// every label on screen comes from the API.
//
// The status KEYS mirror server/app/models/invitation.py INVITATION_STATUSES. The server also
// sends a `status_label` per row (which is where `rejected` becomes "Declined"), so the
// labels here are only a fallback for a row that predates that field.

export const INVITATION_STATUS = {
  pending: { label: "Pending", tone: "warning", dot: true },
  accepted: { label: "Accepted", tone: "success" },
  rejected: { label: "Declined", tone: "danger" },
  expired: { label: "Expired", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  revoked: { label: "Revoked", tone: "danger" },
};

// Lifecycle order for the filter dropdown — the order an invitation actually moves through,
// not alphabetical.
export const STATUS_ORDER = ["pending", "accepted", "rejected", "expired", "cancelled", "revoked"];

// Delivery is a FACT about the email, never a lifecycle state. "Sent" means this server handed
// the message to Resend; "Delivered" means Resend's signed webhook confirmed it arrived. With
// no webhook configured a row honestly stays at "Sent" rather than claiming a delivery.
export const DELIVERY = {
  not_sent: { label: "Not emailed", tone: "neutral" },
  sent: { label: "Sent", tone: "info" },
  delivered: { label: "Delivered", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

// Roles an invitation can grant. Platform roles change what someone can reach across the
// whole organization; event roles are scoped to one event and are what the invitee is
// actually being asked to do.
export const PLATFORM_ROLES = [
  { value: "org_admin", label: "Organization Admin", hint: "Full control of the organization" },
  { value: "host", label: "Host", hint: "Can run broadcasts they're assigned to" },
  { value: "moderator", label: "Moderator", hint: "Can moderate events they're assigned to" },
  { value: "speaker", label: "Speaker", hint: "Can appear on stage when invited" },
  { value: "viewer", label: "Viewer", hint: "Can watch this organization's events" },
];

export const EVENT_ROLES = [
  { value: "host", label: "Host", hint: "Broadcast control for this event" },
  { value: "moderator", label: "Moderator", hint: "Audience moderation for this event" },
  { value: "speaker", label: "Speaker", hint: "Can be invited on stage" },
  { value: "cohost", label: "Co-Host", hint: "Credited — no broadcast control" },
  { value: "producer", label: "Producer", hint: "Credited — no broadcast control" },
  { value: "panelist", label: "Panelist", hint: "Credited — no broadcast control" },
];

const ROLE_LABEL = Object.fromEntries(
  [...PLATFORM_ROLES, ...EVENT_ROLES].map((r) => [r.value, r.label])
);
export const roleLabel = (r) => ROLE_LABEL[r] || (r || "—");

/** Days until expiry, or null when there is no usable date. Negative = already lapsed. */
export function daysUntil(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86_400_000);
}

/** "in 6 days" / "today" / "3 days ago" — the relative form an admin actually scans for. */
export function expiryHint(iso) {
  const days = daysUntil(iso);
  if (days === null) return "—";
  if (days === 0) return "today";
  return days > 0 ? `in ${days} day${days === 1 ? "" : "s"}` : `${-days} day${days === -1 ? "" : "s"} ago`;
}
