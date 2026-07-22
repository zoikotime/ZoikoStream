import { Link } from "react-router-dom";
import { FiCheckCircle } from "react-icons/fi";
import toast from "react-hot-toast";
import Panel from "../Panel";
import HealthDot from "../HealthDot";
import { cx } from "../../../ui/tokens";
import { alerts } from "../../../data/platform";

const ACTIVE = ["critical", "warning"];
const BAR = { critical: "bg-rose-500", warning: "bg-amber-500", info: "bg-blue-500" };

// Section 2 — Critical Alerts. Collapses to a slim "all clear" bar when nothing is
// active, so the section only demands attention when it should.
export default function CriticalAlerts() {
  const active = alerts.filter((a) => ACTIVE.includes(a.severity));

  if (active.length === 0) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-green-200 bg-green-50 px-5 py-3.5 text-sm font-medium text-green-700 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-400">
        <FiCheckCircle /> No active incidents — all systems clear.
      </div>
    );
  }

  return (
    <Panel
      eyebrow={`${active.length} active`}
      title="Critical Alerts"
      action={
        <Link to="/admin/status" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          Incident center →
        </Link>
      }
      flush
    >
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {active.map((a) => (
          <li key={a.id} className="flex items-start gap-3 px-5 py-3.5">
            <span className={cx("mt-0.5 h-9 w-1 shrink-0 rounded-full", BAR[a.severity])} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="truncate font-medium text-slate-900 dark:text-white">{a.title}</p>
                <HealthDot status={a.severity} badge>{a.severity}</HealthDot>
              </div>
              <p className="truncate text-sm text-slate-500 dark:text-slate-400">{a.detail}</p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="hidden text-xs text-slate-400 sm:block">{a.when}</span>
              <button
                onClick={() => toast.success(`Acknowledged: ${a.title}`)}
                className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                Ack
              </button>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
