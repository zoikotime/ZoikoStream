import { useMemo } from "react";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { Reveal } from "../../ui/motion";
import Skeleton from "../../ui/Skeleton";
import PlatformStatus from "../../components/admin/sections/PlatformStatus";
import CriticalAlerts from "../../components/admin/sections/CriticalAlerts";
import OrgsAttention from "../../components/admin/sections/OrgsAttention";
import RevenueGrowth from "../../components/admin/sections/RevenueGrowth";
import LiveEventActivity from "../../components/admin/sections/LiveEventActivity";
import PlatformActivity from "../../components/admin/sections/PlatformActivity";
import Infrastructure from "../../components/admin/sections/Infrastructure";
import AuditTimeline from "../../components/admin/sections/AuditTimeline";
import QuickActions from "../../components/admin/sections/QuickActions";

// Boot skeleton — mirrors the real layout (header + stat strip + stacked panels).
function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1440px] space-y-8">
      <div className="space-y-3">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="line" className="w-96" />
      </div>
      <Skeleton variant="block" className="h-24" />
      <Skeleton variant="block" className="h-64" />
      <Skeleton variant="block" className="h-56" />
    </div>
  );
}

// One fetch for every section on the page — six real /admin/* endpoints, one loading
// state. Each section takes its slice as props; none of them import mock data anymore.
function useDashboardData() {
  return useApi(() =>
    Promise.all([
      api.get("/admin/dashboard").then((r) => r.data),
      api.get("/admin/platform-health").then((r) => r.data),
      api.get("/admin/audit-logs", { params: { page_size: 6 } }).then((r) => r.data.items),
      api.get("/admin/organizations", { params: { page_size: 100 } }).then((r) => r.data.items),
      api.get("/admin/subscriptions", { params: { status: "active", page_size: 100 } }).then((r) => r.data.items),
      api.get("/admin/live-events").then((r) => r.data),
    ]).then(([dashboard, health, auditLogs, organizations, subscriptions, liveEvents]) => ({
      dashboard, health, auditLogs, organizations, subscriptions, liveEvents,
    }))
  );
}

// Platform Operations Center. Sections follow a top-down attention hierarchy:
// status → alerts → orgs at risk → revenue → live events → what just happened →
// infrastructure → quick actions. Every section is fed from real /admin/* data —
// where the backend hasn't integrated a source yet (concurrent viewers, per-stream
// bitrate) the section shows "—", never a fabricated number.
export default function AdminDashboard() {
  const { data, loading, error } = useDashboardData();

  const topAccounts = useMemo(() => {
    const subs = data?.subscriptions || [];
    return [...subs]
      .filter((s) => s.price_monthly != null)
      .sort((a, b) => b.price_monthly - a.price_monthly)
      .slice(0, 4)
      .map((s) => ({ name: s.organization_name || "—", plan: s.plan || "—", amount: s.price_monthly }));
  }, [data]);

  if (loading) return <DashboardSkeleton />;

  if (error || !data) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load the platform dashboard. Try refreshing the page.
      </div>
    );
  }

  const { dashboard, health, auditLogs, organizations, liveEvents } = data;

  return (
    <div className="mx-auto max-w-[1440px] space-y-8">
      <PlatformStatus summary={dashboard.summary} organizationGrowth={dashboard.organization_growth} userGrowth={dashboard.user_growth} />
      <CriticalAlerts alerts={dashboard.recent_alerts} />

      <Reveal><OrgsAttention organizations={organizations} /></Reveal>
      <Reveal>
        <RevenueGrowth mrr={dashboard.summary.monthly_revenue} revenueGrowth={dashboard.revenue_growth} topAccounts={topAccounts} />
      </Reveal>
      <Reveal><LiveEventActivity events={liveEvents} /></Reveal>

      {/* "What just happened" band — signups/orgs feed + audit history side by side. */}
      <div className="grid gap-6 xl:grid-cols-2">
        <Reveal>
          <PlatformActivity latestOrganizations={dashboard.latest_organizations} latestSignups={dashboard.latest_signups} />
        </Reveal>
        <Reveal><AuditTimeline logs={auditLogs} /></Reveal>
      </div>

      <Reveal><Infrastructure health={health} /></Reveal>
      <Reveal><QuickActions /></Reveal>
    </div>
  );
}
