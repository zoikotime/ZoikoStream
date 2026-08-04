// client/src/data/moderation.js
// Static vocabulary for the Moderator Console. The dashboard's DATA is now live (one
// WebSocket, see hooks/useEventStream.js) — what's left here is the labels, tone maps and
// templates that don't come from the server.

import { ACCENT } from "../ui/tokens";

export const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

// Wall-clock label for a server ISO timestamp. Live-only items (a typing blip, an
// unpersisted join) arrive without one, so this must tolerate null rather than print
// "Invalid Date".
export const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "now";

// Stable avatar colour per person: participants arrive from the server without one, and a
// random pick would reshuffle every render.
const ACCENTS = Object.keys(ACCENT);
export const accentFor = (key = "") => {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) % 997;
  return ACCENTS[hash % ACCENTS.length];
};

// ── participants ──────────────────────────────────────────────────────────────

export const ROLE_TONE = { host: "brand", moderator: "info", speaker: "success", viewer: "neutral" };
// Host first, viewers last — the people a moderator acts on most sit at the top.
export const ROLE_ORDER = { host: 0, moderator: 1, speaker: 2, viewer: 3 };

export const PARTICIPANT_FILTERS = [
  { key: "all", label: "All" },
  { key: "hand", label: "Raised hand" },
  { key: "speaking", label: "Speaking" },
  { key: "muted", label: "Muted" },
  { key: "stage", label: "On stage" },
];

export const PARTICIPANT_SORTS = [
  { key: "role", label: "Role" },
  { key: "name", label: "Name" },
  { key: "joined", label: "Join time" },
  { key: "quality", label: "Connection" },
];

// Connection quality → dot colour + label. "lost" is reported by a client whose media
// dropped but whose socket is still up.
export const QUALITY = {
  excellent: { tone: "bg-emerald-500", label: "Excellent" },
  good: { tone: "bg-amber-500", label: "Good" },
  poor: { tone: "bg-rose-500", label: "Poor" },
  lost: { tone: "bg-slate-400", label: "No media" },
};

export const TIMEOUT_OPTIONS = [1, 5, 15, 60];

// ── lobby / stage ────────────────────────────────────────────────────────────

// Longest wait first by default — the person who has been staring at a holding screen for six
// minutes is the one to admit next.
export const LOBBY_SORTS = [
  { key: "waiting", label: "Longest wait" },
  { key: "recent", label: "Newest first" },
  { key: "name", label: "Name" },
];

// Canned private replies to a raised hand — the three sentences a moderator types all day.
export const HAND_REPLIES = [
  "We'll come to you right after this section.",
  "Please put your question in the Q&A tab and we'll read it out.",
  "We're out of time for live questions, sorry!",
];

// ── alerts ───────────────────────────────────────────────────────────────────

export const ALERT_TONE = {
  critical: {
    box: "border-rose-200 bg-rose-50/60 dark:border-rose-500/30 dark:bg-rose-500/10",
    icon: "text-rose-500",
  },
  warning: {
    box: "border-amber-200 bg-amber-50/60 dark:border-amber-500/30 dark:bg-amber-500/10",
    icon: "text-amber-500",
  },
  info: {
    box: "border-slate-200 dark:border-slate-800",
    icon: "text-slate-400",
  },
};

// ── chat ─────────────────────────────────────────────────────────────────────

// Automatic detections from services/moderation.flag_text — flags only, never auto-deleted.
// `reported` is the one flag a HUMAN adds (chat.report, a viewer action).
export const FLAG_LABELS = {
  profanity: "Profanity",
  spam: "Spam",
  link: "Link",
  duplicate: "Duplicate",
  reported: "Reported",
};

export const CHAT_FILTERS = [
  { key: "all", label: "All" },
  { key: "pending", label: "Needs review" },
  { key: "flagged", label: "Flagged" },
  { key: "reported", label: "Reported" },
  { key: "pinned", label: "Pinned" },
  { key: "highlighted", label: "Highlighted" },
];

export const QUICK_REACTIONS = ["👍", "🎉", "❤️", "😮"];

// ── Q&A ──────────────────────────────────────────────────────────────────────

export const QA_FILTERS = [
  { key: "all", label: "All" },
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "answered", label: "Answered" },
];

export const QA_STATUS_TONE = { pending: "warning", approved: "info", answered: "success", dismissed: "neutral" };

// ── polls ────────────────────────────────────────────────────────────────────

export const POLL_STATUS_TONE = { live: "success", draft: "neutral", scheduled: "warning", closed: "info" };

// Countdown presets, in seconds. 0 = no timer (moderator closes it by hand).
export const POLL_DURATIONS = [
  { value: 0, label: "No timer" },
  { value: 30, label: "30s" },
  { value: 60, label: "1 min" },
  { value: 300, label: "5 min" },
];

// ── announcements ────────────────────────────────────────────────────────────

export const ANNOUNCEMENT_PRIORITIES = [
  { key: "normal", label: "Normal", tone: "neutral" },
  { key: "important", label: "Important", tone: "warning" },
  { key: "urgent", label: "Urgent", tone: "danger" },
];

export const ANNOUNCEMENT_TEMPLATES = [
  { label: "Starting soon", text: "We're starting in 5 minutes — grab a seat! 🎬" },
  { label: "Q&A next", text: "We'll take live Q&A right after this section — drop your questions in the Q&A tab." },
  { label: "Slides", text: "Slides are available at the link in the event description 📎" },
  { label: "Recording", text: "This session is being recorded — you'll get the replay by email." },
  { label: "Tech issue", text: "We're aware of an audio issue and are fixing it now. Thanks for your patience 🙏" },
  { label: "Wrapping up", text: "That's a wrap — thank you all for joining! 👋" },
];

// ── activity feed ────────────────────────────────────────────────────────────

export const ACTIVITY_FILTERS = [
  { key: "all", label: "All" },
  { key: "mod", label: "Moderation" },
  { key: "join", label: "Joins & leaves" },
  { key: "chat", label: "Chat" },
  { key: "qa", label: "Q&A" },
  { key: "poll", label: "Polls" },
];

// Feed kinds a filter groups together (join covers leave, mod covers role changes).
export const ACTIVITY_GROUPS = {
  join: ["join", "leave"],
  mod: ["mod", "role"],
  chat: ["chat"],
  qa: ["qa"],
  poll: ["poll"],
};
