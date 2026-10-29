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

// Ids of eligible events involved in at least one overlapping [start, end) pair. The calendar
// badge counts distinct events, not pairs. Unknown/invalid intervals cannot establish a
// conflict, and cancelled or archived events are not active schedule conflicts.
export function overlapping(events) {
  const spans = events
    .filter((e) => e.start_time && e.end_time && !["cancelled", "archived"].includes(e.status))
    .map((e) => {
      const start = new Date(e.start_time).getTime();
      const end = new Date(e.end_time).getTime();
      return { id: e.id, start, end };
    })
    .filter(({ start, end }) => Number.isFinite(start) && Number.isFinite(end) && end > start)
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
