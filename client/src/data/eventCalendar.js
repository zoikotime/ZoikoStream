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

export class OverlapResult extends Set {
  constructor(clashIds, pairs) {
    super(clashIds);
    this.pairs = pairs;
  }

  get pairCount() {
    return this.pairs.length;
  }
}

/**
 * Returns an array of unique overlapping event pairs: [[idA, idB], ...]
 * Follows the interval intersection rule:
 *   eventA.start < eventB.end && eventB.start < eventA.end
 *
 * Requirements:
 * - Compares full date-time values, not just calendar dates.
 * - Uses actual event start and end timestamps.
 * - Events on the same day but at different times do not overlap.
 * - Sequential events (where one ends exactly when the next starts) do not overlap.
 * - Events on different dates do not overlap unless their actual intervals cross a date boundary.
 * - Missing or invalid timestamps are safely excluded (never invent default durations or end times).
 * - Duplicate event records are deduplicated before calculation.
 * - Avoids counting the same overlap pair twice.
 * - Reflects unique overlapping event pairs.
 */
export function overlappingPairs(events) {
  if (!Array.isArray(events)) return [];

  const spans = [];
  const seenIds = new Set();

  for (const e of events) {
    if (!e || !e.id || seenIds.has(e.id)) continue;
    if (e.status === "cancelled" || e.status === "archived" || e.display_status === "cancelled" || e.deleted_at) {
      continue;
    }
    if (!e.start_time || !e.end_time) continue;

    const start = new Date(e.start_time).getTime();
    const end = new Date(e.end_time).getTime();

    // Do not invent durations when end times are missing or invalid.
    // Events without a valid end time (> start) have no interval to intersect.
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      continue;
    }

    seenIds.add(e.id);
    spans.push({ id: e.id, start, end });
  }

  spans.sort((a, b) => a.start - b.start || a.end - b.end);

  const pairs = [];
  for (let i = 0; i < spans.length; i += 1) {
    const a = spans[i];
    for (let j = i + 1; j < spans.length; j += 1) {
      const b = spans[j];
      // Since spans is sorted by start, if b.start >= a.end, no subsequent event can overlap with a
      if (b.start >= a.end) {
        break;
      }
      if (a.start < b.end && b.start < a.end) {
        pairs.push([a.id, b.id]);
      }
    }
  }

  return pairs;
}

/**
 * Returns the count of unique overlapping event pairs.
 */
export function overlapCount(events) {
  return overlappingPairs(events).length;
}

/**
 * Returns a Set containing the IDs of all events involved in any
 * overlap, with `.pairs` (unique pairs) and `.pairCount` (count of unique pairs).
 */
export function overlapping(events) {
  const pairs = overlappingPairs(events);
  const clashIds = new Set();
  for (const [idA, idB] of pairs) {
    clashIds.add(idA);
    clashIds.add(idB);
  }
  Object.defineProperty(clashIds, "pairs", { value: pairs, enumerable: false, writable: true });
  Object.defineProperty(clashIds, "pairCount", { value: pairs.length, enumerable: false, writable: true });
  return clashIds;
}
