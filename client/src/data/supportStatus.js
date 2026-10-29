// What the Support & Status page is allowed to CLAIM, given what the backend actually
// measures. Kept out of the page component so the rules are testable on their own, and so
// the page keeps Fast Refresh.
//
// The page reads `service_status` from /organization/overview (services/org.customer_status):
// only the customer-facing services the platform can actually check — Live streaming,
// Recording & replay, Management API — each with a status from real probes, this
// organization's own and platform-wide incidents, and (for recordings) its own recent
// recording starts. A service nothing can currently check is left out by the server; it is
// never sent as "Not configured" or "Not measured", and this module never invents a status
// for one.
//
// ── WHAT THIS REPLACED ──────────────────────────────────────────────────────────────────
// The page used to list all eight internal lifecycle stages, so a customer read "Transcoding &
// packaging — Not configured — Not measured" (a stage with no service behind it at all), and
// an "Incident-free (24 h)" column that printed 100.00% whenever the incident log was empty.
// That figure was arithmetic over recorded incidents, not uptime; the column now counts the
// incidents that were recorded.

// Customer words for the statuses the server sends. "idle" is Recording & replay with healthy
// replay storage and no recording in the last 24 hours: nothing has shown the recorder working
// or failing, so the row claims neither (services/org.customer_status).
export const STATUS_LABEL = { ok: "Operational", warn: "Degraded", down: "Major outage", idle: "No recent recordings" };

// The dot per status. An idle row is neutral — it is neither a confirmation nor a fault.
export const STATUS_DOT = { ok: "ok", warn: "warn", down: "down", idle: "neutral" };

// The line under a row's name when its status needs saying what it does and does not cover.
export const STATUS_NOTE = {
  idle: "Replay storage is healthy. Recording is confirmed the next time you record.",
};

// The page headline per verdict (services/org.customer_status). "partial" and "unavailable"
// never claim health the server could not establish.
export const VERDICT = {
  ok: { dot: "ok", pill: "All systems operational", title: "ZoikoStream is operating normally." },
  warn: { dot: "warn", pill: "Degraded performance", title: "Some ZoikoStream services are degraded." },
  down: { dot: "down", pill: "Service disruption", title: "ZoikoStream is experiencing a disruption." },
  partial: {
    dot: "neutral",
    pill: "Some services not reporting",
    title: "We can't confirm the status of every service right now.",
  },
  unavailable: {
    dot: "neutral",
    pill: "Status temporarily unavailable",
    title: "We can't show live service status right now.",
  },
};

/** The verdict to headline. Anything the server did not state reads as unavailable, never green. */
export function verdictOf(serviceStatus) {
  const key = serviceStatus?.verdict;
  return VERDICT[key] ? key : "unavailable";
}

// "ok" while a row is idle: every check that could run is healthy, but recording itself was not
// exercised, so the headline says what it covers instead of "operating normally".
const VERDICT_OK_UNCONFIRMED = {
  dot: "ok",
  pill: "All checked services operational",
  title: "Everything we can check is operating normally.",
};

// What the page shows when its last refresh failed but an older answer is on screen: that
// answer is kept (it is still the best information there is) but never headlined as current.
const VERDICT_STALE = {
  dot: "neutral",
  pill: "Status temporarily unavailable",
  title: "We can't refresh service status right now.",
};

/** The headline copy: the verdict, qualified when a row is idle, replaced when it is stale. */
export function headlineOf(serviceStatus, services, { stale = false } = {}) {
  if (stale) return VERDICT_STALE;
  const key = verdictOf(serviceStatus);
  if (key === "ok" && services.some((s) => s.status === "idle")) return VERDICT_OK_UNCONFIRMED;
  return VERDICT[key];
}

/** The banner over a stale page: when the shown state is from, so nobody reads it as now. */
export function staleNotice(generatedAt) {
  const at = generatedAt ? new Date(generatedAt) : null;
  const when = at && !Number.isNaN(at.getTime())
    ? at.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : null;
  return `Status information is temporarily unavailable. Showing the last known state${when ? ` from ${when}` : ""}.`;
}

/** The services to list: only rows the server stated a known status for. */
export function shownServices(serviceStatus) {
  const rows = Array.isArray(serviceStatus?.services) ? serviceStatus.services : [];
  return rows.filter((s) => STATUS_LABEL[s?.status]);
}

/** The "Incidents (24 h)" cell: a count of recorded incidents, never a percentage. */
// `count` and `noun` are `text` in two parts, so the cell can set the number apart from the word.
export function incidentsCell(count) {
  if (!Number.isInteger(count) || count < 0) {
    return { text: "—", count: null, noun: "", title: "No incident count was returned." };
  }
  const noun = count === 1 ? "incident" : "incidents";
  return {
    text: `${count} ${noun}`,
    count,
    noun,
    title: "Incidents recorded against this service in the last 24 hours.",
  };
}

