import { Link } from "react-router-dom";
import Panel from "../Panel";
import HealthDot from "../HealthDot";

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";

// Section 8 — Platform Health. Real signals from GET /admin/platform-health: the
// database is actually pinged, integrated services (streaming/email) report their
// configured state, and anything not wired up yet reports "not_configured" instead of
// a fabricated latency/uptime number.
export default function Infrastructure({ health }) {
  const services = health?.services || [];
  return (
    <Panel
      title="Platform Health"
      eyebrow="Infrastructure"
      flush
      action={
        <Link to="/admin/status" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          Full status →
        </Link>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px]">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className={th}>Service</th>
              <th className={th}>Note</th>
              <th className={`${th} text-right`}>Latency</th>
              <th className={`${th} text-right`}>Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {services.map((s) => (
              <tr key={s.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                <td className={`${td} font-medium text-slate-800 dark:text-slate-100`}>{s.name}</td>
                <td className={`${td} text-slate-500 dark:text-slate-400`}>{s.note}</td>
                <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                  {s.latency_ms != null ? `${s.latency_ms}ms` : "—"}
                </td>
                <td className={`${td} text-right`}>
                  <span className="inline-flex justify-end">
                    <HealthDot status={s.status} />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
