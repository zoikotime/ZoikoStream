// client/src/data/analytics.js
// Dummy data for the Analytics Dashboard (/organization/analytics).
// ponytail: mock data — swap for GET /organization/analytics when the backend lands.
// Trend series are generated deterministically (sine shape, no Math.random) so the
// date-range selector produces stable, believable charts that react to the range.

// Chart colors — hex mirrors of the app's ACCENT tokens (recharts needs hex, not classes).
export const CHART = {
  violet: "#7c3aed", blue: "#3b82f6", emerald: "#10b981",
  amber: "#f59e0b", rose: "#f43f5e", indigo: "#6366f1", cyan: "#06b6d4",
};
export const CATEGORICAL = [CHART.violet, CHART.blue, CHART.emerald, CHART.amber, CHART.rose, CHART.indigo, CHART.cyan];

export const RANGES = [
  { key: "7d", label: "Last 7 days", factor: 0.25 },
  { key: "30d", label: "Last 30 days", factor: 1 },
  { key: "90d", label: "Last 90 days", factor: 2.8 },
  { key: "12m", label: "Last 12 months", factor: 11 },
];
export const rangeLabel = (key) => RANGES.find((r) => r.key === key)?.label ?? "";

// Summary KPIs scale with the selected window (30d is the baseline).
const BASE = { viewers: 128540, watchHours: 48200, peak: 9820, engagement: 72 };
export const summary = (factor) => ({
  viewers: Math.round(BASE.viewers * factor),
  watchHours: Math.round(BASE.watchHours * factor),
  peak: Math.round(BASE.peak * (1 + (factor - 1) * 0.12)), // a peak grows slowly, not linearly
  engagement: BASE.engagement, // an average — roughly window-independent
});

const LABELS = {
  "7d": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  "30d": ["Apr 1", "Apr 6", "Apr 11", "Apr 16", "Apr 21", "Apr 26"],
  "90d": ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10", "W11", "W12"],
  "12m": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
};
// Per-bucket base value — a month holds more than a day, so buckets differ by range.
const VIEWER_BASE = { "7d": 4200, "30d": 21000, "90d": 26000, "12m": 96000 };
const WATCH_BASE = { "7d": 1600, "30d": 8000, "90d": 9800, "12m": 36000 };
const ATT_BASE = { "7d": 5200, "30d": 24000, "90d": 30000, "12m": 110000 };

// Wavy upward trend, deterministic per index.
const shape = (labels, base, growth = 0.7) =>
  labels.map((label, i) => ({
    label,
    value: Math.round(base * (1 + growth * (i / Math.max(1, labels.length - 1))) * (0.85 + 0.15 * Math.sin(i * 1.3))),
  }));

export const trends = (rangeKey) => {
  const labels = LABELS[rangeKey] || LABELS["12m"];
  return {
    viewership: shape(labels, VIEWER_BASE[rangeKey] ?? 96000),
    watchTime: shape(labels, WATCH_BASE[rangeKey] ?? 36000, 0.6),
    attendance: labels.map((label, i) => {
      const registered = Math.round((ATT_BASE[rangeKey] ?? 110000) * (1 + 0.6 * (i / Math.max(1, labels.length - 1))) * (0.9 + 0.1 * Math.sin(i)));
      return { label, registered, attended: Math.round(registered * (0.66 + 0.05 * Math.sin(i * 1.7))) };
    }),
  };
};

// % of the audience still watching across event progress (window-independent).
export const retention = [
  { label: "0%", value: 100 }, { label: "10%", value: 96 }, { label: "20%", value: 89 },
  { label: "30%", value: 83 }, { label: "40%", value: 77 }, { label: "50%", value: 71 },
  { label: "60%", value: 66 }, { label: "70%", value: 60 }, { label: "80%", value: 54 },
  { label: "90%", value: 49 }, { label: "100%", value: 44 },
];

export const topEvents = [
  { label: "Tech Summit 2024", value: 12500 },
  { label: "Annual Partner Summit", value: 11040 },
  { label: "Customer Meet 2024", value: 8600 },
  { label: "Cloud Technology Webinar", value: 7420 },
  { label: "Q1 Product Roadmap", value: 5210 },
];

export const locations = [
  { label: "United States", value: 38 },
  { label: "India", value: 21 },
  { label: "Germany", value: 12 },
  { label: "United Kingdom", value: 9 },
  { label: "Singapore", value: 7 },
  { label: "Brazil", value: 5 },
];

export const devices = [
  { label: "Desktop", value: 58 },
  { label: "Mobile", value: 29 },
  { label: "Tablet", value: 8 },
  { label: "Smart TV", value: 5 },
];

export const trafficSources = [
  { label: "Direct", value: 34 },
  { label: "Email", value: 26 },
  { label: "Social", value: 20 },
  { label: "Search", value: 13 },
  { label: "Referral", value: 7 },
];

export const reports = [
  { id: 1, event: "Tech Summit 2024", date: "2024-05-20", viewers: 12500, watchHours: 8420, engagement: 78 },
  { id: 7, event: "Customer Meet 2024", date: "2024-05-10", viewers: 8600, watchHours: 6900, engagement: 81 },
  { id: 25, event: "Annual Partner Summit", date: "2024-01-30", viewers: 11040, watchHours: 9330, engagement: 74 },
  { id: 6, event: "Cloud Technology Webinar", date: "2024-05-14", viewers: 3420, watchHours: 2110, engagement: 64 },
  { id: 22, event: "Q1 Product Roadmap", date: "2024-03-18", viewers: 5210, watchHours: 2540, engagement: 59 },
  { id: 8, event: "Security & Compliance Briefing", date: "2024-04-30", viewers: 512, watchHours: 280, engagement: 47 },
];
