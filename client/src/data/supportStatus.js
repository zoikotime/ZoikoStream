// What the Support & Status page is allowed to CLAIM, given what the backend actually
// measures. Kept out of the page component so the rules are testable on their own, and so
// the page keeps Fast Refresh.
//
// ── THE PROBLEM THIS EXISTS FOR ─────────────────────────────────────────────────────────
// The page used to print "100.00%" under a column headed "Uptime (24 h)" for every service,
// including ones this deployment has never probed. That number does not come from uptime
// telemetry. services/ops.availability() computes it as
//
//     100 − (recorded incident minutes ÷ window)
//
// and says so in its own docstring: "Availability with no recorded impact is exactly 100.0,
// which is a measurement of 'nothing was recorded', not an assumption of perfection."
//
// With an empty Incident table — the normal state — every stage reads 100.00%. That is a
// true statement about the incident log and a false one about uptime, and presented under
// the word "Uptime" it is the latter that a reader takes away.

// Stages carried only by services that were never built (services/ops.INFORMATIONAL_STAGES
// on the server: Produce ← workers, and Deliver's CDN half). Nothing probes them, so their
// incident-free figure is arithmetic over an empty set, not a measurement.
export const UNMEASURED_STATUSES = new Set(["not_configured", "neutral", undefined, null]);

/**
 * What to print in the incident-free column for one lifecycle stage.
 *
 * A percentage is only honest where something is actually being watched. A stage reporting
 * `not_configured` has no probe behind it, so it gets "Not measured" rather than a figure
 * that would read as a clean bill of health.
 *
 * @returns {{text: string, measured: boolean, title: string}}
 */
export function incidentFreeCell(stage) {
  if (!stage || UNMEASURED_STATUSES.has(stage.status)) {
    return {
      text: "Not measured",
      measured: false,
      title: "Nothing probes this service in this deployment, so there is no window to measure.",
    };
  }
  if (stage.availability == null) {
    return {
      text: "—",
      measured: false,
      title: "No availability figure was returned for this service.",
    };
  }
  return {
    text: `${stage.availability.toFixed(2)}%`,
    measured: true,
    title:
      "Share of the last 24 hours with no recorded incident against this service. " +
      "Derived from the incident log, not from an uptime probe.",
  };
}

/**
 * The "Monitored services" tile.
 *
 * Counting all eight lifecycle stages overstated it: several are permanently
 * `not_configured` because the service behind them does not exist in this deployment, and
 * something nobody watches is not monitored. Reported as "n of m" so the total is still
 * visible without the headline number claiming more than is true.
 */
export function monitoredServices(stages) {
  const total = stages?.length || 0;
  if (!total) return { text: "—", monitored: 0, total: 0, title: "No service list was returned." };
  const monitored = stages.filter((s) => !UNMEASURED_STATUSES.has(s.status)).length;
  return {
    text: monitored === total ? String(total) : `${monitored} of ${total}`,
    monitored,
    total,
    title: `${monitored} of ${total} lifecycle stages have a service behind them in this deployment.`,
  };
}

/**
 * The "Active incidents" tile.
 *
 * `0` is only printed when the payload genuinely arrived and carried a stage list. A missing
 * or empty payload becomes an em dash, never a zero — "we could not ask" and "we asked and
 * the answer is none" are different things, and conflating them is how a status page tells
 * its most damaging lie.
 */
export function activeIncidents(stages, hasData) {
  if (!hasData || !Array.isArray(stages) || stages.length === 0) {
    return { text: "—", count: null, title: "The status feed did not report an incident count." };
  }
  const count = stages.reduce((n, s) => n + (s.open_incidents || 0), 0);
  return {
    text: String(count),
    count,
    title: count
      ? `${count} open incident${count === 1 ? "" : "s"} affecting your services.`
      : "No open incidents are recorded against your services.",
  };
}
