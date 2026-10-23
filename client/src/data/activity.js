// Presentation for Activity & Audit outcomes and categories. The words and the decision come
// from the server (services/activity_log); this only picks a colour and never on its own —
// every badge also carries the outcome's label, so colour is never the only signal (EVT-07
// QA I).
export const OUTCOME_TONE = {
  applied: "success",
  denied: "danger",
  rejected: "warning",
  failed: "danger",
  pending: "info",
};

export const OUTCOME_FILTERS = [
  { value: "", label: "All results" },
  { value: "applied", label: "Applied" },
  { value: "refused", label: "Denied or rejected" },
  { value: "failed", label: "Failed" },
  { value: "pending", label: "Awaiting decision" },
];

export const DATE_FILTERS = [
  { value: "", label: "All dates" },
  { value: "24h", label: "Last 24 hours", hours: 24 },
  { value: "7d", label: "Last 7 days", hours: 24 * 7 },
  { value: "30d", label: "Last 30 days", hours: 24 * 30 },
];

export const sinceFor = (value, now = Date.now()) => {
  const opt = DATE_FILTERS.find((d) => d.value === value);
  return opt?.hours ? new Date(now - opt.hours * 3600 * 1000).toISOString() : null;
};
