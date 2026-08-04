// client/src/data/attendee.js
// Static vocabulary for the Attendee & Viewer experience. Everything that MOVES arrives on the
// live socket (hooks/useViewerEvent) or from /attendee/*; what lives here is labels, option lists
// and the pure helpers the dashboard needs.

import { FiGrid, FiMaximize2 } from "react-icons/fi";

// ── stage ─────────────────────────────────────────────────────────────────────
// The attendee's own composition choice, held locally. The HOST's layout is a broadcast setting
// that deliberately never reaches an attendee — picking grid over spotlight is a preference, not a
// change to the show.
export const STAGE_LAYOUTS = [
  { key: "spotlight", label: "Spotlight", icon: FiMaximize2,
    hint: "One large feed — whoever is presenting or speaking" },
  { key: "grid", label: "Grid", icon: FiGrid, hint: "Everybody, equal size" },
];

// ── reactions ─────────────────────────────────────────────────────────────────
// Must match services/attendee.REACTIONS — the server validates against that allow-list and drops
// anything else, so an emoji added here alone would simply never appear for anybody.
export const REACTIONS = ["👏", "❤️", "🔥", "🎉", "👍", "😂"];

// ── Q&A ───────────────────────────────────────────────────────────────────────

export const QA_FILTERS = [
  { key: "popular", label: "Most voted" },
  { key: "pending", label: "Awaiting an answer" },
  { key: "answered", label: "Answered" },
  { key: "mine", label: "Mine" },
  { key: "saved", label: "Saved" },
];

// ── dashboard buckets ─────────────────────────────────────────────────────────
// Order matters: an event is bucketed by the FIRST rule it matches, so "on air now" always wins
// over "today".
export const isOnAirStatus = (s) => s === "live" || s === "paused";

export const DASHBOARD_BUCKETS = [
  { key: "live", label: "Happening now", match: (e) => isOnAirStatus(e.status) },
  { key: "registered", label: "My events",
    match: (e) => e.registered && !["ended", "archived", "cancelled"].includes(e.status) },
  { key: "bookmarked", label: "Saved",
    match: (e) => e.bookmarked && !["ended", "archived", "cancelled"].includes(e.status) },
  { key: "upcoming", label: "Upcoming",
    match: (e) => ["published", "scheduled"].includes(e.status) },
  { key: "past", label: "Past", match: (e) => ["ended", "archived"].includes(e.status) },
];

/** Watch history: anything actually watched, most recent first. Derived, not a separate list —
 *  `last_joined_at` only exists once somebody has opened the media. */
export const watchHistory = (events) =>
  events
    .filter((e) => e.last_joined_at || e.watch_seconds > 0)
    .sort((a, b) => new Date(b.last_joined_at || 0) - new Date(a.last_joined_at || 0));

// ── preferences ───────────────────────────────────────────────────────────────
// Keys and values must match services/attendee.PREFERENCE_SPECS, which is a whitelist — an option
// offered here but unknown there is silently dropped on save.

export const LANGUAGES = [
  { value: "en", label: "English" }, { value: "es", label: "Español" },
  { value: "fr", label: "Français" }, { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" }, { value: "it", label: "Italiano" },
  { value: "nl", label: "Nederlands" }, { value: "hi", label: "हिन्दी" },
  { value: "ar", label: "العربية" }, { value: "zh", label: "中文" },
  { value: "ja", label: "日本語" }, { value: "ko", label: "한국어" },
];

export const TEXT_SIZES = [
  { value: "default", label: "Default" },
  { value: "large", label: "Large" },
  { value: "larger", label: "Largest" },
];

export const REMINDER_OFFSETS = [
  { value: 0, label: "At start time" },
  { value: 5, label: "5 minutes before" },
  { value: 15, label: "15 minutes before" },
  { value: 30, label: "30 minutes before" },
  { value: 60, label: "1 hour before" },
  { value: 1440, label: "1 day before" },
];

// Each switch maps to something this platform can ACTUALLY detect and deliver in-session. There is
// no push or SMS transport here, so nothing offers one — the label says "in the session" where
// that is the only place it can appear.
export const NOTIFY_SETTINGS = [
  { key: "event_starting", label: "An event I'm registered for is about to start" },
  { key: "session_starting", label: "A session goes live" },
  { key: "announcement", label: "The host makes an announcement" },
  { key: "question_answered", label: "My question is answered" },
  { key: "poll_started", label: "A poll opens" },
  { key: "hand_approved", label: "I'm invited to the stage" },
  { key: "speaker_live", label: "A speaker I follow goes live" },
  { key: "recording_available", label: "A recording becomes available" },
];

// Applied to <html> so it reaches portals and the video chrome too. Reduced motion is ALSO honoured
// by the OS-level `motion-reduce:` variants already used throughout — this is the explicit opt-in
// for somebody whose OS setting says otherwise.
export const PREFERENCE_CLASSES = {
  text_size: { large: "zk-text-large", larger: "zk-text-larger" },
  high_contrast: "zk-high-contrast",
  reduced_motion: "zk-reduced-motion",
};

// ── formatting ────────────────────────────────────────────────────────────────

export const msUntil = (iso) => (iso ? new Date(iso).getTime() - Date.now() : null);

export const fmtCountdown = (ms) => {
  if (ms == null) return "—";
  const past = ms < 0;
  const s = Math.floor(Math.abs(ms) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const body = d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
  return past ? `${body} ago` : `in ${body}`;
};

export const fmtWatched = (seconds) => {
  if (!seconds) return null;
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m watched` : `${m}m watched`;
};

/** The event's own timezone alongside the viewer's. A time shown in only one of them is how
 *  somebody in another country misses the start. */
export const fmtInZone = (iso, timezone) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const local = d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  if (!timezone) return local;
  try {
    const there = d.toLocaleString([], { dateStyle: "medium", timeStyle: "short", timeZone: timezone });
    return there === local ? local : `${local} · ${there} ${timezone}`;
  } catch {
    return local;
  }
};

export const fmtBytes = (n) => {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
};
