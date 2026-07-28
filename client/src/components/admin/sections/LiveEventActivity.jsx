import { Link } from "react-router-dom";
import { FiRadio } from "react-icons/fi";
import Panel from "../Panel";
import HealthDot from "../HealthDot";
import { timeAgo } from "../format";

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";
const dash = <span className="text-slate-300 dark:text-slate-600">—</span>;

// Section 6 — Live Event Activity. Real currently-live streams from /admin/live-events.
// Viewers/bitrate render as "—" until LiveKit room stats are integrated — the backend
// returns null there rather than a guess, and so do we.
export default function LiveEventActivity({ events = [] }) {
  const rows = [...events]
    .sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0))
    .slice(0, 6);

  if (rows.length === 0) {
    return (
      <Panel eyebrow="Realtime" title="Live Event Activity" flush>
        <div className="flex items-center gap-2.5 px-5 py-6 text-sm text-slate-400 dark:text-slate-500">
          <FiRadio /> No events are broadcasting right now.
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      eyebrow={`${rows.length} broadcasting`}
      title="Live Event Activity"
      action={
        <Link to="/admin/live-events" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          Full monitor →
        </Link>
      }
      flush
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px]">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={th}>Event</th>
              <th className={`${th} text-right`}>Viewers</th>
              <th className={`${th} text-right`}>Bitrate</th>
              <th className={`${th} text-right`}>Health</th>
              <th className={`${th} text-right`}>Started</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((e) => (
              <tr key={e.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <td className={td}>
                  <p className="font-medium text-slate-800 dark:text-slate-100">{e.title || "Untitled stream"}</p>
                  <p className="text-xs text-slate-400">{e.organization || e.channel || "—"}</p>
                </td>
                <td className={`${td} text-right font-semibold tabular-nums text-slate-900 dark:text-white`}>
                  {e.viewers != null ? e.viewers.toLocaleString() : dash}
                </td>
                <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                  {e.bitrate_kbps != null ? `${(e.bitrate_kbps / 1000).toFixed(1)} Mbps` : dash}
                </td>
                <td className={`${td} text-right`}>
                  <span className="inline-flex justify-end">
                    <HealthDot status={e.health || "ok"} />
                  </span>
                </td>
                <td className={`${td} text-right text-slate-400`}>{timeAgo(e.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
