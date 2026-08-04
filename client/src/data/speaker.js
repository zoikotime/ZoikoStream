// client/src/data/speaker.js
// Static vocabulary for the Speaker & Panellist console. Everything that MOVES comes from the
// live socket (hooks/useLiveEvent) or the assets API; what lives here is labels, option lists
// and the two pure helpers the schedule needs.

// ── whiteboard ────────────────────────────────────────────────────────────────
// Tools must match services/speaker.BOARD_TOOLS — the server validates against that list and
// silently falls back to "pen", so a tool added here alone would draw the wrong thing.
export const BOARD_TOOLS = [
  { key: "pen", label: "Pen", hint: "Freehand" },
  { key: "highlighter", label: "Highlighter", hint: "Translucent freehand" },
  { key: "line", label: "Line" },
  { key: "arrow", label: "Arrow" },
  { key: "rect", label: "Rectangle" },
  { key: "ellipse", label: "Ellipse" },
  { key: "text", label: "Text" },
  { key: "note", label: "Sticky note" },
];

export const BOARD_COLOURS = ["#0f172a", "#e11d48", "#2563eb", "#059669", "#d97706", "#7c3aed"];
export const BOARD_WIDTHS = [2, 4, 8, 14];

// ── technical issues ──────────────────────────────────────────────────────────
// Keys must match services/speaker.ISSUE_KINDS.
export const ISSUE_KINDS = [
  { key: "audio", label: "Audio problem" },
  { key: "video", label: "Video problem" },
  { key: "network", label: "Network problem" },
  { key: "screen_share", label: "Screen share problem" },
  { key: "presentation", label: "Presentation problem" },
  { key: "other", label: "Something else" },
];

// ── presentation ──────────────────────────────────────────────────────────────

export const ASSET_STATUS_TONE = { approved: "success", pending: "warning", rejected: "danger" };

export const ASSET_STATUS_LABEL = {
  approved: "Approved",
  pending: "Awaiting approval",
  rejected: "Rejected",
};

// Accepted by routers/speaker.upload_asset. PowerPoint is accepted but cannot be RENDERED as
// slides here (no converter in this stack), which the panel says out loud rather than failing
// mysteriously at present time.
export const ACCEPTED_UPLOAD = ".pdf,.png,.jpg,.jpeg,.webp,.gif,.svg,.ppt,.pptx";
export const MAX_UPLOAD_MB = 25;

export const fmtBytes = (n) => {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
};

// ── schedule ──────────────────────────────────────────────────────────────────

/** Milliseconds until an ISO timestamp, or null. Negative means it already started. */
export const msUntil = (iso) => (iso ? new Date(iso).getTime() - Date.now() : null);

export const fmtCountdown = (ms) => {
  if (ms == null) return "—";
  const past = ms < 0;
  const s = Math.floor(Math.abs(ms) / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const body = d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
  return past ? `${body} ago` : `in ${body}`;
};

/** The event's own timezone alongside the viewer's, because a speaker in another country needs
 *  both and guessing which one is meant is how people miss their slot. */
export const fmtInZone = (iso, timezone) => {
  if (!iso) return "—";
  const d = new Date(iso);
  const local = d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  if (!timezone) return local;
  try {
    const there = d.toLocaleString([], { dateStyle: "medium", timeStyle: "short", timeZone: timezone });
    return there === local ? local : `${local} · ${there} ${timezone}`;
  } catch {
    return local;   // an unknown IANA zone must not break the row
  }
};

// ── calendar ──────────────────────────────────────────────────────────────────

const icsTime = (iso) => new Date(iso).toISOString().replace(/[-:]/g, "").split(".")[0] + "Z";
// RFC 5545 wants CRLF and lines folded at 75 octets. Both matter: Outlook rejects a bare-LF
// file outright, and an unfolded long description silently truncates.
const fold = (line) => {
  const out = [];
  let rest = line;
  while (rest.length > 74) {
    out.push(rest.slice(0, 74));
    rest = ` ${rest.slice(74)}`;
  }
  out.push(rest);
  return out.join("\r\n");
};
const esc = (s = "") => String(s).replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n");

/**
 * Build an .ics for one or more events. Generated in the browser on purpose: a calendar file is
 * a text format, and adding a server endpoint (plus an auth story for whatever fetches it) to
 * produce one would be work for nothing.
 */
export function buildIcs(events, { origin = "" } = {}) {
  const lines = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ZoikoStream//Speaker Schedule//EN",
    "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
  ];
  for (const e of events) {
    if (!e.start_time) continue;     // an unscheduled draft has nothing to put in a calendar
    const end = e.end_time || new Date(new Date(e.start_time).getTime() + 3600_000).toISOString();
    lines.push(
      "BEGIN:VEVENT",
      fold(`UID:${e.id}@zoikostream`),
      `DTSTAMP:${icsTime(new Date().toISOString())}`,
      `DTSTART:${icsTime(e.start_time)}`,
      `DTEND:${icsTime(end)}`,
      fold(`SUMMARY:${esc(e.title || "Untitled event")}`),
      fold(`DESCRIPTION:${esc(e.short_description || e.description || "")}`),
      fold(`LOCATION:${esc(e.location || `${origin}/speaker/live?event=${e.id}`)}`),
      fold(`URL:${origin}/speaker/live?event=${e.id}`),
      "END:VEVENT",
    );
  }
  lines.push("END:VCALENDAR");
  return lines.join("\r\n");
}

export function downloadIcs(filename, events, opts) {
  const blob = new Blob([buildIcs(events, opts)], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
