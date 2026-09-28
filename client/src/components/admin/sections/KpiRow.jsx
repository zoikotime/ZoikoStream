import MetricTile from "../MetricTile";
import { compact } from "../format";

// The five Command Center tiles. Every figure, every trend and every comparison comes from
// /admin/command-center (services/ops.command_center); nothing is calculated here.
//
// Each tile states what its number covers, because the range selector means different
// things to different tiles:
//   CURRENT  platform health, live sessions, at-risk — the state NOW, in every range. The
//            range only drives their trend or a clearly labelled "in this period" figure.
//   WINDOW   concurrent audience (outside Live) and API health — aggregates over the window.
// A ▲/▼ badge appears only for a real comparison with the previous equal-length window
// (`comparison.delta_pct`). It used to compare the last two sparkline points, which is not a
// period comparison at all.
//
// Unknown is never zero: "—" plus the reason when nothing was measured.

const HEALTH_TONE = {
  ok: "text-green-600 dark:text-green-400",
  warn: "text-amber-600 dark:text-amber-400",
  down: "text-rose-600 dark:text-rose-400",
};
const HEALTH_WORD = { ok: "Healthy", warn: "Degraded", down: "Disrupted", unknown: "Unknown" };

const WINDOW_LABEL = {
  live: "last 15 min",
  "1h": "last hour",
  "24h": "last 24 hours",
  "7d": "last 7 days",
  custom: "selected period",
};

const pct = (n) => (n == null ? null : Number(n).toFixed(n >= 10 ? 1 : 2));

function badge(comparison) {
  const d = comparison?.delta_pct;
  if (d == null) return { delta: null, up: undefined };
  return { delta: `${Math.abs(d)}%`, up: d >= 0 };
}

