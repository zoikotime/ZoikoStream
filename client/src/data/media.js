// client/src/data/media.js
// Constants and formatters for the Recording & Media Library. No mock data — every figure on
// those screens comes from /media/*. This file holds only the vocabulary the API also uses
// (so a filter chip cannot offer a value the server rejects) and the display helpers.

// Library shelves. Keys match crud/media.SCOPES on the server.
export const SCOPES = [
  { key: "all", label: "Library", hint: "Everything captured and kept" },
  { key: "shared", label: "Shared", hint: "Visible to an audience outside your organization" },
  { key: "processing", label: "In progress", hint: "Capturing now, or awaiting a file" },
  { key: "failed", label: "Needs attention", hint: "Captures that produced no file" },
  { key: "archived", label: "Archive", hint: "Kept, but out of the main shelf" },
  { key: "deleted", label: "Recycle bin", hint: "Deleted and still restorable" },
];

// Keys match crud/media.SORTS.
export const SORTS = [
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "views", label: "Most viewed" },
  { key: "downloads", label: "Most downloaded" },
  { key: "size", label: "Largest" },
  { key: "duration", label: "Longest" },
  { key: "title", label: "Title (A–Z)" },
];

// Mirrors models.live.MEDIA_CATEGORIES.
export const CATEGORIES = ["Webinar", "Conference", "Training", "Town Hall", "Product", "Internal", "Other"];

// Mirrors models.live.MEDIA_VISIBILITY. The descriptions are the whole point of this list — an
// organizer choosing "Event audience" needs to know it also depends on the event's replay switch.
export const VISIBILITY = [
  { key: "private", label: "Private", hint: "Only you and organization admins" },
  { key: "organization", label: "Organization", hint: "Anyone in your organization" },
  { key: "event_audience", label: "Event audience", hint: "Anyone who could attend the event — needs replay enabled on the event" },
  { key: "public", label: "Public", hint: "Anyone signed in with the link — needs replay enabled on the event" },
];

export const DOWNLOAD_MODES = [
  { key: "allowed", label: "Allowed" },
  { key: "disabled", label: "Disabled" },
  { key: "password", label: "Password protected" },
];

// Playback speeds. 1.75× is deliberately absent — the useful jumps are these, and a longer list
// makes the menu a scroll.
export const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
// Seek step for the skip buttons and the arrow keys, in seconds.
export const SKIP_SECONDS = 10;

// ── formatters ───────────────────────────────────────────────────────────────

export const fmtBytes = (n) => {
  const bytes = Number(n || 0);
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
};

// h:mm:ss, dropping the hour when there isn't one. Used for durations AND player positions, so
// they always read the same way.
export const fmtClock = (ms) => {
  const total = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
};

// Long-form duration for cards and tables — "1h 24m" reads faster than "1:24:07" in a grid.
export const fmtDuration = (ms) => {
  const total = Math.floor(Number(ms || 0) / 1000);
  if (!total) return "—";
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return total < 60 ? `${total}s` : `${m}m`;
};

export const fmtCount = (n) => Number(n || 0).toLocaleString();

// A percentage that stays honest: null in, "—" out. Never 0%.
export const fmtPercent = (value, digits = 0) =>
  value === null || value === undefined ? "—" : `${Number(value).toFixed(digits)}%`;

// Seconds → the same long form as fmtDuration, for watch-time figures the API sends in seconds.
export const fmtWatch = (seconds) => fmtDuration(Number(seconds || 0) * 1000);

// ── status presentation ──────────────────────────────────────────────────────

// tone maps onto the Badge component's variants.
export const STATUS_TONE = {
  recording: { label: "Recording", tone: "danger" },
  paused: { label: "Paused", tone: "warning" },
  stopped: { label: "Ready", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
  idle: { label: "Idle", tone: "neutral" },
};

/** What to actually show for a row: a `stopped` recording with no bytes is not "Ready". */
export function statusOf(item) {
  if (item.deleted_at) return { label: "In recycle bin", tone: "neutral" };
  if (item.status === "stopped" && !item.has_file) {
    return { label: "No file", tone: "warning" };
  }
  if (item.archived_at) return { label: "Archived", tone: "neutral" };
  return STATUS_TONE[item.status] || { label: item.status, tone: "neutral" };
}

// ── timestamp links ──────────────────────────────────────────────────────────

/**
 * A shareable deep link to a position in a recording, e.g. /organization/recordings/<id>?t=754.
 * `t` is SECONDS, matching the convention every video platform uses, so a pasted YouTube-style
 * ?t= value behaves the way people expect.
 */
export const timestampLink = (recordingId, ms) =>
  `${window.location.origin}/organization/recordings/${recordingId}?t=${Math.floor(Number(ms || 0) / 1000)}`;

/** Parse ?t= back to milliseconds. Accepts 754, 12m34s and 1:02:03. */
export function parseTimestamp(raw) {
  const value = String(raw ?? "").trim();
  if (!value) return 0;
  if (/^\d+$/.test(value)) return Number(value) * 1000;
  const colon = value.split(":").map(Number);
  if (colon.length > 1 && colon.every((n) => Number.isFinite(n))) {
    return colon.reduce((acc, n) => acc * 60 + n, 0) * 1000;
  }
  const parts = value.match(/(\d+)\s*([hms])/gi) || [];
  const unit = { h: 3600, m: 60, s: 1 };
  return parts.reduce((acc, part) => {
    const [, n, u] = part.match(/(\d+)\s*([hms])/i);
    return acc + Number(n) * unit[u.toLowerCase()];
  }, 0) * 1000;
}
