import { useEffect, useState } from "react";
import { FiCalendar, FiRadio, FiCheckSquare, FiUsers } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import api from "../../api";
import DashboardCard from "../../components/Dashboard/DashboardCard";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import RadialCard from "../../components/Dashboard/RadialCard";
import RecentEvents from "../../components/Dashboard/RecentEvents";
import StorageCard from "../../components/Dashboard/StorageCard";
import QuickActions from "../../components/Dashboard/QuickActions";

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
  const [stats, setStats] = useState(null);
  const orgName = user?.organization_name || "Zoiko Organization";

  useEffect(() => {
    // Fetch organization-specific stats
    api
      .get("/dashboard/org/stats")
      .then((res) => setStats(res.data))
      .catch((err) => console.error("Failed to fetch org stats:", err));
  }, []);

  const kpis = [
    { title: "Upcoming Events", value: String(stats?.upcoming_events || "5"), icon: FiCalendar, accent: "violet", delta: "12%", up: true },
    { title: "Live Events", value: String(stats?.live_events || "1"), icon: FiRadio, accent: "emerald", live: true },
    { title: "Completed Events", value: String(stats?.completed_events || "28"), icon: FiCheckSquare, accent: "indigo", delta: "8%", up: true },
    { title: "Total Viewers", value: String(stats?.total_viewers || "12,530"), icon: FiUsers, accent: "amber", delta: "3%", up: false },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
          Welcome, {orgName} 👋
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
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
