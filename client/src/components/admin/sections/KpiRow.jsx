import MetricTile from "../MetricTile";
import { compact, seriesDelta } from "../format";

const HEALTH_TONE = {
  ok: "text-green-600 dark:text-green-400",
  warn: "text-amber-600 dark:text-amber-400",
  down: "text-rose-600 dark:text-rose-400",
};
const HEALTH_WORD = { ok: "Healthy", warn: "Degraded", down: "Disrupted" };

const pct1 = (n) => (n == null ? null : `${Number(n).toFixed(n >= 10 ? 1 : 2)}`);

export default function KpiRow({ kpis, age }) {
  const k = kpis || {};
  const health = k.platform_health || {};
  const live = k.live_sessions || {};
  const risk = k.at_risk_sessions || {};
  const audience = k.concurrent_audience || {};
  const quality = k.playback_quality || {};
  const apiHealth = k.api_health || {};

  const liveDelta = seriesDelta(live.series);
  const audienceDelta = seriesDelta(audience.series);
  const healthDelta = seriesDelta(health.series);

  const riskBreakdown = [
    risk.critical ? `${risk.critical} critical` : null,
    risk.high ? `${risk.high} high` : null,
    risk.monitoring ? `${risk.monitoring} monitoring` : null,
  ].filter(Boolean).join(" · ");

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      <MetricTile
        label="Platform health"
        value={HEALTH_WORD[health.status] || "—"}
        unit={health.total ? `${health.ok}/${health.total}` : null}
        tone={HEALTH_TONE[health.status]}
        delta={healthDelta ? `${healthDelta.pct}%` : null}
        up={healthDelta?.up}
        note={
          health.total
            ? `${health.unavailable || 0} unavailable · ${health.total - health.ok} not reporting`
            : null
        }
        series={health.series}
        color="#22c55e"
        to="/admin/status"
        linkLabel="Current Status"
        age={age}
      />

      <MetricTile
        label="Live sessions"
        value={live.value != null ? live.value.toLocaleString() : null}
        delta={liveDelta ? `${liveDelta.pct}%` : null}
        up={liveDelta?.up}
        note={`${live.starting_soon || 0} starting in 30 min · ${live.unattended || 0} unattended`}
        series={live.series}
        color="#8b5cf6"
        to="/admin/live-events"
        linkLabel="Live Now"
        age={age}
      />

      <MetricTile
        label="At-risk sessions"
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
        value={audience.value != null ? compact(audience.value) : null}
        delta={audienceDelta ? `${audienceDelta.pct}%` : null}
        up={audienceDelta?.up}
        note={
          audience.value != null
            ? [
                audience.peak != null ? `Peak ${compact(audience.peak)}` : null,
                audience.largest_session_title ? `largest: ${audience.largest_session_title}` : null,
              ].filter(Boolean).join(" · ")
            : "No live audience in this window"
        }
        series={audience.series}
        color="#22d3ee"
        to="/admin/live-events"
        linkLabel="Live Now"
        age={age}
      />

      <MetricTile
        label="Playback quality"
        value={pct1(quality.value)}
        unit={quality.value != null ? "%" : null}
        note={
          quality.value != null
            ? [
                quality.startup_ms != null ? `Startup ${(quality.startup_ms / 1000).toFixed(1)}s` : null,
                quality.rebuffer_ratio != null ? `rebuffer ${quality.rebuffer_ratio}%` : null,
                quality.fatal_ratio != null ? `fatal ${quality.fatal_ratio}%` : null,
              ].filter(Boolean).join(" · ")
            : quality.note
        }
        series={quality.series}
        color="#ec4899"
        to="/admin/analytics"
        linkLabel="Delivery"
        age={age}
      />

      <MetricTile
        label="API health"
        value={pct1(apiHealth.value)}
        unit={apiHealth.value != null ? "% err" : null}
        tone={apiHealth.value > 1 ? "text-rose-600 dark:text-rose-400" : undefined}
        note={
          apiHealth.value != null
            ? `p95 ${apiHealth.p95_ms}ms · ${compact(apiHealth.requests)} requests`
            : "No requests measured yet in this window"
        }
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
