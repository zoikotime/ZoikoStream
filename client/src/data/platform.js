// client/src/data/platform.js
// Mock data still backing a few pieces of the Super Admin shell (AdminTopbar's search/
// region picker/alerts bell, AdminSidebar's infrastructure summary, and the shared
// Quick Actions list). Everything the real Dashboard/Analytics pages need now comes
// from GET /admin/dashboard and GET /admin/analytics -- see pages/admin/Dashboard.jsx
// and pages/admin/Analytics.jsx. ponytail: these remaining exports are placeholders
// for the search/alerts/infrastructure surfaces, which don't have a backend yet.

// Chart colors — recharts needs hex, not Tailwind classes. Mirrors the ACCENT tokens.
// NOTE: the app remaps emerald->violet & teal->pink in index.css, so for real
// traffic-light health we use true green/amber/red below (see health()).
export const CHART = {
  violet: "#8b5cf6", indigo: "#6366f1", blue: "#3b82f6", cyan: "#06b6d4",
  emerald: "#10b981", amber: "#f59e0b", rose: "#f43f5e", pink: "#ec4899",
};

/* ─────────────────────────── Infrastructure Status ─────────────────────── */
// TODO(backend): GET /admin/infrastructure/health  (regions + services)
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

/* ─────────────────────────── Quick Actions ─────────────────────────────────── */
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
