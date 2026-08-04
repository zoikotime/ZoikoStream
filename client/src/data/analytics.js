// client/src/data/analytics.js
// Vocabulary and formatters for the Analytics platform. NO mock data — every figure comes from
// /analytics/*. What used to live here (generated sine-wave trends, invented device and traffic
// mixes, hard-coded top events) is gone: the platform now measures these, and the ones it cannot
// measure arrive from the API in an `unavailable` block with the reason, so the UI prints why
// instead of drawing a plausible shape.

// Chart colours — hex mirrors of the app's ACCENT tokens (recharts needs hex, not classes).
export const CHART = {
  violet: "#7c3aed", blue: "#3b82f6", emerald: "#10b981",
  amber: "#f59e0b", rose: "#f43f5e", indigo: "#6366f1", cyan: "#06b6d4",
};
export const CATEGORICAL = [CHART.violet, CHART.blue, CHART.emerald, CHART.amber, CHART.rose, CHART.indigo, CHART.cyan];

// Report periods. Keys match services/analytics.PERIODS on the server, which owns the calendar
// arithmetic — the client never computes a date range, it names one.
export const PERIODS = [
  { key: "today", label: "Today" },
  { key: "weekly", label: "This week" },
  { key: "monthly", label: "This month" },
  { key: "quarterly", label: "This quarter" },
  { key: "yearly", label: "This year" },
  { key: "custom", label: "Custom range" },
];

export const EVENT_SORTS = [
  { key: "attended", label: "Most attended" },
  { key: "registrations", label: "Most registrations" },
  { key: "peak", label: "Highest peak" },
  { key: "engagement", label: "Most engaged" },
  { key: "watch", label: "Most watch time" },
  { key: "recent", label: "Most recent" },
  { key: "title", label: "Title (A–Z)" },
];

// Export scopes — keys match services/analytics.EXPORT_COLUMNS.
export const DATASETS = [
  { key: "events", label: "Events" },
  { key: "trends", label: "Trends" },
  { key: "speakers", label: "Speakers" },
  { key: "attendees", label: "Attendees" },
  { key: "recordings", label: "Recordings" },
];

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// ── formatters ───────────────────────────────────────────────────────────────
// Every one of these treats null as "not measured" and renders an em dash. That is the whole
// contract with the backend: it sends null rather than 0 when there is no measurement, and the UI
// must not turn that back into a zero.

export const num = (value) =>
  value === null || value === undefined ? "—" : Number(value).toLocaleString();

export const pct = (value, digits = 0) =>
  value === null || value === undefined ? "—" : `${Number(value).toFixed(digits)}%`;

/** Seconds → "3h 12m" / "12m" / "48s". Null-safe. */
export const dur = (seconds) => {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(Number(seconds));
  if (!total) return "0s";
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  if (h && m) return `${h}h ${m}m`;
  if (h) return `${h}h`;
  return total < 60 ? `${total}s` : `${m}m`;
};

/** Watch time in HOURS, the unit an executive summary wants. */
export const hours = (seconds) =>
  seconds === null || seconds === undefined ? "—" : `${Math.round(Number(seconds) / 360) / 10} hrs`;

export const bytes = (n) => {
  const value = Number(n || 0);
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  const scaled = value / 1024 ** i;
  return `${scaled >= 100 || i === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[i]}`;
};

/** A growth delta for StatsCard: null when there was no previous period to compare against. */
export const growth = (value) =>
  value === null || value === undefined
    ? {}
    : { delta: `${Math.abs(value).toFixed(1)}%`, up: value >= 0 };

/**
 * Bucketed period label for a chart axis. The server returns ISO timestamps at the bucket
 * boundary; the bucket decides how much of it is worth showing.
 */
export function bucketLabel(iso, bucket) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const opts = {
    hour: { hour: "numeric" },
    day: { day: "numeric", month: "short" },
    week: { day: "numeric", month: "short" },
    month: { month: "short", year: "2-digit" },
    quarter: { month: "short", year: "2-digit" },
    year: { year: "numeric" },
  }[bucket] || { day: "numeric", month: "short" };
  return date.toLocaleDateString(undefined, opts);
}

/** Shape a trend series for the AreaChart/BarChart primitives. */
export const series = (rows, keys, bucket) =>
  (rows || []).map((row) => {
    const point = { label: bucketLabel(row.period, bucket) };
    keys.forEach((key) => { point[key] = row[key] ?? 0; });
    return point;
  });

/** The engagement tone thresholds the old dashboard used, kept so the visual language is stable. */
export const engagementTone = (value) =>
  value >= 70 ? CHART.emerald : value >= 55 ? CHART.amber : CHART.rose;

/** The browser's own timezone, as an IANA name — the default the charts bucket by. */
export const localZone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
};
