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

// Ids of events whose scheduled [start, end) intervals intersect.
// Follows the interval-overlap rule: eventA.start < eventB.end && eventB.start < eventA.end.
// Events ending exactly when another starts are non-overlapping.
// Cancelled/archived/deleted events and events without a valid end time/duration are excluded.
export function overlapping(events) {
  if (!Array.isArray(events)) return new Set();

  const spans = [];
  const seenIds = new Set();

  for (const e of events) {
    if (!e || !e.id || seenIds.has(e.id)) continue;
    if (e.status === "cancelled" || e.status === "archived" || e.display_status === "cancelled" || e.deleted_at) {
      continue;
    }
    if (!e.start_time) continue;

    const start = new Date(e.start_time).getTime();
    if (Number.isNaN(start)) continue;

    let end = null;
    if (e.end_time) {
      const parsedEnd = new Date(e.end_time).getTime();
      if (!Number.isNaN(parsedEnd) && parsedEnd > start) {
        end = parsedEnd;
      }
    } else if (typeof e.duration_minutes === "number" && e.duration_minutes > 0) {
      end = start + e.duration_minutes * 60 * 1000;
    }

    // Do not invent durations when end times or duration_minutes are missing.
    // Events without a valid end time (> start) have no interval to intersect.
    if (end === null) continue;

    seenIds.add(e.id);
    spans.push({ id: e.id, start, end });
  }

  spans.sort((a, b) => a.start - b.start || a.end - b.end);

  const clash = new Set();
  for (let i = 0; i < spans.length; i += 1) {
    const a = spans[i];
    for (let j = i + 1; j < spans.length; j += 1) {
      const b = spans[j];
      // Since spans is sorted by start, if b.start >= a.end, no subsequent event can overlap with a
      if (b.start >= a.end) {
        break;
      }
      if (a.start < b.end && b.start < a.end) {
        clash.add(a.id);
        clash.add(b.id);
      }
    }
  }

  return clash;
}
