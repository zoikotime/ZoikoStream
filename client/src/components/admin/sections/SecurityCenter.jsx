import { FiArrowRight } from "react-icons/fi";
import StatsCard from "../../../ui/StatsCard";
import SectionHeading from "../SectionHeading";
import SectionCard from "../SectionCard";
import HealthDot from "../HealthDot";
import { ICONS } from "../icons";
import { security } from "../../../data/platform";

const ACCENTS = { failed: "rose", blocked: "rose", abuse: "amber", ratelimit: "blue" };

// Section 6 — auth, abuse and rate-limit signals plus the recent security event log.
export default function SecurityCenter() {
  return (
    <section className="space-y-4">
      <SectionHeading
        title="Security Center"
        description="Authentication, abuse and rate-limit signals (last 24h)"
        action={
          <a href="/admin/security" className="inline-flex items-center gap-1 text-sm font-medium text-violet-600 hover:underline dark:text-violet-400">
            Security console <FiArrowRight />
          </a>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {security.stats.map((s) => (
          <StatsCard
            key={s.key}
            title={s.label}
            value={s.value}
            icon={ICONS[s.icon]}
            accent={ACCENTS[s.key] || "violet"}
            delta={s.delta}
            up={s.up}
          />
        ))}
      </div>

      <SectionCard title="Recent Security Events" padding="none">
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {security.events.map((e) => (
            <li key={e.title} className="flex items-start justify-between gap-3 px-5 py-3">
              <div className="flex min-w-0 gap-3">
                <HealthDot status={e.status} label="" className="mt-1" />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{e.title}</p>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">{e.detail}</p>
                </div>
              </div>
              <span className="shrink-0 text-[11px] text-slate-400">{e.when}</span>
            </li>
          ))}
        </ul>
      </SectionCard>
    </section>
  );
}
