// Rendering measured-zero apart from never-measured.
//
// The analytics service returns null for a figure it could not establish (no
// AnalyticsSnapshot rows in the window) and a number for one it measured — including a
// measured 0. These two must never look alike: "0.0 hrs" asserts we sampled and saw
// nothing, which is a claim the data cannot support when nothing was ever sampled.
//
// Lives in its own module rather than in Analytics.jsx because a component file that also
// exports plain functions breaks Fast Refresh (react-refresh/only-export-components).
const NOT_MEASURED = "—";   // em dash

/** Hours -> the largest honest unit. The 15s sampler cannot justify finer than a minute. */
export function formatWatchTime(hours) {
  if (hours == null) return NOT_MEASURED;
  const minutes = hours * 60;
  if (minutes > 0 && minutes < 1) return "< 1 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;
  return `${hours.toFixed(1)} hrs`;
}

/** A heuristic score, or an em dash when it was never measured. */
export function formatEngagement(score) {
  return score == null ? NOT_MEASURED : `${score}%`;
}
