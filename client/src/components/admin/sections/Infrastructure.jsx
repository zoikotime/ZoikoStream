import Panel from "../Panel";
import HealthDot, { healthColor } from "../HealthDot";
import { Sparkline } from "../../../ui/charts";
import { cx } from "../../../ui/tokens";
import { healthServices, regions } from "../../../data/platform";

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";

const loadTone = (load) =>
  load >= 80 ? "bg-rose-500" : load >= 65 ? "bg-amber-500" : "bg-green-500";

// Section 8 — Infrastructure Monitoring. A scannable services table (status · latency ·
// availability · trend) beside regional load — the engineering-console view, not big cards.
export default function Infrastructure() {
  return (
    <div className="grid gap-6 xl:grid-cols-3">
      <Panel title="Services" eyebrow="Infrastructure" className="xl:col-span-2" flush>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px]">
            <thead>
              <tr className="border-b border-slate-100 dark:border-slate-800">
                <th className={th}>Service</th>
                <th className={`${th} text-right`}>Latency</th>
                <th className={`${th} text-right`}>Availability</th>
                <th className={`${th} text-right`}>Trend (14d)</th>
                <th className={`${th} text-right`}>Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {healthServices.map((s) => (
                <tr key={s.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                  <td className={`${td} font-medium text-slate-800 dark:text-slate-100`}>{s.name}</td>
                  <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                    {s.latency}ms
                  </td>
                  <td className={`${td} text-right font-mono tabular-nums text-slate-600 dark:text-slate-300`}>
                    {s.availability}%
                  </td>
                  <td className={`${td}`}>
                    <div className="ml-auto w-24">
                      <Sparkline data={s.trend} color={healthColor(s.status)} height={26} />
                    </div>
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

      <Panel title="Regions" eyebrow="Edge">
        <ul className="space-y-4">
          {regions.map((r) => (
            <li key={r.id}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{r.name}</span>
                <HealthDot status={r.status} />
              </div>
              <div className="mt-1.5 flex items-center gap-3">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className={cx("h-full rounded-full", loadTone(r.load))} style={{ width: `${r.load}%` }} />
                </div>
                <span className="w-20 shrink-0 text-right font-mono text-xs tabular-nums text-slate-400">
                  {r.status === "down" ? "offline" : `${r.latency}ms · ${r.load}%`}
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
