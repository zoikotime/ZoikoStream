import { FiAlertTriangle, FiCheckCircle, FiActivity } from "react-icons/fi";
import { Section, SectionHeading, Reveal, Counter, Chip } from "../../../ui";
import { ENTERPRISE_METRICS, ENTERPRISE_ALERTS, ENTERPRISE_OPS } from "../../../data/home";

// Tiny inline sparkline (dummy data) — pure SVG, no chart lib.
function Sparkline() {
  const pts = [8, 12, 9, 14, 11, 18, 16, 22, 19, 26, 24, 30];
  const max = Math.max(...pts);
  const d = pts.map((p, i) => `${(i / (pts.length - 1)) * 100},${40 - (p / max) * 36}`).join(" ");
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-16 w-full">
      <polyline points={d} fill="none" stroke="currentColor" strokeWidth="2" className="text-emerald-500" vectorEffect="non-scaling-stroke" />
      <polygon points={`0,40 ${d} 100,40`} className="fill-emerald-500/10" />
    </svg>
  );
}

const ALERT_ICON = { ok: FiCheckCircle, warn: FiAlertTriangle };
const ALERT_TONE = { ok: "text-emerald-600 dark:text-emerald-400", warn: "text-amber-600 dark:text-amber-400" };

export default function EnterpriseOperations() {
  return (
    <Section id="enterprise" tone="subtle">
      <SectionHeading
        eyebrow="Enterprise media operations"
        title="Run your media platform from one control room"
        lead="Monitor every stream, recording, and viewer in real time — with the alerts, analytics, and replay controls your operations team needs to keep broadcasts flawless."
      />

      {/* Dashboard mockup */}
      <Reveal className="mt-14">
        <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl dark:border-slate-800 dark:bg-slate-900 dark:shadow-black/40">
          {/* Mock topbar */}
          <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50/80 px-5 py-3 dark:border-slate-800 dark:bg-slate-800/40">
            <FiActivity className="text-emerald-600 dark:text-emerald-400" />
            <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">Operations · Live</span>
            <span className="ml-auto flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /> All systems operational
            </span>
          </div>

          <div className="grid gap-px bg-slate-100 lg:grid-cols-3 dark:bg-slate-800">
            {/* Metrics */}
            <div className="bg-white p-5 lg:col-span-2 dark:bg-slate-900">
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {ENTERPRISE_METRICS.map((m) => (
                  <div key={m.label} className="rounded-2xl border border-slate-200 p-4 transition-colors hover:border-emerald-300 dark:border-slate-700 dark:hover:border-emerald-500/40">
                    <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{m.label}</p>
                    <p className="mt-1 text-2xl font-bold text-slate-900 dark:text-white">
                      <Counter value={m.value} suffix={m.suffix} decimals={m.decimals || 0} />
                    </p>
                  </div>
                ))}
              </div>
              <div className="mt-4 rounded-2xl border border-slate-200 p-4 dark:border-slate-700">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Concurrent viewers · 24h</p>
                  <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">▲ 18%</span>
                </div>
                <Sparkline />
              </div>
            </div>

            {/* Alerts + ops */}
            <div className="space-y-4 bg-white p-5 dark:bg-slate-900">
              <div>
                <p className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">Alerts</p>
                <ul className="space-y-2">
                  {ENTERPRISE_ALERTS.map((a, i) => {
                    const Icon = ALERT_ICON[a.level];
                    return (
                      <li key={i} className="flex items-start gap-2 rounded-xl border border-slate-100 bg-slate-50/60 p-2.5 dark:border-slate-700 dark:bg-slate-800/40">
                        <Icon className={`mt-0.5 shrink-0 ${ALERT_TONE[a.level]}`} />
                        <div className="min-w-0">
                          <p className="truncate text-sm text-slate-700 dark:text-slate-200">{a.text}</p>
                          <p className="text-xs text-slate-400 dark:text-slate-500">{a.time}</p>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
              <div>
                <p className="mb-2 text-sm font-semibold text-slate-800 dark:text-slate-100">Capabilities</p>
                <div className="flex flex-wrap gap-1.5">
                  {ENTERPRISE_OPS.map((o) => <Chip key={o}>{o}</Chip>)}
                </div>
              </div>
            </div>
          </div>
        </div>
      </Reveal>
    </Section>
  );
}