/** The "Monitored services" tile: how many services the page can state a status for. */
export function monitoredServices(services, hasData) {
  if (!hasData) return { text: "—", title: "The status feed did not report its services." };
  return {
    text: String(services.length),
    title: "Services whose status ZoikoStream checks and can report.",
  };
}

/**
 * The "Active incidents" tile.
 *
 * `0` is only printed when the feed genuinely answered. A missing payload is an em dash, never
 * a zero — "we could not ask" and "we asked and the answer is none" are different things.
 */
export function activeIncidents(services, hasData, notice = null) {
  if (!hasData) return { text: "—", count: null, title: "The status feed did not report an incident count." };
  const count = services.reduce((n, s) => n + (s.open_incidents || 0), 0)
    + (Number.isInteger(notice?.count) ? notice.count : 0);
  return {
    text: String(count),
    count,
    title: count
      ? `${count} open incident${count === 1 ? "" : "s"} affecting your services.`
      : "No open incidents are recorded against your services.",
  };
}

const NOTICE_SCOPE = { platform: "the ZoikoStream platform", organization: "your organization" };

/**
 * The open incident to headline, normalized for the panel: the most severe of the services'
 * incidents and the platform notice (an incident that names no single service — services/org.
 * customer_status `platform_notice`). On a tie, a service's own incident wins: it says more.
 */
export function headlineIncident(services, notice = null) {
  const candidates = services
    .filter((s) => s.incident?.title)
    .map((s) => ({ title: s.incident.title, severity: s.incident.severity, affects: s.name, open: s.open_incidents || 1 }));
  if (notice?.title && NOTICE_SCOPE[notice.scope]) {
    candidates.push({ title: notice.title, severity: notice.severity, affects: NOTICE_SCOPE[notice.scope],
      open: Number.isInteger(notice.count) ? notice.count : 1 });
  }
  return candidates.sort((a, b) => (b.severity === "major") - (a.severity === "major"))[0] || null;
}

// ── scheduled maintenance ────────────────────────────────────────────────────────────────
// From `maintenance` on /organization/overview (services/org.maintenance_schedule): the
// published windows still ahead or under way — the same set the public status page lists —
// and `authoritative`, whether an EMPTY list may be read as "nothing is planned". It is false
// while operators have no tool to publish windows, and then the page must not say "No
// scheduled maintenance". A published window is always real and always shown.

export const MAINTENANCE_COPY = {
  none: "No scheduled maintenance at this time.",
  pending: "Maintenance updates will appear here when available.",
  unavailable: "Scheduled maintenance information is temporarily unavailable.",
};

const WINDOW_STATUS = { scheduled: "Scheduled", in_progress: "In progress", extended: "Extended" };

/** What the maintenance panel shows: the windows, or which of the three empty states. */
export function maintenanceState(maintenance) {
  if (!maintenance || !Array.isArray(maintenance.upcoming)) {
    return { kind: "unavailable", text: MAINTENANCE_COPY.unavailable };
  }
  const items = maintenance.upcoming.filter((w) => w && w.title && w.starts_at);
  if (items.length) return { kind: "upcoming", items };
  return maintenance.authoritative === true
    ? { kind: "none", text: MAINTENANCE_COPY.none }
    : { kind: "pending", text: MAINTENANCE_COPY.pending };
}

/** The badge: emergency work is never labelled as scheduled. */
export function maintenanceLabel(window) {
  if (window?.kind === "emergency") return "Emergency";
  return WINDOW_STATUS[window?.status] || "Scheduled";
}

/**
 * A window in the viewer's own time zone (named, so it is unambiguous), plus its length.
 * `timeZone`/`locale` exist for tests; the page passes neither.
 */
export function formatMaintenanceWindow(startsAt, endsAt, { timeZone, locale } = {}) {
  const start = new Date(startsAt);
  if (!startsAt || Number.isNaN(start.getTime())) return null;
  const end = endsAt ? new Date(endsAt) : null;
  const hasEnd = end && !Number.isNaN(end.getTime()) && end > start;
  const full = { timeZone, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  const fmt = (opts, d) => new Intl.DateTimeFormat(locale, opts).format(d);
  if (!hasEnd) return { when: fmt({ ...full, timeZoneName: "short" }, start), duration: null };

  const day = { timeZone, year: "numeric", month: "numeric", day: "numeric" };
  const sameDay = fmt(day, start) === fmt(day, end);
  const endText = sameDay
    ? fmt({ timeZone, hour: "numeric", minute: "2-digit", timeZoneName: "short" }, end)
    : fmt({ ...full, timeZoneName: "short" }, end);
  const minutes = Math.round((end - start) / 60000);
  const hours = Math.floor(minutes / 60);
  const duration = minutes < 60 ? `${minutes} min`
    : minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  return { when: `${fmt(full, start)} – ${endText}`, duration };
}