export default function KpiRow({ kpis, range, age }) {
  const k = kpis || {};
  const mode = range?.mode || "live";
  const period = WINDOW_LABEL[mode] || "selected period";
  const health = k.platform_health || {};
  const live = k.live_sessions || {};
  const risk = k.at_risk_sessions || {};
  const audience = k.concurrent_audience || {};
  const apiHealth = k.api_health || {};

  const riskBreakdown = [
    risk.critical ? `${risk.critical} critical` : null,
    risk.high ? `${risk.high} high` : null,
    risk.monitoring ? `${risk.monitoring} monitoring` : null,
  ].filter(Boolean).join(" · ");

  // ── Platform health: current status; the window drives only the trend and comparison.
  const healthBadge = badge(health.comparison);
  const unreported = (health.unmonitored || 0) + (health.not_configured || 0);
  const healthNote = health.total
    ? [
        `${health.down || 0} down · ${health.warn || 0} degraded · ${unreported} unmonitored or not configured`,
        health.average_ok != null ? `avg ${health.average_ok}/${health.total} healthy over ${period}` : null,
        health.history_note || null,
      ].filter(Boolean).join(" · ")
    : null;

  // ── Live sessions: current count; the window gives the "in this period" figure + trend.
  const prevSessions = live.comparison?.previous;
  const liveNote = [
    live.in_period != null
      ? `${live.in_period} in ${period}${prevSessions != null ? ` (${prevSessions} in the previous ${mode === "custom" ? "period" : period.replace("last ", "")})` : ""}`
      : null,
    `${live.starting_soon || 0} starting in 30 min · ${live.unattended || 0} unattended`,
  ].filter(Boolean).join(" · ");

  // ── Concurrent audience: now (Live) or the window's peak (every other range).
  const windowed = audience.semantics === "window";
  let audienceValue = audience.value != null ? compact(audience.value) : null;
  let audienceNote;
  const audState = audience.measurement_state;
  if (!windowed) {
    if (audState === "not_sampled") audienceNote = "Sessions are live but no audience sample in the last minute";
    else if (audience.current === 0 && !audience.largest_session) audienceNote = "No live sessions right now";
    else audienceNote = [
      audState === "partial" ? `${audience.unsampled_now} live session(s) not sampled` : null,
      audience.largest_session_title ? `largest: ${audience.largest_session_title}` : null,
    ].filter(Boolean).join(" · ") || null;
  } else if (audState === "not_sampled") {
    audienceValue = null;
    audienceNote = `Broadcasts ran in the ${period}, but no audience samples were recorded`;
  } else {
    audienceNote = [
      audience.peak === 0 && audience.sampling_coverage_pct == null ? `No broadcasts in the ${period}` : null,
      audience.average != null && audience.peak ? `avg ${compact(audience.average)} while live` : null,
      audState === "partial" ? `sampled ${audience.sampling_coverage_pct}% of broadcast time — the peak is a floor` : null,
      audience.highest_session_peak != null ? `highest single-session peak ${compact(audience.highest_session_peak)}` : null,
      audience.current != null ? `${compact(audience.current)} now` : null,
    ].filter(Boolean).join(" · ");
  }
  // Only for the windowed peak: in Live the headline is the audience NOW, and a badge
  // comparing two peaks beside it would describe a different number.
  const audienceBadge = windowed ? badge(audience.comparison) : { delta: null, up: undefined };

  // ── API health: requests, error rate, p95 and the line all from the SAME window's rows.
  let apiValue = null;
  let apiNote;
  if (apiHealth.measurement_state === "not_measured" || apiHealth.requests == null) {
    apiNote = `Not measured — no request data was collected for the ${period}`;
  } else if (apiHealth.requests === 0) {
    apiNote = `0 requests in the ${period} — no error rate to report`;
  } else {
    apiValue = pct(apiHealth.value);
    const p95 = apiHealth.p95_ms == null ? null
      : apiHealth.p95_overflow ? `p95 > ${apiHealth.p95_ms}ms` : `p95 ≤ ${apiHealth.p95_ms}ms`;
    const prevReq = apiHealth.comparison?.previous;
    apiNote = [
      p95,
      `${compact(apiHealth.requests)} requests${prevReq != null ? ` (${compact(prevReq)} in the previous period)` : ""}`,
      apiHealth.measurement_state === "partial" ? `${apiHealth.coverage_pct}% of the period collected` : null,
    ].filter(Boolean).join(" · ");
  }

  return (
    // Five tiles. Playback quality was the sixth and is gone: nothing writes playback_*
    // metrics, and an overview is the wrong place to explain a missing integration.
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <MetricTile
        label="Platform health"
        caption={`Now · trend ${period}`}
        value={HEALTH_WORD[health.status] || null}
        unit={health.total ? `${health.ok}/${health.total}` : null}
        tone={HEALTH_TONE[health.status]}
        delta={healthBadge.delta}
        up={healthBadge.up}
        note={healthNote}
        series={health.series}
        color="#22c55e"
        to="/admin/status"
        linkLabel="Current Status"
        age={age}
      />

      <MetricTile
        label="Live sessions"
        caption="Now"
        value={live.value != null ? live.value.toLocaleString() : null}
        note={liveNote}
        series={live.series}
        color="#8b5cf6"
        to="/admin/live-events"
        linkLabel="Live Now"
        age={age}
      />

      <MetricTile
        label="At-risk sessions"
        caption="Now"
        value={risk.total != null ? risk.total.toLocaleString() : null}
        tone={risk.total > 0 ? "text-rose-600 dark:text-rose-400" : undefined}
        delta={risk.critical ? String(risk.critical) : null}
        up={false}
        note={
          risk.total
            ? `${riskBreakdown}${risk.dominant_stage ? ` — dominant: ${cap(risk.dominant_stage)}` : ""}`
            : "No sessions need attention"
        }
        series={null}
        color="#f43f5e"
        to="/admin/live-events"
        linkLabel="At Risk"
        age={age}
      />

      <MetricTile
        label="Concurrent audience"
        caption={windowed ? `Peak · ${period}` : "Now"}
        value={audienceValue}
        delta={audienceBadge.delta}
        up={audienceBadge.up}
        note={audienceNote}
        series={audience.series}
        color="#22d3ee"
        to="/admin/live-events"
        linkLabel="Live Now"
        age={age}
      />

      <MetricTile
        label="API health"
        caption={`Error rate · ${period}`}
        value={apiValue}
        unit={apiValue != null ? "% err" : null}
        tone={apiHealth.value > 1 ? "text-rose-600 dark:text-rose-400" : undefined}
        note={apiNote}
        series={apiHealth.series}
        color="#f59e0b"
        to="/admin/developers"
        linkLabel="Traffic"
        age={age}
      />
    </div>
  );
}

const cap = (s = "") => s.charAt(0).toUpperCase() + s.slice(1);
