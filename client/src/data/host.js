// client/src/data/host.js
// Static vocabulary for the Host / Producer console. The dashboard's DATA is live (one
// WebSocket — see hooks/useEventStream.js); what remains here is labels, option lists and
// tone maps. Shared moderation vocabulary (roles, quality, activity kinds, announcement
// templates) is NOT redeclared — import it from data/moderation.js.

export { initials, hhmm, accentFor, QUALITY } from "./moderation";

// ── broadcast lifecycle ───────────────────────────────────────────────────────

export const BROADCAST_TONE = {
  preview: "neutral",
  live: "success",
  paused: "warning",
  ended: "neutral",
};

export const BROADCAST_LABEL = {
  preview: "Preview",
  live: "Live",
  paused: "Paused",
  ended: "Ended",
};

export const HEALTH_TONE = { ok: "success", warn: "warning", down: "danger" };
export const HEALTH_LABEL = { ok: "Healthy", warn: "Degraded", down: "At risk" };

export const COUNTDOWN_PRESETS = [5, 10, 30, 60];

// ── media ─────────────────────────────────────────────────────────────────────

// Labels only — the pixel dimensions live in hooks/useMediaPreview.RESOLUTIONS so the
// constraint builder and this list can't drift apart.
export const RESOLUTION_OPTIONS = [
  { value: "720p", label: "720p HD" },
  { value: "1080p", label: "1080p Full HD" },
  { value: "2k", label: "2K QHD" },
  { value: "4k", label: "4K UHD" },
];

export const FRAMERATE_OPTIONS = [
  { value: 24, label: "24 fps" },
  { value: 30, label: "30 fps" },
  { value: 60, label: "60 fps" },
];

// Recommended bitrate per resolution — shown as guidance next to the field, not enforced.
export const BITRATE_GUIDE = { "720p": 3000, "1080p": 4500, "2k": 9000, "4k": 16000 };

export const RECORDING_TONE = {
  recording: "danger",
  paused: "warning",
  stopped: "neutral",
  idle: "neutral",
  failed: "danger",
};

// Audio/video processing toggles. `native` marks the ones the browser genuinely applies via
// MediaTrackConstraints; the rest need a dependency we haven't taken, and the panel says so
// rather than offering a switch that does nothing.
export const PROCESSING_TOGGLES = [
  { key: "noise_cancellation", label: "Noise cancellation", native: true },
  { key: "echo_cancellation", label: "Echo cancellation", native: true },
  { key: "auto_gain", label: "Automatic mic gain", native: true },
  { key: "adaptive", label: "Adaptive bitrate", native: false, note: "Applied by the media server when publishing" },
];

export const BACKGROUND_OPTIONS = [
  { value: "none", label: "None" },
  { value: "blur", label: "Blur" },
  { value: "image", label: "Virtual background" },
];

// ── audience controls (host-side toggles that the server enforces) ────────────

export const CHAT_CONTROLS = [
  { key: "chat_enabled", label: "Chat enabled" },
  { key: "emoji_only", label: "Emoji-only mode" },
  { key: "subscriber_only", label: "Members only" },
  { key: "profanity_filter", label: "Profanity filter" },
  { key: "spam_filter", label: "Spam & link filter" },
  { key: "auto_moderation", label: "Auto-remove flagged" },
  { key: "reactions_enabled", label: "Reactions" },
];

export const SLOW_MODE_OPTIONS = [
  { value: 0, label: "Off" },
  { value: 5, label: "5s" },
  { value: 15, label: "15s" },
  { value: 30, label: "30s" },
  { value: 60, label: "1 min" },
];

export const STAGE_CONTROLS = [
  { key: "qa_enabled", label: "Q&A open" },
  { key: "polls_enabled", label: "Polls" },
  { key: "raise_hand_enabled", label: "Raise hand" },
  { key: "allow_screen_share", label: "Screen sharing" },
  { key: "waiting_room", label: "Waiting room" },
];

// ── header telemetry ──────────────────────────────────────────────────────────

// Network Information API effective types -> a tone the header can show.
export const NETWORK_TONE = { "4g": "success", "3g": "warning", "2g": "danger", "slow-2g": "danger" };

// Thresholds shared by the load/memory meters so both read the same way.
export const meterTone = (percent) =>
  percent == null ? "neutral" : percent < 60 ? "success" : percent < 85 ? "warning" : "danger";
