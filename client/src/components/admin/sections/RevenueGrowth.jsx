import { Link } from "react-router-dom";
import Panel from "../Panel";
import { AreaTrend } from "../../../ui/charts";
import { CHART } from "../../../data/platform";
import { money, seriesDelta } from "../format";

const PLAN_PILL = {
  Enterprise: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
  Pro: "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300",
  Starter: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
};

function Figure({ label, value, delta }) {
  return (
    <div className="px-5 first:pl-0">
      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-slate-900 dark:text-white">{value}</p>
      {delta && <p className="mt-0.5 text-[11px] font-medium text-green-600 dark:text-green-400">▲ {delta}</p>}
    </div>
  );
}

// Section 5 — Revenue & Growth. MRR (current sum of active + trial subscription plan
// prices) and its monthly trend, next to the highest-paying live subscriptions. ARR is
// the standard MRR×12 projection — a real formula, not a fabricated figure.
export default function RevenueGrowth({ mrr, revenueGrowth = [], topAccounts = [] }) {
  const delta = seriesDelta(revenueGrowth);

  return (
    <Panel
      eyebrow="Billing"
      title="Revenue & Growth"
      action={
        <Link to="/admin/subscriptions" className="font-medium text-violet-600 hover:underline dark:text-violet-400">
          Subscriptions →
        </Link>
      }
    >
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="flex divide-x divide-slate-100 dark:divide-slate-800">
            <Figure label="MRR" value={money(mrr)} delta={delta ? `${delta.pct}%` : undefined} />
            <Figure label="ARR (projected)" value={money(mrr * 12)} />
          </div>
          <div className="mt-4">
            {revenueGrowth.length > 0 ? (
              <AreaTrend data={revenueGrowth} color={CHART.violet} height={192} showX valueFormatter={money} />
            ) : (
              <div className="grid h-48 place-items-center text-sm text-slate-400 dark:text-slate-500">
                Not enough billing history yet
              </div>
            )}
          </div>
        </div>

        <div className="lg:border-l lg:border-slate-100 lg:pl-6 dark:lg:border-slate-800">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            Top accounts
          </p>
          {topAccounts.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400 dark:text-slate-500">No active subscriptions yet.</p>
          ) : (
            <ul className="mt-2 divide-y divide-slate-100 dark:divide-slate-800">
              {topAccounts.map((c) => (
                <li key={c.name} className="flex items-center justify-between gap-3 py-2.5 first:pt-1">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{c.name}</p>
                    <span className={`mt-0.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold ${PLAN_PILL[c.plan] || PLAN_PILL.Starter}`}>
                      {c.plan}
                    </span>
                  </div>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-900 dark:text-white">
                    {money(c.amount)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
