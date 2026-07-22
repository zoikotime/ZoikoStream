// client/src/data/platform.js
// Mock data for the Super Admin control center (/admin/dashboard).
//
// ponytail: every export here is placeholder data. Each block is annotated with the
// backend call that should replace it — search "TODO(backend)" to find them all.
// Series are generated deterministically (sine shape, no Math.random) so charts are
// stable across renders, matching data/analytics.js house style.

// Chart colors — recharts needs hex, not Tailwind classes. Mirrors the ACCENT tokens.
// NOTE: the app remaps emerald->violet & teal->pink in index.css, so for real
// traffic-light health we use true green/amber/red below (see health()).
export const CHART = {
  violet: "#8b5cf6", indigo: "#6366f1", blue: "#3b82f6", cyan: "#06b6d4",
  emerald: "#10b981", amber: "#f59e0b", rose: "#f43f5e", pink: "#ec4899",
};
export const CATEGORICAL = [CHART.violet, CHART.blue, CHART.emerald, CHART.amber, CHART.rose, CHART.cyan];

// Deterministic wavy-upward series: [{ label, value }].
const shape = (labels, base, growth = 0.6) =>
  labels.map((label, i) => ({
    label,
    value: Math.round(base * (1 + growth * (i / Math.max(1, labels.length - 1))) * (0.86 + 0.14 * Math.sin(i * 1.3))),
  }));

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LAST_14 = Array.from({ length: 14 }, (_, i) => `${i + 1}`);

/* ─────────────────────────── 1. Platform Overview ─────────────────────────── */
// TODO(backend): GET /admin/platform/stats
export const kpis = [
  { key: "orgs", title: "Organizations", value: 1284, icon: "FiGrid", accent: "violet", delta: "6.2%", up: true },
  { key: "users", title: "Total Users", value: 48920, icon: "FiUsers", accent: "blue", delta: "9.1%", up: true },
  { key: "live", title: "Live Events", value: 37, icon: "FiRadio", accent: "emerald", live: true },
  { key: "streams", title: "Active Streams", value: 52, icon: "FiVideo", accent: "indigo", delta: "4.0%", up: true },
  { key: "assets", title: "Media Assets", value: 1284502, icon: "FiFilm", accent: "violet", delta: "2.3%", up: true },
  { key: "storage", title: "Storage Used", value: 48.2, suffix: " TB", decimals: 1, icon: "FiHardDrive", accent: "amber", delta: "3.4%", up: true },
  { key: "bandwidth", title: "Bandwidth", value: 12.4, suffix: " Gbps", decimals: 1, icon: "FiWifi", accent: "blue", live: true },
  { key: "revenue", title: "MRR", value: 284, prefix: "$", suffix: "K", icon: "FiDollarSign", accent: "emerald", delta: "8.7%", up: true },
];

// Platform-health gauge (RadialCard).
export const platformHealth = { percent: 99.98, label: "uptime", footer: "30-day availability" };

/* ─────────────────────────── 0. Platform Health (monitoring board) ────────── */
// TODO(backend): GET /admin/health/services  (poll every ~10s, or subscribe to a
// "platform:health" socket channel). status: ok | warn | down. `trend` = recent
// latency samples for the sparkline. availability is a rolling 30-day %.
const hTrend = (base, amp) =>
  Array.from({ length: 14 }, (_, i) => Math.max(1, Math.round(base + amp * Math.sin(i / 1.7) + (amp / 2) * Math.sin(i / 0.9))));

