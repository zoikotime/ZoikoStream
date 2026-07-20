import { FiCalendar, FiRadio, FiCheckSquare, FiUsers } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import DashboardCard from "../../components/Dashboard/DashboardCard";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import RadialCard from "../../components/Dashboard/RadialCard";
import RecentEvents from "../../components/Dashboard/RecentEvents";
import StorageCard from "../../components/Dashboard/StorageCard";
import QuickActions from "../../components/Dashboard/QuickActions";

// ponytail: mock data — swap for a stats endpoint later.
const kpis = [
  { title: "Upcoming Events", value: "5", icon: FiCalendar, accent: "violet", delta: "12%", up: true },
  { title: "Live Events", value: "1", icon: FiRadio, accent: "emerald", live: true },
  { title: "Completed Events", value: "28", icon: FiCheckSquare, accent: "indigo", delta: "8%", up: true },
  { title: "Total Viewers", value: "12,530", icon: FiUsers, accent: "amber", delta: "3%", up: false },
];

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
  const orgName = user?.organization_name || "Zoiko Organization";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
          Welcome, {orgName} 👋
        </h1>
        <p className="text-sm text-slate-500 dark:text-neutral-400">
          Here's what's happening across your organization today.
        </p>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => (
          <DashboardCard key={k.title} {...k} />
        ))}
      </div>

      {/* Chart + gauge */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <BarChartCard
            title="Viewership Growth"
            subtitle="Daily viewers this week"
            data={viewership}
          />
        </div>
        <RadialCard title="Engagement" percent={75} label="avg. watch rate" footer="+6.3% vs last week" />
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
