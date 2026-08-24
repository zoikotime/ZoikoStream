import { useEffect } from "react";
import { Panel, HealthDot } from "../../components/admin";
import api, { diagnoseLoadError } from "../../api";
import useApi from "../../hooks/useApi";
import Skeleton from "../../ui/Skeleton";

const VERDICT = {
  ok: { status: "ok", label: "All systems operational" },
  warn: { status: "warn", label: "Minor degradation" },
  down: { status: "down", label: "Service disruption" },
};

const th = "px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400";
const td = "px-5 py-3 text-sm";

function usePlatformHealth() {
  return useApi(() => api.get("/admin/platform-health").then((r) => r.data));
}

// Full platform health console — GET /admin/platform-health. The database check is a real
// ping; integrated services report their configured state; anything not wired up yet shows
// "not_configured" (informational, not counted as an outage) instead of a fabricated uptime.
export default function SystemStatus() {
  const { data, loading, error, reload } = usePlatformHealth();

  // Light auto-refresh so the console stays current without a manual reload.
  useEffect(() => {
    const t = setInterval(reload, 30000);
    return () => clearInterval(t);
  }, [reload]);

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        {diagnoseLoadError(error, "/admin/platform-health")}
      </div>
    );
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-[1000px] space-y-6">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="block" className="h-16" />
        <Skeleton variant="block" className="h-72" />
      </div>
    );
  }

  const verdict = VERDICT[data.overall] || VERDICT.ok;
  const services = data.services || [];
  const configured = services.filter((s) => s.status !== "not_configured");
  const pending = services.filter((s) => s.status === "not_configured");

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">System Status</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Live infrastructure health, auto-refreshing every 30s</p>
      </div>

      <div className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-white px-5 py-3.5 dark:border-slate-800 dark:bg-slate-900/50">
        <HealthDot status={verdict.status} pulse badge>{verdict.label}</HealthDot>
        <span className="text-sm text-slate-400">
          {configured.length} of {services.length} services integrated
        </span>
      </div>

      <Panel title="Services" eyebrow="Infrastructure" flush>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
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

      {pending.length > 0 && (
        <p className="text-xs text-slate-400">
          {pending.length} {pending.length === 1 ? "service is" : "services are"} not yet integrated: {pending.map((s) => s.name).join(", ")}.
        </p>
      )}
    </div>
  );
}
