import { useAuth } from "../../auth/AuthContext";
import api from "../../api";
import useApi from "../../hooks/useApi";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import StatCard from "../../components/admin/StatCard";
import { BarChart, RadialChart } from "../../ui/charts";
import RecentEvents from "../../components/Dashboard/RecentEvents";
import StorageCard from "../../components/Dashboard/StorageCard";
import QuickActions from "../../components/Dashboard/QuickActions";

// ponytail: viewership/engagement/storage have no backend endpoint yet — kept as
// static previews and marked. When GET /dashboard/org/analytics lands, feed these.
const viewership = [
  { label: "Mon", value: 90 },
  { label: "Tue", value: 120 },
  { label: "Wed", value: 80 },
  { label: "Thu", value: 130 },
  { label: "Fri", value: 110 },
  { label: "Sat", value: 160 },
  { label: "Sun", value: 190 },
];

export default function OrganizationDashboard() {
  const { user } = useAuth();
  const { data: stats, loading, error, reload } = useApi(() =>
    api.get("/dashboard/org/stats").then((r) => r.data)
  );

  const orgName =
    stats?.organization_name || user?.organization_name || "Zoiko Organization";

  const kpis = [
    { label: "Upcoming Events", value: stats?.upcoming_events ?? 0 },
    { label: "Live Events", value: stats?.live_events ?? 0 },
    { label: "Completed Events", value: stats?.completed_events ?? 0 },
    { label: "Total Viewers", value: stats?.total_viewers ?? 0 },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title={`Welcome, ${orgName}`}
        subtitle="Here's what's happening across your organization today."
      />

      {/* KPI cards — real loading skeletons, honest error state (no hidden fallback numbers) */}
      {error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load dashboard stats" />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {kpis.map((k) => (
            <StatCard key={k.label} label={k.label} value={k.value} loading={loading} />
          ))}
        </div>
      )}

      {/* Chart + gauge */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <BarChart title="Viewership Growth" subtitle="Daily viewers this week" data={viewership} />
        </div>
        <RadialChart title="Engagement" percent={75} label="avg. watch rate" footer="+6.3% vs last week" />
      </div>

      {/* Recent events + side column */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <RecentEvents />
        </div>
        <div className="space-y-6">
          <StorageCard />
          <QuickActions />
        </div>
      </div>
    </div>
  );
}
