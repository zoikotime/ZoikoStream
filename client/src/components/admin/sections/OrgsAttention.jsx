import { useMemo } from "react";
import { Link } from "react-router-dom";
import { FiArrowRight, FiCheckCircle } from "react-icons/fi";
import Panel from "../Panel";
import HealthDot from "../HealthDot";
import { initials } from "../format";

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";

// Section 4 — Organizations Requiring Attention. Derived from real org + subscription
// state (not a fabricated list): a suspended org, or one whose subscription is past
// due, needs a look.
export default function OrgsAttention({ organizations = [] }) {
  const rows = useMemo(
    () =>
      organizations
        .filter((o) => o.status === "suspended" || o.subscription_status === "past_due")
        .map((o) => ({
          id: o.id,
          name: o.name,
          reason: o.status === "suspended" ? "Organization suspended" : "Subscription payment past due",
          status: o.status === "suspended" ? "error" : "warning",
        }))
        .slice(0, 8),
    [organizations]
  );

  if (rows.length === 0) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-green-200 bg-green-50 px-5 py-3.5 text-sm font-medium text-green-700 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-400">
        <FiCheckCircle /> No organizations need attention right now.
      </div>
    );
  }

  return (
    <Panel
      eyebrow={`${rows.length} need attention`}
      title="Organizations Requiring Attention"
      action={
        <Link to="/admin/organizations" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          All organizations →
        </Link>
      }
      flush
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px]">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={th}>Organization</th>
              <th className={th}>Issue</th>
              <th className={`${th} text-right`}>Status</th>
              <th className={`${th} w-12`} />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((r) => (
              <tr key={r.id} className="group hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <td className={td}>
                  <div className="flex items-center gap-3">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                      {initials(r.name)}
                    </span>
                    <span className="font-medium text-slate-800 dark:text-slate-100">{r.name}</span>
                  </div>
                </td>
                <td className={`${td} text-slate-500 dark:text-slate-400`}>{r.reason}</td>
                <td className={`${td} text-right`}>
                  <HealthDot status={r.status} badge>{r.status === "error" ? "Critical" : "Warning"}</HealthDot>
                </td>
                <td className={`${td} text-right`}>
                  <Link
                    to="/admin/organizations"
                    aria-label={`Open ${r.name}`}
                    className="inline-flex text-slate-400 transition group-hover:text-violet-600 dark:group-hover:text-violet-400"
                  >
                    <FiArrowRight />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
