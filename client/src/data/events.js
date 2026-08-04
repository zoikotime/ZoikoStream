// Presentation helpers for events. List/detail data now comes from the /events API
// (see pages/organization/Events.jsx); this file is formatters + display maps only.

const DTF_DATE = { month: "short", day: "numeric", year: "numeric" };
const DTF_TIME = { hour: "numeric", minute: "2-digit" };

// Accepts an ISO date ("2024-05-20") or a full datetime; returns "May 20, 2024".
export const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(String(iso).length <= 10 ? `${iso}T00:00:00` : iso);
  return isNaN(d) ? "—" : d.toLocaleDateString("en-US", DTF_DATE);
};

// "May 20, 2024 · 10:00 AM"
export const fmtDateTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : `${d.toLocaleDateString("en-US", DTF_DATE)} · ${d.toLocaleTimeString("en-US", DTF_TIME)}`;
};

export const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : d.toLocaleTimeString("en-US", DTF_TIME);
};

export const fmtDuration = (mins) => {
  if (mins == null) return "—";
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h ? `${h}h${m ? ` ${m}m` : ""}` : `${m}m`;
};

// Backend EventStatus -> display label + admin Badge tone (+ pulse dot while on air).
// Keep the keys in sync with models/event.py EVENT_STATUSES — this map is the single source
// of the status vocabulary for every screen (table, detail header, KPI row, filters).
export const EVENT_STATUS = {
  draft: { label: "Draft", tone: "neutral" },
  scheduled: { label: "Scheduled", tone: "info" },
  published: { label: "Published", tone: "info" },
  live: { label: "Live", tone: "brand", pulse: true },
  // Distinct from both live and ended: the broadcast is held, not finished. Amber because
  // it is a state that needs someone's attention, not a resting state.
  paused: { label: "Paused", tone: "warning", pulse: true },
  ended: { label: "Ended", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "danger" },
  archived: { label: "Archived", tone: "neutral" },
};
export const statusMeta = (s) => EVENT_STATUS[s] || { label: s || "—", tone: "neutral" };

// Statuses in lifecycle order — used by the filter dropdown and the lifecycle rail so both
// read in the order an event actually moves through them, not alphabetically.
export const STATUS_ORDER = [
  "draft", "scheduled", "published", "live", "paused", "ended", "cancelled", "archived",
];

// An event is "on air" in both live and paused: the room exists and the audience is in it.
export const isOnAir = (s) => s === "live" || s === "paused";

export const VISIBILITY_LABEL = {
  public: "Public",
  private: "Private",
  unlisted: "Unlisted",
  invite_only: "Invite only",
};
export const visLabel = (v) => VISIBILITY_LABEL[v] || v || "—";

// What each visibility value actually MEANS for access — shown next to the picker so an
// admin is not guessing. Mirrors services/viewer.py access_for.
export const VISIBILITY_HELP = {
  public: "Any signed-in viewer with the link can watch",
  unlisted: "Not listed anywhere; any signed-in viewer with the link can watch",
  private: "Organization members only",
  invite_only: "Organization members, plus anyone holding a viewer access link",
};

// Event-team roles. host/moderator/speaker carry real authority in the live consoles
// (services/moderation.py resolve_ctx); the other three are credited team roles with no
// broadcast powers of their own — the labels say so rather than implying otherwise.
export const TEAM_ROLES = [
  { key: "host", label: "Host", plural: "Hosts", grants: "Broadcast control" },
  { key: "moderator", label: "Moderator", plural: "Moderators", grants: "Audience moderation" },
  { key: "speaker", label: "Speaker", plural: "Speakers", grants: "Can be invited on stage" },
  { key: "producer", label: "Producer", plural: "Producers", grants: "Credited — no live powers" },
  { key: "cohost", label: "Co-host", plural: "Co-hosts", grants: "Credited — no live powers" },
  { key: "panelist", label: "Panelist", plural: "Panelists", grants: "Credited — no live powers" },
];
export const roleMeta = (key) => TEAM_ROLES.find((r) => r.key === key) || { key, label: key, plural: key, grants: "" };

