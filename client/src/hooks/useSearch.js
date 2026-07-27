import { useMemo, useState } from "react";

// Client-side text search. `toText(item)` returns the string to match against (join
// multiple fields with a space). Case-insensitive substring, trimmed. Returns the query
// state + the filtered results. Replaces the per-page query/filter copies.
//
//   const { query, setQuery, results } = useSearch(events, (e) => `${e.title} ${e.host}`);
export default function useSearch(items, toText, initial = "") {
  const [query, setQuery] = useState(initial);
  const q = query.trim().toLowerCase();
  const results = useMemo(
    () => (q ? items.filter((it) => toText(it).toLowerCase().includes(q)) : items),
    [items, q, toText]
  );
  return { query, setQuery, results };
}
