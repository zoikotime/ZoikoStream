// client/src/data/liveEvents.js
// Mock data for the platform-wide Live Events monitor (/admin/live-events).
// ponytail: static mock. TODO(backend): GET /admin/events?state=… for the table and
// GET /admin/events/summary for the KPIs, or subscribe to socket.io "platform:events".
//
// Per-event fields (null where not applicable to the state):
//   status:    upcoming | live | finished | failed
//   bitrate:   Mbps (live only)      viewers: current (live) / final (finished)
//   latency:   ms (live only)        health:  ok | warn | down
//   recording: recording | processing | saved | scheduled | failed
//   replay:    ready | processing | queued | failed | null

export const liveEvents = [
  // ── Live ──────────────────────────────────────────────────────────────────
  { id: 1, org: "Vertex Live", title: "Product Keynote 2026", status: "live", bitrate: 5.8, viewers: 24150, latency: 38, health: "ok", recording: "recording", replay: null, time: "Started 42m ago" },
  { id: 2, org: "Stark Streaming", title: "Global Partner Summit", status: "live", bitrate: 6.1, viewers: 18420, latency: 44, health: "ok", recording: "recording", replay: null, time: "Started 1h ago" },
  { id: 3, org: "Massive Dynamic", title: "Dev Conference · Day 1", status: "live", bitrate: 4.9, viewers: 12880, latency: 62, health: "warn", recording: "recording", replay: null, time: "Started 2h ago" },
  { id: 4, org: "Nakatomi Broadcast", title: "Championship Finals", status: "live", bitrate: 5.4, viewers: 31200, latency: 51, health: "ok", recording: "recording", replay: null, time: "Started 18m ago" },
  { id: 5, org: "Aperture Stream", title: "Fireside Chat", status: "live", bitrate: 3.2, viewers: 4120, latency: 118, health: "warn", recording: "recording", replay: null, time: "Started 9m ago" },
  { id: 6, org: "Orbit Broadcasting", title: "Music Festival Stream", status: "live", bitrate: 6.4, viewers: 22760, latency: 40, health: "ok", recording: "recording", replay: null, time: "Started 3h ago" },

  // ── Upcoming ──────────────────────────────────────────────────────────────
  { id: 7, org: "Zoiko Industries", title: "Investor Briefing", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts in 2h" },
  { id: 8, org: "Acme Corp", title: "Q3 Town Hall", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts in 45m" },
  { id: 9, org: "Umbrella Co", title: "Launch Event", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts tomorrow" },
  { id: 10, org: "Northwind Media", title: "Training Webinar", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts in 3h" },
  { id: 11, org: "Wonka Media", title: "Weekly Standup Live", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts in 20m" },
  { id: 12, org: "Pulse Studios", title: "Community AMA", status: "upcoming", bitrate: null, viewers: null, latency: null, health: null, recording: "scheduled", replay: null, time: "Starts in 5h" },

  // ── Finished ──────────────────────────────────────────────────────────────
  { id: 13, org: "Globex Media", title: "Earnings Call", status: "finished", bitrate: null, viewers: 8600, latency: null, health: "ok", recording: "saved", replay: "ready", time: "Ended 1h ago" },
  { id: 14, org: "Vertex Live", title: "Onboarding Session", status: "finished", bitrate: null, viewers: 3410, latency: null, health: "ok", recording: "saved", replay: "ready", time: "Ended 2h ago" },
  { id: 15, org: "Stark Streaming", title: "Roadmap Reveal", status: "finished", bitrate: null, viewers: 15230, latency: null, health: "ok", recording: "saved", replay: "processing", time: "Ended 30m ago" },
  { id: 16, org: "Acme Corp", title: "Customer Meet 2026", status: "finished", bitrate: null, viewers: 5980, latency: null, health: "ok", recording: "saved", replay: "ready", time: "Ended 4h ago" },
  { id: 17, org: "Massive Dynamic", title: "Research Showcase", status: "finished", bitrate: null, viewers: 2740, latency: null, health: "ok", recording: "saved", replay: "ready", time: "Ended 6h ago" },
  { id: 18, org: "Orbit Broadcasting", title: "DJ Set · Replay Test", status: "finished", bitrate: null, viewers: 9120, latency: null, health: "ok", recording: "saved", replay: "queued", time: "Ended 15m ago" },

  // ── Failed ────────────────────────────────────────────────────────────────
  { id: 19, org: "Initech", title: "Sales Kickoff", status: "failed", bitrate: null, viewers: 210, latency: null, health: "down", recording: "failed", replay: "failed", time: "Failed 12m ago", reason: "Encoder disconnected" },
  { id: 20, org: "Tyrell Media", title: "Press Conference", status: "failed", bitrate: null, viewers: 0, latency: null, health: "down", recording: "failed", replay: "failed", time: "Failed 1h ago", reason: "Ingest timeout" },
  { id: 21, org: "Gekko Media", title: "Trading Floor Live", status: "failed", bitrate: null, viewers: 540, latency: null, health: "down", recording: "failed", replay: "failed", time: "Failed 3h ago", reason: "SFU node crash" },
];

// Tab counts derived from the list so they never drift from the rows.
export const counts = liveEvents.reduce((acc, e) => ((acc[e.status] = (acc[e.status] || 0) + 1), acc), {});

// Platform-wide KPI aggregates (today). `viewers` = current concurrent across live events.
export const summary = {
  events: 342,
  viewers: liveEvents.filter((e) => e.status === "live").reduce((s, e) => s + e.viewers, 0),
  peak: 128400,
  recordings: 96,
  replayJobs: 34,
};
