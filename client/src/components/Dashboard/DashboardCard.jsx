import { FiArrowUpRight, FiArrowDownRight } from "react-icons/fi";

// Static accent -> classes map so Tailwind keeps these in the build.
const ACCENTS = {
  violet: "bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400",
  indigo: "bg-indigo-100 text-indigo-600 dark:bg-indigo-500/15 dark:text-indigo-400",
  emerald: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400",
  blue: "bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400",
  amber: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400",
  rose: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400",
};

// Reusable KPI card: icon, title, value, optional delta % and live pulse. Hover lift.
export default function DashboardCard({ title, value, icon: Icon, accent = "violet", delta, up = true, live }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-md dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center gap-3">
        <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${ACCENTS[accent]}`}>
          <Icon className="text-xl" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-500 dark:text-neutral-400">{title}</p>
          <p className="text-2xl font-bold text-slate-900 dark:text-white">{value}</p>
        </div>
      </div>

      {(delta || live) && (
        <div className="mt-3 flex items-center gap-1 text-xs font-medium">
          {live ? (
            <span className="text-emerald-600 dark:text-emerald-400">
              <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500 align-middle" />
              Live now
            </span>
          ) : (
            <span
              className={`inline-flex items-center gap-0.5 ${
                up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"
              }`}
            >
              {up ? <FiArrowUpRight /> : <FiArrowDownRight />} {delta}
            </span>
          )}
          {delta && !live && (
            <span className="text-slate-400 dark:text-neutral-500">vs last week</span>
          )}
        </div>
      )}
    </div>
  );
}
