import { Link } from "react-router-dom";
import { CONSOLE, SEVERITY, cx, type } from "../../../ui/tokens";
import Panel from "../Panel";
import { initials } from "../format";

// Live sessions requiring attention. Rows are ordered by severity then age, because during
// an incident the top of this table is the next thing a human should touch.
//
// Every row is derived from a real signal — a paused session, a live session with nothing
// on stage, a recording that was enabled but never attached, an unresolved single-path
// override — or raised by an operator. The `issue` string names which.
const SEV_LABEL = { critical: "Critical", high: "High", monitoring: "Monitoring" };

// Elapsed time since the issue opened, as mm:ss — the shape an incident clock is read in.
function elapsed(iso) {
  if (!iso) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const m = Math.floor(secs / 60);
  if (m < 60) return `${String(m).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

const cap = (s = "") => s.charAt(0).toUpperCase() + s.slice(1);

export default function SessionsAttention({ items = [] }) {
  return (
    <Panel
      title="Live sessions requiring attention"
      count={items.length}
      action={
        <Link to="/admin/live-events" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
          Open Live Operations →
        </Link>
      }
      flush
      className="flex flex-col"
    >
      {items.length === 0 ? (
        <div className="px-5 py-10 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No sessions need attention</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            Every live session is publishing, attended and capturing as configured.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[42rem] text-left">
            <thead>
              <tr className={cx("border-b", CONSOLE.divider)}>
                {["Severity", "Event", "Issue", "Time", "Owner", ""].map((h) => (
                  <th
                    key={h}
                    scope="col"
                    className={cx(
                      "px-4 py-2.5 text-[10px] font-semibold uppercase tracking-[0.1em]",
                      CONSOLE.faint
                    )}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className={cx("divide-y", CONSOLE.divideY)}>
              {items.map((it) => (
                <tr key={it.id} className="transition-colors hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                  <td className="px-4 py-3 align-top">
                    <span
                      className={cx(
                        "inline-block rounded px-2 py-0.5 text-[11px] font-semibold",
                        SEVERITY[it.severity] || SEVERITY.monitoring
                      )}
                    >
                      {SEV_LABEL[it.severity] || cap(it.severity)}
                    </span>
                  </td>
                  <td className="max-w-[15rem] px-4 py-3 align-top">
                    <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{it.event}</p>
                    <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
                      {[it.organization, it.impact === "unrepeatable" ? "Unrepeatable" : null]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </td>
                  <td className={cx("max-w-[14rem] px-4 py-3 align-top text-[12px]", CONSOLE.body)}>
                    {it.stage && <span className={CONSOLE.faint}>{cap(it.stage)} · </span>}
                    {it.issue}
                  </td>
                  <td className={cx("whitespace-nowrap px-4 py-3 align-top text-[12px]", type.mono, CONSOLE.body)}>
                    {elapsed(it.started_at)}
                  </td>
                  <td className="px-4 py-3 align-top">
                    {it.owner ? (
                      <span className="flex items-center gap-2">
                        <span
                          className={cx(
                            "grid h-6 w-6 shrink-0 place-items-center rounded-full text-[9px] font-bold",
                            "bg-violet-100 text-violet-700 dark:bg-violet-500/20 dark:text-violet-300"
                          )}
                        >
                          {initials(it.owner)}
                        </span>
                        <span className={cx("truncate text-[12px]", CONSOLE.body)}>{it.owner}</span>
                      </span>
                    ) : (
                      <span className="text-[12px] font-medium text-amber-600 dark:text-amber-400">
                        Unassigned
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 align-top">
                    <Link
                      to={`/admin/live-events?event=${it.event_id}`}
                      className={cx("whitespace-nowrap text-[12px] font-semibold", CONSOLE.link)}
                    >
                      Open →
                    </Link>
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
