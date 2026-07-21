import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiGrid, FiUsers, FiRadio, FiEye, FiPlus, FiArrowRight, FiTrendingUp } from "react-icons/fi";
import api from "../../api";
import DashboardCard from "../../components/Dashboard/DashboardCard";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import RadialCard from "../../components/Dashboard/RadialCard";

export default function AdminDashboard() {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [orgs, setOrgs] = useState([]);

  useEffect(() => {
    Promise.all([
      api.get("/dashboard/platform/stats"),
      api.get("/dashboard/platform/organizations?limit=5"),
    ])
      .then(([statsRes, orgsRes]) => {
        setStats(statsRes.data);
        setOrgs(orgsRes.data);
      })
      .catch(console.error);
  }, []);

  const kpis = [
    { title: "Organizations", value: stats?.organizations ?? 0, icon: FiGrid, accent: "violet" },
    { title: "Total Users", value: stats?.total_users ?? 0, icon: FiUsers, accent: "blue" },
    { title: "Live Events", value: stats?.live_events ?? 0, icon: FiRadio, accent: "emerald", live: true },
    { title: "Total Viewers", value: stats?.total_viewers ?? 0, icon: FiEye, accent: "amber" },
  ];

  const signups = [
    { label: "Mon", value: 45 },
    { label: "Tue", value: 52 },
    { label: "Wed", value: 48 },
    { label: "Thu", value: 61 },
    { label: "Fri", value: 55 },
    { label: "Sat", value: 70 },
    { label: "Sun", value: 64 },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-slate-900 dark:text-white">Platform Overview</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Real-time insights across all organizations</p>
        </div>
        <button
          onClick={() => navigate("/admin/organizations")}
          className="flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white hover:bg-violet-700 transition"
        >
          <FiPlus /> Create Organization
        </button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => (
          <DashboardCard key={k.title} {...k} />
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <BarChartCard title="Sign-ups Trend" subtitle="Weekly organization registrations" data={signups} />
        </div>
        <RadialCard title="Platform Health" percent={98} label="availability" footer="Last 30 days" color="#8b5cf6" />
      </div>

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="border-b border-slate-100 px-6 py-4 dark:border-slate-800">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-slate-900 dark:text-white">
            <FiTrendingUp className="text-violet-600" /> Recent Organizations
          </h2>
        </div>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {orgs.map((org) => (
            <div
              key={org.id}
              onClick={() => navigate(`/admin/organizations/${org.id}`)}
              className="flex items-center justify-between px-6 py-4 hover:bg-slate-50 cursor-pointer transition dark:hover:bg-slate-800/50"
            >
              <div>
                <p className="font-medium text-slate-900 dark:text-white">{org.name}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">{org.users} members</p>
              </div>
              <FiArrowRight className="text-slate-400 dark:text-slate-600" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
