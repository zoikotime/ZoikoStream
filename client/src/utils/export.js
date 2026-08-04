// Client-side CSV export. The moderator console exports the activity feed, chat log and
// poll results — all three are already in memory, so an export endpoint would only
// re-fetch what the socket already delivered.
// ponytail: no papaparse. Quote-escaping is the whole job.

const cell = (v) => {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

// `columns` is [[header, pick]] so the caller controls order and labels.
export function toCsv(rows, columns) {
  const head = columns.map(([label]) => cell(label)).join(",");
  const body = rows.map((r) => columns.map(([, pick]) => cell(pick(r))).join(","));
  return [head, ...body].join("\n");
}

// Shared blob -> anchor -> revoke plumbing for every download on the client.
function save(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadCsv(filename, rows, columns) {
  save(filename, new Blob([toCsv(rows, columns)], { type: "text/csv;charset=utf-8" }));
}

// Snapshot export for the admin Command Center: the payload IS the record of what the
// operator was looking at, so it is written verbatim rather than flattened into columns.
export function downloadJson(data, filename) {
  save(
    filename.endsWith(".json") ? filename : `${filename}.json`,
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" })
  );
}

// ── iCalendar ────────────────────────────────────────────────────────────────
// "Add to calendar" on the attendee watch page. A generated .ics beats linking out to
// Google Calendar: it works with Outlook, Apple Calendar and Google alike, needs no
// third-party URL, and leaks nothing about the attendee to anyone.
// ponytail: no ical library. Escaping is the part that actually matters (an unescaped
// comma in a title silently truncates the field in Outlook); RFC 5545's 75-octet line
// folding is omitted because every calendar client in use accepts long lines.

// Order matters: backslashes must be escaped before the sequences that introduce them.
const ics = (v) =>
  String(v ?? "").replace(/\\/g, "\\\\").replace(/[;,]/g, (c) => `\\${c}`).replace(/\r?\n/g, "\\n");

// UTC basic format — 20260802T090000Z. Anchoring to UTC sidesteps VTIMEZONE entirely:
// the client renders it in the attendee's own zone, which is what they want to see.
const icsDate = (d) => new Date(d).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");

/**
 * Build an iCalendar VEVENT.
 * `alarmMinutes` adds a VALARM — that is what makes the "Remind me" action a real
 * reminder rather than a button wired to an endpoint this platform doesn't have.
 */
export function toIcs({ id, title, description, location, url, start, end, alarmMinutes }) {
  // A calendar entry with no start is meaningless; callers gate on this.
  if (!start) return null;
  const stamp = icsDate(Date.now());
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//ZoikoStream//Events//EN",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${ics(id)}@zoikostream`,
    `DTSTAMP:${stamp}`,
    `DTSTART:${icsDate(start)}`,
    // No end time -> default to an hour, so the entry occupies a block rather than
    // collapsing to a zero-length item some clients refuse to display.
    `DTEND:${icsDate(end || new Date(new Date(start).getTime() + 3600_000))}`,
    `SUMMARY:${ics(title || "Event")}`,
    description && `DESCRIPTION:${ics(description)}`,
    location && `LOCATION:${ics(location)}`,
    url && `URL:${ics(url)}`,
    alarmMinutes && "BEGIN:VALARM",
    alarmMinutes && `TRIGGER:-PT${alarmMinutes}M`,
    alarmMinutes && "ACTION:DISPLAY",
    alarmMinutes && `DESCRIPTION:${ics(title || "Event")} starts soon`,
    alarmMinutes && "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
  ].filter(Boolean);
  return lines.join("\r\n"); // CRLF is required by RFC 5545, not a Windows habit
}

export function downloadIcs(event, filename = "event.ics") {
  const body = toIcs(event);
  if (!body) return false;
  save(filename, new Blob([body], { type: "text/calendar;charset=utf-8" }));
  return true;
}
