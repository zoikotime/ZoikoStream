// Month arithmetic and conflict detection for the Events calendar (pages/organization/
// EventsCalendar). Pure functions, so the rules are testable without rendering a month.

const pad = (n) => String(n).padStart(2, "0");

export const monthKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
export const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

// "2026-10" -> the first of that month, local time. Anything else -> this month.
export function parseMonth(value, now = new Date()) {
  const m = /^(\d{4})-(\d{2})$/.exec(value || "");
  if (m && Number(m[2]) >= 1 && Number(m[2]) <= 12) return new Date(Number(m[1]), Number(m[2]) - 1, 1);
  return new Date(now.getFullYear(), now.getMonth(), 1);
}

// Ids of events whose [start, end) intervals overlap another's. An event without an end is
// taken as one hour long — so it can still conflict — and an end at or before its start as
// one minute.
export function overlapping(events) {
  const spans = events
    .filter((e) => e.start_time)
    .map((e) => {
      const start = new Date(e.start_time).getTime();
      const end = e.end_time ? new Date(e.end_time).getTime() : start + 3600 * 1000;
      return { id: e.id, start, end: Math.max(end, start + 60 * 1000) };
    })
    .sort((a, b) => a.start - b.start);
  const clash = new Set();
  for (let i = 0; i < spans.length; i += 1) {
    for (let j = i + 1; j < spans.length && spans[j].start < spans[i].end; j += 1) {
      clash.add(spans[i].id);
      clash.add(spans[j].id);
    }
  }
  return clash;
}
