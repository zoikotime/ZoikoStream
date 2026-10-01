// client/src/components/organization/ViewingSessionsPanel.jsx
// Session-aware audience metrics (ZST-SPEC-VAP-001 §9), shown BESIDE the existing analytics,
// never instead of them: Peak Viewers, Watch Time, Avg Watch and every card above keep
// their own numbers. One session is one connect-to-leave cycle of one viewer credential
// (server/app/services/viewing_sessions.py), so these count sessions, not people.
//
// Missing data renders as "—" with a reason, never as 0: the server sends None for anything
// it did not measure, and this panel keeps it that way.
import Card from "../../ui/Card";

const DASH = "—";

const fmtSeconds = (s) => {
  if (s == null) return DASH;
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
};
const fmtCount = (n) => (n == null ? DASH : Number(n).toLocaleString());
const fmtRate = (r) => (r == null ? DASH : `${Math.round(r * 1000) / 10}%`);
const fmtMs = (ms) => (ms == null ? DASH : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

const BUCKET_LABEL = {
  before_start: "Before start",
  "0-5m": "0–5 min",
  "5-15m": "5–15 min",
  "15-30m": "15–30 min",
  "30-60m": "30–60 min",
  "60m+": "60+ min",
};

function Stat({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-100 px-4 py-3 dark:border-slate-800">
      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-white">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{hint}</p>}
    </div>
  );
}

export default function ViewingSessionsPanel({ summary, reports = [] }) {
  const measured = Boolean(summary?.measured);
  const rows = reports.filter((r) => r.sessions?.measured);
  const buckets = summary?.join_time_distribution || null;
  const bucketMax = buckets ? Math.max(1, ...buckets.map((b) => b.sessions)) : 1;

  return (
    <Card padding="md" data-testid="viewing-sessions-panel">
      <div className="mb-4">
        <h2 className="font-semibold text-slate-900 dark:text-white">Viewing sessions</h2>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Additional to the viewer figures above · one session per join-to-leave of a viewer · not unique people
        </p>
      </div>

      {!measured ? (
        <p className="py-6 text-center text-sm text-slate-400 dark:text-slate-500">
          No viewing sessions recorded in this window yet.
        </p>
      ) : (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Sessions admitted" value={fmtCount(summary.sessions_admitted)}
                  hint={`${summary.events_measured} event${summary.events_measured === 1 ? "" : "s"} measured`} />
            <Stat label="Peak concurrent sessions" value={fmtCount(summary.peak_concurrent_sessions)} hint="Highest single event" />
            <Stat label="Rejoin rate" value={fmtRate(summary.rejoin_rate)} hint="Sessions that were a viewer coming back" />
            <Stat label="Sessions reporting playback" value={fmtCount(summary.qoe_sessions_reporting)} hint="Startup time / failures sent by the player" />
          </div>

          {buckets && (
            <div>
              <h3 className="mb-2 text-sm font-medium text-slate-700 dark:text-slate-200">When sessions joined, relative to the broadcast start</h3>
              <ul className="space-y-1.5" aria-label="Join-time distribution">
                {buckets.map((b) => (
                  <li key={b.bucket} className="flex items-center gap-3 text-sm">
                    <span className="w-24 shrink-0 text-slate-500 dark:text-slate-400">{BUCKET_LABEL[b.bucket] || b.bucket}</span>
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                      <span className="block h-full rounded-full bg-violet-500" style={{ width: `${(b.sessions / bucketMax) * 100}%` }} />
                    </span>
                    <span className="w-12 shrink-0 text-right tabular-nums text-slate-600 dark:text-slate-300">{b.sessions}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <caption className="sr-only">Viewing sessions per event</caption>
              <thead>
                <tr className="border-b border-slate-100 text-xs uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-500">
                  <th scope="col" className="py-2 pr-3 font-medium">Event</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Sessions</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Peak concurrent</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Median watch / session</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Rejoin rate</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Startup (median)</th>
                  <th scope="col" className="py-2 font-medium">Failed sessions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const s = r.sessions;
                  return (
                    <tr key={r.id} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                      <td className="max-w-[220px] truncate py-2 pr-3 text-slate-700 dark:text-slate-200">{r.event}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtCount(s.sessions_admitted)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtCount(s.peak_concurrent_sessions)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtSeconds(s.median_watch_seconds)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtRate(s.rejoin_rate)}</td>
                      <td className="py-2 pr-3 tabular-nums">{fmtMs(s.qoe?.startup_ms_median)}</td>
                      <td className="py-2 tabular-nums">{fmtCount(s.qoe?.failed_sessions)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-slate-400 dark:text-slate-500">
            Rebuffering is not measured for live (WebRTC) playback. Rejoin rate excludes shared
            access-link sessions from older links, which cannot tell one viewer from another.
          </p>
        </div>
      )}
    </Card>
  );
}
