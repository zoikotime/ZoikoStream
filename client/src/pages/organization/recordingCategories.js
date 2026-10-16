// The Recordings page's category filter, from the recordings themselves.
//
// A recording's category is its event's (Event.category, returned by GET
// /organization/recordings), and that field is free text — so "Webinar" and " webinar" can
// both exist. Categories are matched trimmed and case-insensitively, shown once each in the
// first spelling seen, and sorted. Recordings with no category are reachable through
// "Uncategorized", offered only when some recordings DO have a category (otherwise there is
// nothing to tell apart and the page shows no filter at all).

export const ALL_CATEGORIES = "__all__";
export const UNCATEGORIZED = "__uncategorized__";

const keyOf = (category) => (typeof category === "string" ? category.trim().toLowerCase() : "");

/** [{ value, label }] for the dropdown, without "All categories"; empty = no filter to offer. */
export function categoryOptions(recordings) {
  const seen = new Map();
  let uncategorized = 0;
  for (const recording of recordings) {
    const label = typeof recording.category === "string" ? recording.category.trim() : "";
    if (!label) {
      uncategorized += 1;
      continue;
    }
    const key = label.toLowerCase();
    if (!seen.has(key)) seen.set(key, label);
  }
  if (seen.size === 0) return [];
  const options = [...seen].map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
  if (uncategorized > 0) options.push({ value: UNCATEGORIZED, label: "Uncategorized" });
  return options;
}

export function matchesCategory(recording, selected) {
  if (!selected || selected === ALL_CATEGORIES) return true;
  const key = keyOf(recording.category);
  return selected === UNCATEGORIZED ? key === "" : key === selected;
}
