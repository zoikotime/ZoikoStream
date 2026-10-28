// What Settings -> Developer says about an API key's lifetime.
//
// The server decides a key's state (crud.admin.key_lifecycle): usable while now < expires_at,
// expired from that instant on — no grace period, nothing ever extends a key — and "expiring
// soon" inside its last 30 days. Its status and expiry countdown are snapshots at generated_at.
// The page advances those snapshots from the exact expires_at instant while it remains open.
//
// `lifecycleOf` re-derives the same rule only for a payload without them (an older API), with
// exact millisecond arithmetic. It used to be Math.ceil of a day count, which read a key that
// expired a few hours ago as "Expires in 0d" and counted it as Active.

const DAY = 86_400;
export const EXPIRING_SOON_DAYS = 30;
export const URGENT_DAYS = 7;

const warningFor = (seconds) =>
  seconds <= DAY ? "imminent" : seconds <= URGENT_DAYS * DAY ? "urgent" : "notice";

/** { status, usable, seconds, warning } for one key, `seconds` measured at `nowMs`. */
export function lifecycleOf(key, nowMs = Date.now(), generatedAtMs = null) {
  if (key?.status) {
    const drift = generatedAtMs != null ? Math.round((nowMs - generatedAtMs) / 1000) : 0;
    const expiresMs = key.expires_at ? new Date(key.expires_at).getTime() : null;
    const seconds = expiresMs != null && !Number.isNaN(expiresMs)
      ? Math.floor((expiresMs - nowMs) / 1000)
      : key.seconds_until_expiry == null ? null : key.seconds_until_expiry - drift;
    if (key.status === "revoked" || key.revoked) return { status: "revoked", usable: false, seconds, warning: null };
    if (key.status === "expired" || (seconds != null && seconds <= 0)) {
      return { status: "expired", usable: false, seconds, warning: null };
    }
    if (seconds == null) {
      return { status: key.status, usable: !!key.usable, seconds, warning: key.expiry_warning || null };
    }
    if (key.status !== "active" && key.status !== "expiring_soon") {
      return { status: key.status, usable: !!key.usable, seconds, warning: key.expiry_warning || null };
    }
    if (seconds <= EXPIRING_SOON_DAYS * DAY) {
      return { status: "expiring_soon", usable: true, seconds, warning: warningFor(seconds) };
    }
    return { status: "active", usable: true, seconds, warning: null };
  }
  const expiresMs = key?.expires_at ? new Date(key.expires_at).getTime() : null;
  const seconds = expiresMs == null || Number.isNaN(expiresMs) ? null : Math.floor((expiresMs - nowMs) / 1000);
  if (key?.revoked) return { status: "revoked", usable: false, seconds, warning: null };
  if (seconds != null && seconds <= 0) return { status: "expired", usable: false, seconds, warning: null };
  if (seconds != null && seconds <= EXPIRING_SOON_DAYS * DAY) {
    return { status: "expiring_soon", usable: true, seconds, warning: warningFor(seconds) };
  }
  return { status: "active", usable: true, seconds, warning: null };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** Local calendar day number of a moment, for "tomorrow". */
const dayNumber = (ms) => {
  const d = new Date(ms);
  return Math.round(new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / (DAY * 1000));
};

/**
 * The relative line: "Expires in 27 days", "Expires tomorrow", "Expires in 4 hours",
 * "Expired 21 days ago", "Never expires". Days are rounded to the nearest day; under a day it
 * is whole hours rounded DOWN, so the last day never overstates what is left.
 */
export function expiryText(life, expiresAt, nowMs = Date.now()) {
  // A revoked key's expiry no longer means anything: it is unusable either way.
  if (life.status === "revoked") return "No longer usable";
  if (life.status === "expired" && life.seconds == null) return "Expired (date unavailable)";
  if (life.seconds == null) return "Never expires";
  const s = life.seconds;
  if (s <= 0) {
    const ago = -s;
    if (ago < 3600) return "Expired less than an hour ago";
    if (ago < DAY) return `Expired ${plural(Math.floor(ago / 3600), "hour")} ago`;
    return `Expired ${plural(Math.floor(ago / DAY), "day")} ago`;
  }
  if (s < 3600) return "Expires in less than an hour";
  if (s < DAY) return `Expires in ${plural(Math.floor(s / 3600), "hour")}`;
  if (expiresAt && dayNumber(new Date(expiresAt).getTime()) - dayNumber(nowMs) === 1) return "Expires tomorrow";
  return `Expires in ${plural(Math.max(1, Math.round(s / DAY)), "day")}`;
}

/** The exact moment in the viewer's zone, zone named: "Sep 25, 2026, 3:30 PM GMT+5:30". */
export function exactExpiry(expiresAt, { timeZone, locale } = {}) {
  if (!expiresAt) return null;
  const d = new Date(expiresAt);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    timeZone, year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    timeZoneName: "short",
  }).format(d);
}

