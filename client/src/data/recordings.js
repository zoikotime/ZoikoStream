// client/src/data/recordings.js
// Dummy data for the Recordings page (/organization/recordings).
// ponytail: mock data — swap for GET /organization/recordings when the backend lands.
// ids 6/7/8 line up with the Completed events in data/events.js so "Watch Replay"
// deep-links into the existing /events/:id/watch page.

export const recordings = [
  { id: 6,  name: "Cloud Technology Webinar",         date: "2024-05-14", duration: "1:02:14", views: 3420, sizeGB: 2.4, avgWatchMin: 41,  category: "Webinar",        accent: "rose" },
  { id: 7,  name: "Customer Meet 2024",               date: "2024-05-10", duration: "2:48:30", views: 890,  sizeGB: 6.1, avgWatchMin: 96,  category: "Conference",     accent: "emerald" },
  { id: 8,  name: "Security & Compliance Briefing",   date: "2024-04-30", duration: "58:20",   views: 512,  sizeGB: 1.9, avgWatchMin: 33,  category: "Internal",       accent: "blue" },
  { id: 21, name: "Developer Deep Dive",              date: "2024-06-05", duration: "1:24:05", views: 1210, sizeGB: 3.2, avgWatchMin: 52,  category: "Workshop",       accent: "indigo" },
  { id: 22, name: "Q1 Product Roadmap",               date: "2024-03-18", duration: "47:10",   views: 2040, sizeGB: 1.6, avgWatchMin: 29,  category: "Product Launch", accent: "violet" },
  { id: 23, name: "Scaling Live Video — Architecture", date: "2024-04-02", duration: "1:11:48", views: 1580, sizeGB: 2.8, avgWatchMin: 44, category: "Webinar",        accent: "amber" },
  { id: 24, name: "Onboarding & Best Practices",      date: "2024-02-27", duration: "39:55",   views: 760,  sizeGB: 1.3, avgWatchMin: 22,  category: "Workshop",       accent: "emerald" },
  { id: 25, name: "Annual Partner Summit",            date: "2024-01-30", duration: "3:12:40", views: 4110, sizeGB: 7.4, avgWatchMin: 118, category: "Conference",     accent: "blue" },
];

export const RECORDING_CATEGORIES = ["All", ...new Set(recordings.map((r) => r.category))];

// Display formatters (pure) — keep storage/time rendering in one place.
export const fmtStorage = (gb) => (gb >= 1000 ? `${(gb / 1000).toFixed(2)} TB` : `${gb.toFixed(1)} GB`);
export const fmtSize = (gb) => (gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(gb * 1024)} MB`);
export const fmtWatch = (min) =>
  min >= 60 ? `${Math.floor(min / 60)}h ${String(Math.round(min % 60)).padStart(2, "0")}m` : `${Math.round(min)}m`;
