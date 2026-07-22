import { Link } from "react-router-dom";
import Panel from "../Panel";
import HealthDot from "../HealthDot";
import { liveEvents } from "../../../data/liveEvents";

const live = liveEvents
  .filter((e) => e.status === "live")
  .sort((a, b) => b.viewers - a.viewers)
  .slice(0, 6);

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";

// Section 6 — Live Event Activity. The busiest events broadcasting right now, densest
// where scanning matters, with a jump to the full monitor.
export default function LiveEventActivity() {
  return (
    <Panel
      eyebrow={`${live.length} broadcasting`}
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
              <th className={`${th} text-right`}>Latency</th>
              <th className={`${th} text-right`}>Health</th>
              <th className={`${th} text-right`}>Started</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {live.map((e) => (
              <tr key={e.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <td className={td}>
                  <p className="font-medium text-slate-800 dark:text-slate-100">{e.title}</p>
                  <p className="text-xs text-slate-400">{e.org}</p>
                </td>
                <td className={`${td} text-right font-semibold tabular-nums text-slate-900 dark:text-white`}>
                  {e.viewers.toLocaleString()}
                </td>
                <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                  {e.bitrate} Mbps
                </td>
                <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                  {e.latency}ms
                </td>
                <td className={`${td} text-right`}>
                  <span className="inline-flex justify-end">
                    <HealthDot status={e.health} />
                  </span>
                </td>
                <td className={`${td} text-right text-slate-400`}>{e.time.replace("Started ", "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
