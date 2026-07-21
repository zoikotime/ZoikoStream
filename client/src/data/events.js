// ponytail: mock data — swap for GET /organization/events when the backend lands.
export const EVENTS = [
  { id: 1, name: "Tech Summit 2024", status: "Live", date: "2024-05-20", start: "10:00", end: "16:00", timezone: "America/New_York", host: "Ava Chen", moderators: ["Marcus Reed"], speakers: ["Priya Nair", "Leo Fischer", "Sofia Alvarez"], category: "Conference", visibility: "Public", registration: "Open", registered: 1840, viewers: 1250, accent: "violet", description: "The flagship annual gathering on where streaming infrastructure is headed — architecture deep-dives, customer stories, and live demos." },
  { id: 2, name: "Zoiko Product Launch", status: "Upcoming", date: "2024-05-25", start: "13:00", end: "14:30", timezone: "America/New_York", host: "Marcus Reed", moderators: ["Ava Chen"], speakers: ["Noah Kim"], category: "Product Launch", visibility: "Public", registration: "Open", registered: 620, viewers: null, accent: "emerald", description: "Unveiling the next generation of the ZoikoStream platform." },
  { id: 3, name: "Future of Streaming", status: "Upcoming", date: "2024-05-28", start: "09:00", end: "11:00", timezone: "Europe/London", host: "Priya Nair", moderators: [], speakers: ["Ava Chen", "Leo Fischer"], category: "Webinar", visibility: "Unlisted", registration: "Invite only", registered: 210, viewers: null, accent: "blue", description: "A panel on codecs, low-latency delivery, and what comes after HLS." },
  { id: 4, name: "Q2 All-Hands", status: "Draft", date: "2024-06-02", start: "15:00", end: "16:00", timezone: "America/Los_Angeles", host: "Ava Chen", moderators: ["Noah Kim"], speakers: [], category: "Internal", visibility: "Private", registration: "Closed", registered: 0, viewers: null, accent: "amber", description: "Company-wide quarterly update." },
  { id: 5, name: "Developer Deep Dive", status: "Draft", date: "2024-06-05", start: "11:00", end: "12:30", timezone: "Europe/Berlin", host: "Leo Fischer", moderators: [], speakers: ["Priya Nair"], category: "Workshop", visibility: "Private", registration: "Closed", registered: 0, viewers: null, accent: "indigo", description: "Hands-on with the ZoikoStream SDK and webhooks." },
  { id: 6, name: "Cloud Technology Webinar", status: "Completed", date: "2024-05-14", start: "10:00", end: "11:00", timezone: "America/New_York", host: "Priya Nair", moderators: ["Sofia Alvarez"], speakers: ["Marcus Reed"], category: "Webinar", visibility: "Public", registration: "Closed", registered: 4100, viewers: 3420, accent: "rose", description: "Scaling live delivery on cloud infrastructure." },
  { id: 7, name: "Customer Meet 2024", status: "Completed", date: "2024-05-10", start: "14:00", end: "17:00", timezone: "Asia/Singapore", host: "Marcus Reed", moderators: [], speakers: ["Ava Chen", "Noah Kim"], category: "Conference", visibility: "Unlisted", registration: "Closed", registered: 980, viewers: 890, accent: "emerald", description: "Annual customer appreciation and roadmap session." },
  { id: 8, name: "Security & Compliance Briefing", status: "Completed", date: "2024-04-30", start: "09:00", end: "10:00", timezone: "Europe/London", host: "Leo Fischer", moderators: ["Priya Nair"], speakers: [], category: "Internal", visibility: "Private", registration: "Invite only", registered: 540, viewers: 512, accent: "blue", description: "SOC 2, GDPR, and data-residency updates for enterprise customers." },
];

export const getEvent = (id) => EVENTS.find((e) => String(e.id) === String(id));

// Distinct tone per event status (STATUS token map doesn't cover all four).
export const STATUS_PILL = {
  Live: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  Upcoming: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  Draft: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  Completed: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400",
};

export const VIS_PILL = {
  Public: "text-emerald-600 dark:text-emerald-400",
  Private: "text-slate-500 dark:text-slate-400",
  Unlisted: "text-amber-600 dark:text-amber-400",
};

export const fmtDate = (iso) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
