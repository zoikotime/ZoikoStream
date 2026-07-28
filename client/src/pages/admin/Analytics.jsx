import { FiInfo } from "react-icons/fi";
import { StatCard, money, seriesDelta } from "../../components/admin";
import { AreaChart, LineChart } from "../../ui/charts";
import { SERIES } from "../../ui/tokens";
import api from "../../api";
import useApi from "../../hooks/useApi";

function useAnalyticsData() {
  return useApi(() => api.get("/admin/analytics").then((r) => r.data));
}

// Platform Analytics — every chart reads straight from GET /admin/analytics (real
// subscription/org/user history). Traffic and bandwidth aren't shown: the backend has no
// metering pipeline for them yet, and this page never substitutes a fabricated number.
export default function Analytics() {
  const { data, loading, error } = useAnalyticsData();

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load analytics. Try refreshing the page.
      </div>
    );
  }

  const revenue = data?.revenue || [];
  const organizations = data?.organizations || [];
  const users = data?.users || [];
  const revenueDelta = seriesDelta(revenue);
  const orgDelta = seriesDelta(organizations);
  const userDelta = seriesDelta(users);

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Analytics</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Revenue and growth trends across the platform</p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="MRR" value={loading ? 0 : money(data.mrr)} loading={loading} />
        <StatCard label="Streaming Hours" value={loading ? 0 : data.streaming_hours} decimals={1} loading={loading} />
        <StatCard label="New MRR (last month)" value={revenueDelta ? `${revenueDelta.up ? "+" : "-"}${revenueDelta.pct}%` : "—"} loading={loading} />
        <StatCard label="Org Growth (last month)" value={orgDelta ? `${orgDelta.up ? "+" : "-"}${orgDelta.pct}%` : "—"} loading={loading} />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <AreaChart
          title="Revenue Growth"
          subtitle="New MRR added per month"
          data={revenue}
          keys={[{ key: "value", name: "Revenue", color: SERIES.brand }]}
          suffix=""
          loading={loading}
          empty={!loading && revenue.length === 0}
        />
        <AreaChart
          title="Organization Growth"
          subtitle="New organizations per month"
          data={organizations}
          keys={[{ key: "value", name: "Organizations", color: SERIES.info }]}
          loading={loading}
          empty={!loading && organizations.length === 0}
        />
      </div>

      <LineChart
        title="User Growth"
        subtitle="New users per month"
        data={users}
        keys={[{ key: "value", name: "Users", color: SERIES.success }]}
        loading={loading}
        empty={!loading && users.length === 0}
      />

      {!loading && data?.note && (
        <div className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-5 py-3.5 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-300">
          <FiInfo className="mt-0.5 shrink-0 text-slate-400" />
          <span>{data.note}</span>
        </div>
      )}

      <p className="text-right text-xs text-slate-400">
        {userDelta ? `User growth ${userDelta.up ? "up" : "down"} ${userDelta.pct}% vs the prior month.` : ""}
      </p>
    </div>
  );
}