export const healthServices = [
  { id: "api", name: "API Health", icon: "FiActivity", status: "ok", latency: 42, availability: 99.99, lastIncident: "None (30d)", trend: hTrend(42, 8) },
  { id: "redis", name: "Redis", icon: "FiZap", status: "ok", latency: 1, availability: 100, lastIncident: "None (30d)", trend: hTrend(1.2, 0.6) },
  { id: "supabase", name: "Supabase", icon: "FiDatabase", status: "ok", latency: 38, availability: 99.98, lastIncident: "12d ago", trend: hTrend(38, 9) },
  { id: "livekit", name: "LiveKit", icon: "FiRadio", status: "warn", latency: 412, availability: 99.90, lastIncident: "24m ago", trend: hTrend(360, 90) },
  { id: "storage", name: "Storage", icon: "FiHardDrive", status: "ok", latency: 54, availability: 99.99, lastIncident: "8d ago", trend: hTrend(54, 10) },
  { id: "cdn", name: "CDN", icon: "FiCloud", status: "ok", latency: 18, availability: 100, lastIncident: "None (30d)", trend: hTrend(18, 5) },
  { id: "media", name: "Media Workers", icon: "FiFilm", status: "ok", latency: 88, availability: 99.95, lastIncident: "3d ago", trend: hTrend(88, 18) },
  { id: "queues", name: "Queues", icon: "FiLayers", status: "warn", latency: 240, availability: 99.92, lastIncident: "1h ago", trend: hTrend(210, 70) },
  { id: "jobs", name: "Background Jobs", icon: "FiClock", status: "ok", latency: 130, availability: 99.97, lastIncident: "5d ago", trend: hTrend(130, 22) },
  { id: "workers", name: "Worker Status", icon: "FiCpu", status: "ok", latency: 12, availability: 99.99, lastIncident: "None (30d)", trend: hTrend(12, 4) },
  { id: "db", name: "Database Connections", icon: "FiServer", status: "ok", latency: 6, availability: 99.99, lastIncident: "9d ago", trend: hTrend(6, 2) },
];

/* ─────────────────────────── 2. Infrastructure Status ─────────────────────── */
// TODO(backend): GET /admin/infrastructure/health  (regions + services)
// health level -> HealthDot handles the green/amber/red mapping.
export const regions = [
  { id: "us-east", name: "US East (Virginia)", status: "ok", latency: 24, load: 61 },
  { id: "us-west", name: "US West (Oregon)", status: "ok", latency: 31, load: 48 },
  { id: "eu-west", name: "EU West (Ireland)", status: "warn", latency: 88, load: 83 },
  { id: "ap-south", name: "AP South (Mumbai)", status: "ok", latency: 42, load: 55 },
  { id: "sa-east", name: "SA East (São Paulo)", status: "down", latency: 0, load: 0 },
];

export const services = [
  { id: "pipeline", name: "Media Pipeline", status: "ok", metric: "1.2k jobs/min", uptime: "99.99%", icon: "FiLayers" },
  { id: "redis", name: "Redis", status: "ok", metric: "0.4ms p99", uptime: "100%", icon: "FiZap" },
  { id: "supabase", name: "Supabase", status: "ok", metric: "38ms p95", uptime: "99.98%", icon: "FiDatabase" },
  { id: "livekit", name: "LiveKit SFU", status: "warn", metric: "elevated latency", uptime: "99.90%", icon: "FiRadio" },
  { id: "gateway", name: "API Gateway", status: "ok", metric: "8.1k req/s", uptime: "99.99%", icon: "FiServer" },
  { id: "queue", name: "Job Queue", status: "ok", metric: "142 queued", uptime: "99.97%", icon: "FiCpu" },
  { id: "cdn", name: "CDN Edge", status: "ok", metric: "94% cache hit", uptime: "100%", icon: "FiCloud" },
];

/* ─────────────────────────── 3. Live Platform Activity ────────────────────── */
// TODO(backend): subscribe socket.io "platform:activity" for realtime counters
export const liveActivity = {
  liveEvents: 37,
  currentViewers: 84210,
  streamsStarting: 6,
  avgBitrate: 5.4, // Mbps
  recordingJobs: 128,
  replayProcessing: 19,
  // rolling window of concurrent viewers (last 30 ticks) for the realtime chart
  viewersSeries: Array.from({ length: 30 }, (_, i) => ({
    label: `${i}`,
    value: Math.round(80000 * (1 + 0.08 * Math.sin(i / 3)) + 4000 * Math.sin(i / 1.5)),
  })),
};

/* ─────────────────────────── 4. Organization Insights ─────────────────────── */
// TODO(backend): GET /admin/organizations/insights
export const orgInsights = {
  counts: { trials: 214, enterprise: 68, needsAttention: 5 },
  newest: [
    { name: "Northwind Media", plan: "Pro", when: "2h ago" },
    { name: "Vertex Live", plan: "Enterprise", when: "5h ago" },
    { name: "Pulse Studios", plan: "Starter", when: "9h ago" },
    { name: "Orbit Broadcasting", plan: "Pro", when: "1d ago" },
  ],
  topUsage: [
    { name: "Zoiko Industries", metric: "18.4 TB", pct: 92 },
    { name: "Umbrella Co", metric: "12.1 TB", pct: 74 },
    { name: "Acme Corp", metric: "9.8 TB", pct: 61 },
    { name: "Globex Media", metric: "7.2 TB", pct: 45 },
  ],
  attention: [
    { name: "Initech", reason: "Payment failed · 3 retries", status: "error" },
    { name: "Hooli", reason: "Trial ends in 2 days", status: "warning" },
    { name: "Soylent Corp", reason: "Storage over 95%", status: "warning" },
    { name: "Wonka Media", reason: "Abnormal API traffic", status: "error" },
  ],
};

