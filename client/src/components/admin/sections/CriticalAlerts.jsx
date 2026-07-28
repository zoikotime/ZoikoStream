import { Link } from "react-router-dom";
import { FiCheckCircle } from "react-icons/fi";
import Panel from "../Panel";
import HealthDot from "../HealthDot";
import { cx } from "../../../ui/tokens";

const BAR = { down: "bg-rose-500", warn: "bg-amber-500" };

// Section 2 — Critical Alerts. Sourced from /admin/dashboard's recent_alerts, which is
// itself derived from real platform-health service statuses — degraded/down services
// only, never a fabricated incident feed. Collapses to a slim "all clear" bar when
// nothing is active.
export default function CriticalAlerts({ alerts = [] }) {
  if (alerts.length === 0) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-green-200 bg-green-50 px-5 py-3.5 text-sm font-medium text-green-700 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-400">
        <FiCheckCircle /> No active incidents — all systems clear.
      </div>
    );
  }

  return (
    <Panel
      eyebrow={`${alerts.length} active`}
      title="Critical Alerts"
      action={
        <Link to="/admin/status" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          System status →
        </Link>
      }
      flush
    >
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {alerts.map((a) => (
          <li key={a.id} className="flex items-start gap-3 px-5 py-3.5">
            <span className={cx("mt-0.5 h-9 w-1 shrink-0 rounded-full", BAR[a.severity] || BAR.warn)} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <p className="truncate font-medium text-slate-900 dark:text-white">{a.title}</p>
                <HealthDot status={a.severity} badge>{a.severity === "down" ? "down" : "degraded"}</HealthDot>
              </div>
              {a.detail && <p className="truncate text-sm text-slate-500 dark:text-slate-400">{a.detail}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
