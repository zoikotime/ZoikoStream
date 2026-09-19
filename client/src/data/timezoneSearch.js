// Timezone search: the matching rules, in their own module.
//
// Separate from the TimezonePicker component so it can be imported by non-component code and
// by tests without pulling React in — and because a file that exports anything other than
// components loses Fast Refresh for the whole file (the same reason pages/organization/
// roleConfig.js exists).
//
// Nothing here touches VALUES. Rows carry the IANA identifier untouched; only the text used
// for matching is derived.
import { TIMEZONE_GROUPS, tzLabel, tzOffset } from "./timezones";

/**
 * One flat, pre-computed row per zone: { zone, group, label, haystack }.
 *
 * Built ONCE at module load rather than per keystroke — tzOffset() calls Intl, and the form
 * that renders this re-renders on every character typed.
 *
 * `haystack` deliberately holds more than the visible label:
 *   - the region heading, so "Europe" finds every European zone
 *   - the IANA identifier and a de-punctuated copy, so "Kolkata" finds India even though the
 *     label reads "Mumbai, Delhi…"
 *   - the GMT offset in both "GMT+5:30" and "UTC+5:30" spellings, because an operator typing
 *     a UTC offset means the same zone
 */
export function buildTimezoneRows(groups = TIMEZONE_GROUPS) {
  const rows = [];
  for (const [group, zones] of groups) {
    for (const [zone, abbr, places] of zones) {
      const offset = tzOffset(zone) || "";
      rows.push({
        zone,
        group,
        label: tzLabel(zone, abbr, places),
        haystack: [group, abbr, places, zone, zone.replace(/[_/]/g, " "), offset,
                   offset.replace(/^GMT/, "UTC")]
          .join(" ")
          .toLowerCase(),
      });
    }
  }
  return rows;
}

export const TIMEZONE_ROWS = buildTimezoneRows();

/**
 * Filter rows by free text. Case-insensitive.
 *
 * Every whitespace-separated term must match, so "new york" narrows to New York rather than
 * widening to everything containing "new" OR "york". A second, de-punctuated comparison lets
 * "GMT+5:30", "gmt+530" and "+5:30" all reach the same zone.
 */
export function filterTimezones(query, rows = TIMEZONE_ROWS) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return rows;
  const strip = (s) => s.replace(/[:\s]/g, "");
  const terms = q.split(/\s+/).filter(Boolean);
  return rows.filter((r) => {
    const loose = strip(r.haystack);
    return terms.every((t) => r.haystack.includes(t) || loose.includes(strip(t)));
  });
}

/**
 * Filtered rows re-assembled into the original [group, rows[]] shape.
 *
 * The picker renders the SAME grouped layout the old <select> had — "Universal", "Americas",
 * "Europe"… with full labels underneath — so search narrows the list without changing how it
 * reads. A group whose rows all filtered out is dropped entirely rather than left as an empty
 * heading.
 */
export function groupTimezones(query, rows = TIMEZONE_ROWS) {
  const matched = filterTimezones(query, rows);
  const out = [];
  for (const row of matched) {
    const last = out[out.length - 1];
    if (last && last[0] === row.group) last[1].push(row);
    else out.push([row.group, [row]]);
  }
  return out;
}
