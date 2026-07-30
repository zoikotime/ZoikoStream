import { Link } from "react-router-dom";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Panel from "../admin/Panel";
import { timeAgo } from "../admin/format";

// Live and recent broadcast sessions for this organization.
//
// MODE is the session's operating mode, which the BroadcastSession row records. The ingest
// PROTOCOL and region shown in the design are not stored anywhere in this stack, so the
// second line carries what we do know instead of an invented endpoint.
const MODE = {
  live: "bg-green-100 text-green-700 dark:bg-green-500/15 dark:text-green-400",
  test: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  paused: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400",
  ended: "bg-slate-100 text-slate-600 dark:bg-white/[0.07] dark:text-neutral-300",
};

const STATE_TONE = {
  Healthy: "text-green-600 dark:text-green-400",
  Paused: "text-blue-600 dark:text-blue-400",
  Archived: CONSOLE.faint,
  Ended: CONSOLE.faint,
};

// Elapsed run time from real timestamps: live sessions count up to now.
function duration(startedAt, endedAt) {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  const mins = Math.max(0, Math.floor((end - start) / 60000));
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  return `${h}h ${String(mins % 60).padStart(2, "0")}m`;
}

export default function SessionsTable({ sessions }) {
  const items = sessions?.items || [];

  return (
    <Panel
      title="Live / recent sessions"
      action={
        <Link to="/organization/sessions" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Streaming Sessions →
        </Link>
      }
      flush
    >
      {items.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No sessions yet</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Broadcasts appear here as soon as a host goes live.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left">
            <thead>
              <tr className={cx("border-b", CONSOLE.divider)}>
                {["Session", "Mode", "Started", "State"].map((h) => (
                  <th
                    key={h}
                    scope="col"
                    className={cx("px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint)}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className={cx("divide-y", CONSOLE.divideY)}>
              {items.map((s) => (
                <tr key={s.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                  <td className="max-w-[16rem] px-4 py-3">
                    <Link
                      to={`/organization/events/${s.event_id}`}
                      className={cx("block truncate text-[13px] font-semibold hover:underline", CONSOLE.heading)}
                    >
                      {s.title}
                    </Link>
                    <p className={cx("truncate text-[11px]", type.mono, CONSOLE.faint)}>
                      {s.peak_viewers > 0 ? `peak ${s.peak_viewers.toLocaleString()} viewers` : "no audience recorded"}
                    </p>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={cx(
                        "inline-block rounded px-2 py-0.5 text-[11px] font-semibold capitalize",
                        MODE[s.mode] || MODE.ended
                      )}
                    >
                      {s.mode}
                    </span>
                  </td>
                  <td className={cx("whitespace-nowrap px-4 py-3 text-[12px]", type.mono, CONSOLE.body)}>
                    {s.ended_at ? timeAgo(s.ended_at) : duration(s.started_at)}
                  </td>
                  <td className={cx("whitespace-nowrap px-4 py-3 text-[12px] font-medium", STATE_TONE[s.state] || CONSOLE.body)}>
                    {s.state}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
