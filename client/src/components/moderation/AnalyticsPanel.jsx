// client/src/components/moderation/AnalyticsPanel.jsx
// Live analytics, shared by the HOST control room and the MODERATOR console.
//
// It lives in moderation/* rather than host/* because both consoles render it and the import
// direction in this codebase is host -> moderation (see components/host/HostPanel). It was
// originally written inside HostPanel; moving it means one implementation instead of a
// moderator copy that drifts.
//
// Every figure here comes from services/broadcast.analytics_now, which computes from real rows
// and real presence records. Where this stack has no source (per-viewer geography), the server
// sends null plus a reason and this renders the reason — never a plausible-looking number.
import { FiTrendingUp, FiSmartphone, FiMonitor, FiGlobe } from "react-icons/fi";
import EmptyState from "../organization/OrganizationEmptyState";

const fmtDuration = (s) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : m >= 1 ? `${m}m ${s % 60}s` : `${s}s`;
};

export function Stat({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-200 p-2.5 dark:border-slate-800">
      <p className="truncate text-[11px] text-slate-500 dark:text-slate-400" title={hint}>{label}</p>
      <p className="text-lg font-semibold tabular-nums text-slate-900 dark:text-white">{value}</p>
    </div>
  );
}

// Retention sparkline. Inline SVG rather than a chart library: it's a single polyline over
// the server's analytics samples, and recharts is already loaded elsewhere for real charts.
function Retention({ points }) {
  if (!points?.length) {
    return <p className="py-6 text-center text-xs text-slate-400">Collecting samples… the graph fills in every 15s.</p>;
  }
  const values = points.map((p) => p.viewers);
  const max = Math.max(...values, 1);
  const step = points.length > 1 ? 100 / (points.length - 1) : 0;
  const path = values.map((v, i) => `${i * step},${40 - (v / max) * 36}`).join(" ");
  return (
    <div>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-20 w-full" role="img"
           aria-label={`Viewer retention, peaking at ${max}`}>
        <polyline points={`0,40 ${path} 100,40`} fill="rgb(16 185 129 / 0.15)" stroke="none" />
        <polyline points={path} fill="none" stroke="rgb(16 185 129)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between text-[10px] text-slate-400">
        <span>{points.length} samples</span>
        <span>peak {max.toLocaleString()}</span>
      </div>
    </div>
  );
}

function Distribution({ title, icon: Icon, rows, note }) {
  const total = (rows || []).reduce((s, r) => s + r.value, 0);
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        <Icon aria-hidden="true" /> {title}
      </p>
      {!rows?.length ? (
        <p className="text-xs text-slate-400">{note || "No data yet."}</p>
      ) : (
        <div className="space-y-1">
          {rows.map((r) => (
            <div key={r.label} className="relative overflow-hidden rounded-lg border border-slate-200 px-2 py-1 dark:border-slate-700">
              <div className="absolute inset-y-0 left-0 bg-violet-500/15 transition-[width] duration-500"
                   style={{ width: `${total ? (r.value / total) * 100 : 0}%` }} />
              <div className="relative flex items-center justify-between text-xs">
                <span className="text-slate-700 dark:text-slate-200">{r.label}</span>
                <span className="tabular-nums text-slate-500 dark:text-slate-400">{r.value}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * `variant="moderator"` drops the encoder/audience-composition detail a moderator has no
 * lever over (retention curve, device mix) and keeps the room figures they act on. Same
 * component, because the numbers and their caveats must not be described twice.
 */
export default function AnalyticsPanel({ analytics, health, variant = "host" }) {
  const a = analytics;
  if (!a) {
    return <EmptyState icon={FiTrendingUp} title="No analytics yet" description="Numbers appear once the broadcast has an audience." className="py-10" />;
  }
  const full = variant !== "moderator";
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Live viewers" value={(a.viewers ?? 0).toLocaleString()} />
        <Stat label="Peak viewers" value={(a.peak_viewers ?? 0).toLocaleString()} />
        <Stat label="Concurrent users" value={(a.participants ?? 0).toLocaleString()} hint="Everyone connected, including staff" />
        {/* Room composition — the moderator's own workload, and useful to a host too. */}
        <Stat label="On stage" value={`${(a.speakers ?? 0) + (a.hosts ?? 0)}`} hint="Hosts and speakers currently publishing or staged" />
        <Stat label="Raised hands" value={(a.hands ?? 0).toLocaleString()} />
        <Stat label="In the lobby" value={(a.waiting ?? 0).toLocaleString()} hint="Waiting for admission" />
        <Stat label="Avg watch time" value={fmtDuration(a.avg_watch_seconds)} hint="Mean time in room of everyone currently connected" />
        <Stat label="Engagement" value={`${a.engagement ?? 0}/100`} hint="Weighted interactions per viewer — a heuristic, see services/broadcast.engagement_score" />
        <Stat label="Chat rate" value={`${a.chat_per_minute ?? 0}/min`} hint="Messages in the last minute" />
        <Stat label="Questions" value={(a.questions_asked ?? 0).toLocaleString()} />
        <Stat label="Poll votes" value={(a.poll_votes ?? 0).toLocaleString()} />
        <Stat
          label="Poll participation"
          value={a.poll_participation == null ? "—" : `${a.poll_participation}%`}
          hint="Votes as a share of peak viewers"
        />
        {full && <Stat label="Reactions" value={(a.reactions ?? 0).toLocaleString()} />}
        <Stat label="Poor connections" value={(a.poor_connections ?? 0).toLocaleString()} hint="Participants reporting poor or lost media" />
      </div>

      {health?.issues?.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">Health</p>
          <ul className="mt-1 space-y-0.5 text-xs text-amber-800 dark:text-amber-300">
            {health.issues.map((i) => <li key={i}>• {i}</li>)}
          </ul>
        </div>
      )}

      {/* Who actually held the floor. Accumulated server-side on the falling edge of each
          publisher's speaking flag, so it can't be inflated by a client. */}
      {a.speaking_time?.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Speaking time</p>
          <div className="space-y-1">
            {a.speaking_time.map((s) => (
              <div key={s.identity} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate text-slate-700 dark:text-slate-200">{s.name || s.identity}</span>
                <span className="shrink-0 tabular-nums text-slate-500 dark:text-slate-400">{fmtDuration(s.seconds)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {full && (
        <>
          {/* Encoder health, reported by the publishers' own peer connections — the only place
              outbound bitrate, loss and RTT exist. Absent (not zero) when nobody is publishing. */}
          {a.publish?.publishers > 0 && (
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Outbound bitrate" value={a.publish.bitrate_kbps == null ? "—" : `${a.publish.bitrate_kbps} kbps`} hint="Summed across every publisher" />
              <Stat label="Packet loss" value={a.publish.packet_loss == null ? "—" : `${a.publish.packet_loss}%`} />
              <Stat label="Round trip" value={a.publish.rtt_ms == null ? "—" : `${a.publish.rtt_ms} ms`} />
              <Stat label="Frame rate" value={a.publish.fps == null ? "—" : `${a.publish.fps} fps`} />
            </div>
          )}

          <div>
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Retention</p>
            <Retention points={a.retention} />
          </div>
          <Distribution title="Devices" icon={FiSmartphone} rows={a.devices} />
          <Distribution title="Platforms" icon={FiMonitor} rows={a.platforms} />
          <Distribution title="Browsers" icon={FiGlobe} rows={a.browsers} />
          {/* Stated, not faked: there's no GeoIP in this stack. */}
          <Distribution title="Countries" icon={FiGlobe} rows={a.countries} note={a.countries_note} />
        </>
      )}
    </div>
  );
}
