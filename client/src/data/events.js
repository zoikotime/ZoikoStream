// Display helpers shared by the Events list and Event Details pages. Event data itself
// comes from GET /streams — this file only maps the backend's lowercase enum values to
// display labels/styles.

export const STATUS_LABEL = {
  draft: "Draft",
  scheduled: "Upcoming",
  live: "Live",
  completed: "Completed",
  canceled: "Canceled",
};

export const STATUS_PILL = {
  draft: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  scheduled: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  live: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  completed: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-400",
  canceled: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400",
};

export const VISIBILITY_LABEL = {
  public: "Public",
  private: "Private",
  unlisted: "Unlisted",
};

export const VIS_PILL = {
  public: "text-emerald-600 dark:text-emerald-400",
  private: "text-slate-500 dark:text-slate-400",
  unlisted: "text-amber-600 dark:text-amber-400",
};

export const fmtDate = (isoDate) =>
  isoDate
    ? new Date(isoDate + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    : "—";

// Deterministic banner color per event — the backend has no "accent" concept, this is
// purely a stable visual pick so the same event doesn't change color on reload.
const ACCENTS = ["violet", "emerald", "blue", "amber", "indigo", "rose"];
const accentFor = (id) => ACCENTS[[...String(id)].reduce((h, c) => h + c.charCodeAt(0), 0) % ACCENTS.length];

// Public pages (EventRegistration, EventWatch) and their child components were built
// against a richer mock shape (name/date/start/end/accent/status-as-capitalized-word).
// Adapt the real API response into that shape rather than rewriting every consumer —
// those pages' video/chat surfaces are still placeholders pending live-streaming work.
export const toLegacyEventShape = (stream) => ({
  ...stream,
  name: stream.title,
  date: stream.scheduled_date,
  start: stream.start_time,
  end: stream.end_time,
  accent: accentFor(stream.id),
  speakers: [],
  status: STATUS_LABEL[stream.status] || stream.status,
  viewers: null,
});