/** The calendar date alone, for the row's second line: "Sep 25, 2026". */
export function expiryDate(expiresAt, { timeZone, locale } = {}) {
  if (!expiresAt) return null;
  const d = new Date(expiresAt);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat(locale, { timeZone, year: "numeric", month: "short", day: "numeric" }).format(d);
}

/** The hover text: the exact local moment, then the canonical UTC instant the server enforces. */
export function expiryTooltip(expiresAt) {
  const local = exactExpiry(expiresAt);
  if (!local) return undefined;
  return `${local} · ${new Date(expiresAt).toISOString().replace(".000Z", "Z")} (UTC)`;
}

/** The status badge: tone + words. Never "Active" for a key that cannot be used. */
export function badgeFor(life) {
  switch (life.status) {
    case "revoked":
      return { tone: "neutral", label: "Revoked" };
    case "expired":
      return { tone: "danger", label: "Expired" };
    case "expiring_soon":
      if (life.warning === "imminent") {
        const hours = Math.max(0, Math.floor((life.seconds ?? 0) / 3600));
        return { tone: "danger", label: hours < 1 ? "Expires within the hour" : `Expires in ${plural(hours, "hour")}` };
      }
      if (life.warning === "urgent") {
        return { tone: "warning", label: `Expires in ${plural(Math.max(1, Math.round((life.seconds ?? 0) / DAY)), "day")}` };
      }
      return { tone: "warning", label: "Expiring soon" };
    default:
      return { tone: "success", label: "Active" };
  }
}

// The filter's buckets are exactly the states the server sends — nothing aspirational.
export const STATUS_GROUPS = ["active", "expiring_soon", "expired", "revoked"];

/** The inventory counts: the server's when it sent them, else derived by the same rule. */
export function summaryOf(keys, serverSummary, lives) {
  if (serverSummary) return serverSummary;
  const usable = lives.filter((l) => l.usable);
  return {
    total: keys.length,
    active: usable.length,
    expiring_30d: usable.filter((l) => l.warning).length,
    expiring_7d: usable.filter((l) => l.warning === "urgent" || l.warning === "imminent").length,
    expired: lives.filter((l) => l.status === "expired").length,
    revoked: lives.filter((l) => l.status === "revoked").length,
    non_expiring: usable.filter((l) => l.seconds == null).length,
  };
}

/** The page-level notice, or null when no usable key is inside its last 30 days. */
export function expiryBanner(summary) {
  const within7 = summary?.expiring_7d || 0;
  const within30 = summary?.expiring_30d || 0;
  if (!within30) return null;
  const n = within7 || within30;
  const days = within7 ? URGENT_DAYS : EXPIRING_SOON_DAYS;
  return {
    urgent: within7 > 0,
    text: `${plural(n, "API key")} ${n === 1 ? "expires" : "expire"} within ${days} days. Create a replacement key and update your integration before ${n === 1 ? "it expires" : "they expire"}.`,
  };
}
