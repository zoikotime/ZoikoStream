// Command Center window helpers, shared by the page and its filter bar so the request, the
// "does the screen match the filters" check, the export and the custom-range validation all
// agree.

// Validation mirrors services/ops_window.resolve; the server re-checks and its 400 is shown.
export function customWindowError(from, to, now = new Date()) {
  if (!from || !to) return "Choose both a start and an end.";
  const a = new Date(from);
  const b = new Date(to);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime())) return "Enter valid dates.";
  if (a >= b) return "The start must be before the end.";
  if (a > now) return "The start cannot be in the future.";
  if (b - a > 92 * 24 * 3600 * 1000) return "A custom range can cover at most 92 days.";
  return null;
}

// The params a filter state sends. One function, so the request, the "does the page match
// the filters" check and the export all agree on what the filters were.
export function commandCenterParams(filters) {
  return {
    range: filters.range,
    region: filters.region || undefined,
    scope: filters.scope,
    include_test: filters.include_test,
    // datetime-local has no zone; toISOString sends the operator's wall-clock as a real UTC
    // instant, so the server and the picker agree on the window.
    ...(filters.range === "custom" && filters.from
      ? {
          from: new Date(filters.from).toISOString(),
          to: filters.to ? new Date(filters.to).toISOString() : undefined,
        }
      : {}),
  };
}