/* ─────────────────────────── 5. Revenue Overview ──────────────────────────── */
// TODO(backend): GET /admin/billing/summary
export const revenue = {
  mrr: 284000,
  arr: 3408000,
  growthPct: 8.7,
  mrrSeries: shape(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"], 180000, 0.7),
  topCustomers: [
    { name: "Zoiko Industries", plan: "Enterprise", amount: 42000 },
    { name: "Umbrella Co", plan: "Enterprise", amount: 31500 },
    { name: "Vertex Live", plan: "Enterprise", amount: 24800 },
    { name: "Acme Corp", plan: "Pro", amount: 9900 },
  ],
  pendingPayments: [
    { name: "Initech", amount: 1290, due: "Overdue 4d", status: "error" },
    { name: "Globex Media", amount: 2400, due: "Due today", status: "warning" },
    { name: "Pulse Studios", amount: 590, due: "Due in 3d", status: "pending" },
  ],
};

/* ─────────────────────────── 6. Security Center ───────────────────────────── */
// TODO(backend): GET /admin/security/overview
export const security = {
  stats: [
    { key: "failed", label: "Failed Logins", value: 342, delta: "+12%", up: false, icon: "FiLock" },
    { key: "blocked", label: "Blocked Requests", value: 1890, delta: "+34%", up: false, icon: "FiShield" },
    { key: "abuse", label: "API Abuse Flags", value: 27, delta: "-5%", up: true, icon: "FiAlertTriangle" },
    { key: "ratelimit", label: "Rate-Limited IPs", value: 64, delta: "+8%", up: false, icon: "FiSlash" },
  ],
  events: [
    { title: "Brute-force blocked", detail: "213.x.x.x · 48 attempts", when: "3m ago", status: "error" },
    { title: "New API key issued", detail: "Zoiko Industries · prod", when: "22m ago", status: "info" },
    { title: "Rate limit tripped", detail: "/v1/streams · 5 orgs", when: "1h ago", status: "warning" },
    { title: "MFA enforced org-wide", detail: "Vertex Live", when: "3h ago", status: "success" },
  ],
};

/* ─────────────────────────── 7. Platform Analytics ────────────────────────── */
// TODO(backend): GET /admin/analytics?range=14d
export const analytics = {
  traffic: LAST_14.map((label, i) => ({
    label,
    playback: Math.round(420000 * (1 + 0.5 * (i / 13)) * (0.9 + 0.1 * Math.sin(i))),
    live: Math.round(180000 * (1 + 0.7 * (i / 13)) * (0.85 + 0.15 * Math.sin(i * 1.4))),
  })),
  apiBandwidth: LAST_14.map((label, i) => ({
    label,
    requests: Math.round(6_800_000 * (1 + 0.4 * (i / 13)) * (0.9 + 0.1 * Math.sin(i * 1.2))),
    bandwidth: Math.round(9200 * (1 + 0.35 * (i / 13)) * (0.9 + 0.1 * Math.sin(i * 0.8))), // GB
  })),
  mix: [
    { label: "Playback", value: 48 },
    { label: "Live Streams", value: 26 },
    { label: "Recording", value: 14 },
    { label: "API", value: 12 },
  ],
  // Activity heatmap: 7 days x 24 hours, intensity 0-4 (deterministic).
  heatmap: DAYS.map((day, d) => ({
    day,
    hours: Array.from({ length: 24 }, (_, h) =>
      Math.max(0, Math.round(2 + 2 * Math.sin((h - 6) / 3.8) + Math.sin(d + h / 6)))
    ),
  })),
};

/* ─────────────────────────── 8. Operational Feed ──────────────────────────── */
// TODO(backend): GET /admin/events/feed  (or socket.io "platform:feed")
export const feed = [
  { type: "org", title: "New organization created", detail: "Northwind Media · Pro plan", when: "2m ago", status: "info" },
  { type: "stream", title: "Live stream started", detail: "Vertex Live · Product Keynote", when: "6m ago", status: "success" },
  { type: "recording", title: "Recording finished", detail: "Acme Corp · Town Hall (1h 24m)", when: "14m ago", status: "success" },
  { type: "incident", title: "Incident opened", detail: "SA East region unreachable", when: "26m ago", status: "error" },
  { type: "webhook", title: "Webhook delivery failed", detail: "Globex Media · 5xx from endpoint", when: "38m ago", status: "warning" },
  { type: "support", title: "Support ticket escalated", detail: "Initech · billing · P1", when: "52m ago", status: "warning" },
];

/* ─────────────────────────── Alert Center ─────────────────────────────────── */
// TODO(backend): GET /admin/alerts?state=active
export const alerts = [
  { id: 1, severity: "critical", title: "SA East region down", detail: "All nodes unreachable · failover active", when: "26m ago" },
  { id: 2, severity: "warning", title: "LiveKit latency elevated", detail: "p99 above 400ms in EU West", when: "1h ago" },
  { id: 3, severity: "warning", title: "5 orgs need attention", detail: "Billing & storage thresholds", when: "2h ago" },
  { id: 4, severity: "info", title: "Scheduled maintenance", detail: "Redis upgrade · Sun 02:00 UTC", when: "5h ago" },
];

/* ─────────────────────────── Command bar / search ─────────────────────────── */
// TODO(backend): GET /admin/search?q=  (global entity search)
export const searchIndex = [
  { label: "Organizations", to: "/admin/organizations", kind: "Page", icon: "FiGrid" },
  { label: "Live Events", to: "/admin/live-events", kind: "Page", icon: "FiRadio" },
  { label: "Media Infrastructure", to: "/admin/infrastructure", kind: "Page", icon: "FiServer" },
  { label: "Users", to: "/admin/users", kind: "Page", icon: "FiUsers" },
  { label: "Security", to: "/admin/security", kind: "Page", icon: "FiShield" },
  { label: "Audit Logs", to: "/admin/audit", kind: "Page", icon: "FiFileText" },
  { label: "Subscriptions", to: "/admin/subscriptions", kind: "Page", icon: "FiCreditCard" },
  { label: "Feature Flags", to: "/admin/feature-flags", kind: "Page", icon: "FiFlag" },
  { label: "Zoiko Industries", to: "/admin/organizations", kind: "Organization", icon: "FiGrid" },
  { label: "Acme Corp", to: "/admin/organizations", kind: "Organization", icon: "FiGrid" },
];

export const regionOptions = [
  { id: "global", name: "Global" },
  ...regions.map((r) => ({ id: r.id, name: r.name })),
];

/* ─────────────────────────── Audit Timeline ───────────────────────────────── */
// TODO(backend): GET /admin/audit-logs?limit=8  (real endpoint already exists on the API)
export const auditLog = [
  { id: 1, actor: "info@zoikostream.com", action: "organization.suspend", target: "Initech", when: "4m ago" },
  { id: 2, actor: "info@zoikostream.com", action: "subscription.update", target: "Vertex Live → Enterprise", when: "18m ago" },
  { id: 3, actor: "system", action: "incident.open", target: "SA East region", when: "26m ago" },
  { id: 4, actor: "info@zoikostream.com", action: "user.update", target: "j.rivera@acme.com → org_admin", when: "1h ago" },
  { id: 5, actor: "info@zoikostream.com", action: "organization.create", target: "Northwind Media", when: "2h ago" },
  { id: 6, actor: "system", action: "settings.update", target: "streaming_limits", when: "3h ago" },
  { id: 7, actor: "info@zoikostream.com", action: "user.delete", target: "temp@globex.com", when: "5h ago" },
];

/* ─────────────────────────── 9. Quick Actions ─────────────────────────────── */
// Shared by the topbar's "Quick Create" menu and the dashboard Quick Actions panel.
// Items without `to` are non-navigational (broadcast / maintenance) — wired to a toast
// stub for now. TODO(backend): POST the corresponding platform action.
export const quickActions = [
  { label: "Create Organization", icon: "FiGrid", to: "/admin/organizations", accent: "violet" },
  { label: "Create Admin", icon: "FiUserPlus", to: "/admin/users", accent: "blue" },
  { label: "Platform Broadcast", icon: "FiSend", accent: "indigo" },
  { label: "Maintenance Mode", icon: "FiTool", accent: "amber" },
  { label: "Feature Flags", icon: "FiFlag", to: "/admin/feature-flags", accent: "emerald" },
  { label: "Release Notes", icon: "FiFileText", to: "/admin/releases", accent: "rose" },
];
