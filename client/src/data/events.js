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

// Backend EventStatus -> display label + admin Badge tone (+ pulse dot for live).
export const EVENT_STATUS = {
  draft: { label: "Draft", tone: "neutral" },
  scheduled: { label: "Scheduled", tone: "info" },
  published: { label: "Published", tone: "info" },
  rehearsal: { label: "Rehearsal", tone: "info" },
  ready_to_arm: { label: "Ready to Arm", tone: "info" },
  armed: { label: "Armed", tone: "warning" },
  live: { label: "Live", tone: "brand", pulse: true },
  degraded: { label: "Degraded", tone: "warning", pulse: true },
  ending: { label: "Ending", tone: "warning" },
  processing: { label: "Processing", tone: "info" },
  replay_ready: { label: "Replay Ready", tone: "success" },
  ended: { label: "Ended", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "danger" },
  archived: { label: "Archived", tone: "neutral" },
  blocked: { label: "Blocked", tone: "danger" },
};
export const statusMeta = (s) => EVENT_STATUS[s] || { label: s || "—", tone: "neutral" };

export const VISIBILITY_LABEL = { public: "Public", private: "Private", unlisted: "Unlisted" };
export const visLabel = (v) => VISIBILITY_LABEL[v] || v || "—";

// What each visibility actually permits, for the Playback & Access page. Keyed to the three
// values models/event.EVENT_VISIBILITY allows — no "invite_only" entry, because this backend
// cannot produce one and a row for it would advertise a mode the API rejects.
export const VISIBILITY_HELP = {
  public: "Any signed-in viewer with the link can watch",
  unlisted: "Not listed anywhere; any signed-in viewer with the link can watch",
  private: "Organization members only",
};

// ── Legacy mock ───────────────────────────────────────────────────────────────
// Still consumed by the public attendee pages (EventRegistration, watch/EventWatch),
// which are out of the org-admin scope. The org admin screens now use the /events API.
// ponytail: keep until those pages are wired to GET /events/{id}.
export const EVENTS = [
  { id: 1, name: "Tech Summit 2024", status: "Live", date: "2024-05-20", start: "10:00", end: "16:00", timezone: "America/New_York", host: "Ava Chen", speakers: ["Priya Nair", "Leo Fischer", "Sofia Alvarez"], category: "Conference", visibility: "Public", registration: "Open", registered: 1840, viewers: 1250, accent: "violet", description: "The flagship annual gathering on where streaming infrastructure is headed — architecture deep-dives, customer stories, and live demos." },
  { id: 2, name: "Zoiko Product Launch", status: "Upcoming", date: "2024-05-25", start: "13:00", end: "14:30", timezone: "America/New_York", host: "Marcus Reed", speakers: ["Noah Kim"], category: "Product Launch", visibility: "Public", registration: "Open", registered: 620, viewers: null, accent: "emerald", description: "Unveiling the next generation of the ZoikoStream platform." },
  { id: 3, name: "Future of Streaming", status: "Upcoming", date: "2024-05-28", start: "09:00", end: "11:00", timezone: "Europe/London", host: "Priya Nair", speakers: ["Ava Chen", "Leo Fischer"], category: "Webinar", visibility: "Unlisted", registration: "Invite only", registered: 210, viewers: null, accent: "blue", description: "A panel on codecs, low-latency delivery, and what comes after HLS." },
  { id: 6, name: "Cloud Technology Webinar", status: "Completed", date: "2024-05-14", start: "10:00", end: "11:00", timezone: "America/New_York", host: "Priya Nair", speakers: ["Marcus Reed"], category: "Webinar", visibility: "Public", registration: "Closed", registered: 4100, viewers: 3420, accent: "rose", description: "Scaling live delivery on cloud infrastructure." },
  { id: 7, name: "Customer Meet 2024", status: "Completed", date: "2024-05-10", start: "14:00", end: "17:00", timezone: "Asia/Singapore", host: "Marcus Reed", speakers: ["Ava Chen", "Noah Kim"], category: "Conference", visibility: "Unlisted", registration: "Closed", registered: 980, viewers: 890, accent: "emerald", description: "Annual customer appreciation and roadmap session." },
  { id: 8, name: "Security & Compliance Briefing", status: "Completed", date: "2024-04-30", start: "09:00", end: "10:00", timezone: "Europe/London", host: "Leo Fischer", speakers: [], category: "Internal", visibility: "Private", registration: "Invite only", registered: 540, viewers: 512, accent: "blue", description: "SOC 2, GDPR, and data-residency updates for enterprise customers." },
];

export const getEvent = (id) => EVENTS.find((e) => String(e.id) === String(id));