// Encoder targets. Same vocabulary as models/event.py STREAM_QUALITIES and
// services/broadcast.py RESOLUTIONS, so the stored value needs no translation.
export const STREAM_QUALITY = [
  { value: "720p", label: "720p · HD" },
  { value: "1080p", label: "1080p · Full HD" },
  { value: "2k", label: "2K · QHD" },
  { value: "4k", label: "4K · UHD" },
];

export const CATEGORIES = [
  "Webinar", "Conference", "Product Launch", "Workshop", "Q&A Session", "Training",
  "Town Hall", "Internal",
];

// A short list rather than the full IANA set: these cover the org's actual regions, and a
// 400-entry <select> is worse for keyboard users than eight relevant options.
export const TIMEZONES = [
  "UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London",
  "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore", "Australia/Sydney",
];

// ── Legacy mock ───────────────────────────────────────────────────────────────
// Still consumed by the public attendee pages (EventRegistration, watch/EventWatch),
// which are out of the org-admin scope. The org admin screens now use the /events API.
// ponytail: keep until those pages are wired to GET /events/{id}.
export const EVENTS = [
  { id: 1, name: "Tech Summit 2024", status: "Live", date: "2024-05-20", start: "10:00", end: "16:00", timezone: "America/New_York", host: "Ava Chen", moderators: ["Marcus Reed"], speakers: ["Priya Nair", "Leo Fischer", "Sofia Alvarez"], category: "Conference", visibility: "Public", registration: "Open", registered: 1840, viewers: 1250, accent: "violet", description: "The flagship annual gathering on where streaming infrastructure is headed — architecture deep-dives, customer stories, and live demos." },
  { id: 2, name: "Zoiko Product Launch", status: "Upcoming", date: "2024-05-25", start: "13:00", end: "14:30", timezone: "America/New_York", host: "Marcus Reed", moderators: ["Ava Chen"], speakers: ["Noah Kim"], category: "Product Launch", visibility: "Public", registration: "Open", registered: 620, viewers: null, accent: "emerald", description: "Unveiling the next generation of the ZoikoStream platform." },
  { id: 3, name: "Future of Streaming", status: "Upcoming", date: "2024-05-28", start: "09:00", end: "11:00", timezone: "Europe/London", host: "Priya Nair", moderators: [], speakers: ["Ava Chen", "Leo Fischer"], category: "Webinar", visibility: "Unlisted", registration: "Invite only", registered: 210, viewers: null, accent: "blue", description: "A panel on codecs, low-latency delivery, and what comes after HLS." },
  { id: 6, name: "Cloud Technology Webinar", status: "Completed", date: "2024-05-14", start: "10:00", end: "11:00", timezone: "America/New_York", host: "Priya Nair", moderators: ["Sofia Alvarez"], speakers: ["Marcus Reed"], category: "Webinar", visibility: "Public", registration: "Closed", registered: 4100, viewers: 3420, accent: "rose", description: "Scaling live delivery on cloud infrastructure." },
  { id: 7, name: "Customer Meet 2024", status: "Completed", date: "2024-05-10", start: "14:00", end: "17:00", timezone: "Asia/Singapore", host: "Marcus Reed", moderators: [], speakers: ["Ava Chen", "Noah Kim"], category: "Conference", visibility: "Unlisted", registration: "Closed", registered: 980, viewers: 890, accent: "emerald", description: "Annual customer appreciation and roadmap session." },
  { id: 8, name: "Security & Compliance Briefing", status: "Completed", date: "2024-04-30", start: "09:00", end: "10:00", timezone: "Europe/London", host: "Leo Fischer", moderators: ["Priya Nair"], speakers: [], category: "Internal", visibility: "Private", registration: "Invite only", registered: 540, viewers: 512, accent: "blue", description: "SOC 2, GDPR, and data-residency updates for enterprise customers." },
];

export const getEvent = (id) => EVENTS.find((e) => String(e.id) === String(id));
