import { useNavigate } from "react-router-dom";
import { FiGrid, FiUsers, FiRadio, FiEye, FiPlus } from "react-icons/fi";
import DashboardCard from "../../components/Dashboard/DashboardCard";
import BarChartCard from "../../components/Dashboard/BarChartCard";
import RadialCard from "../../components/Dashboard/RadialCard";
import StorageCard from "../../components/Dashboard/StorageCard";
import { ORGS, ORG_STATUS } from "../../data/orgs";

// ponytail: mock platform stats — swap for a super-admin stats endpoint later.
const kpis = [
  { title: "Organizations", value: "24", icon: FiGrid, accent: "violet", delta: "9%", up: true },
  { title: "Total Users", value: "1,208", icon: FiUsers, accent: "blue", delta: "14%", up: true },
  { title: "Live Events", value: "3", icon: FiRadio, accent: "emerald", live: true },
  { title: "Total Viewers", value: "84,120", icon: FiEye, accent: "amber", delta: "5%", up: true },
];

const signups = [
  { label: "Jan", value: 12 },
  { label: "Feb", value: 18 },
  { label: "Mar", value: 15 },
  { label: "Apr", value: 22 },
  { label: "May", value: 19 },
  { label: "Jun", value: 28 },
];

const orgs = ORGS.slice(0, 4); // top few on the dashboard; full list lives on /admin/organizations

const th = "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400";
const td = "px-4 py-3 text-sm text-slate-600 dark:text-neutral-300";

export default function AdminDashboard() {
  const navigate = useNavigate();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
          Platform Overview 🛰️
        </h1>
        <p className="text-sm text-slate-500 dark:text-neutral-400">
          Everything happening across all organizations on ZoikoStream.
        </p>
      </div>

      {/* Platform KPIs */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((k) => (
          <DashboardCard key={k.title} {...k} />
        ))}
      </div>

      {/* Chart + gauge */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <BarChartCard title="New Organizations" subtitle="Sign-ups per month" data={signups} />
        </div>
        <RadialCard title="Platform Health" percent={98} label="uptime" footer="30-day average" color="#10b981" />
      </div>

      {/* Organizations table + storage */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm xl:col-span-2 dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-neutral-800">
            <h2 className="font-semibold text-slate-900 dark:text-white">Organizations</h2>
            <button
              onClick={() => navigate("/admin/organizations/create")}
              className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-violet-700"
            >
              <FiPlus /> Add Organization
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px]">
              <thead className="border-b border-slate-100 dark:border-neutral-800">
                <tr>
                  <th className={th}>Organization</th>
                  <th className={th}>Plan</th>
                  <th className={`${th} text-right`}>Users</th>
                  <th className={`${th} text-right`}>Events</th>
                  <th className={th}>Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-neutral-800">
                {orgs.map((o) => (
                  <tr key={o.name} className="hover:bg-slate-50 dark:hover:bg-neutral-800/50">
                    <td className={`${td} font-medium text-slate-800 dark:text-neutral-100`}>{o.name}</td>
                    <td className={td}>{o.plan}</td>
                    <td className={`${td} text-right`}>{o.users}</td>
                    <td className={`${td} text-right`}>{o.events}</td>
                    <td className={td}>
                      <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${ORG_STATUS[o.status]}`}>
                        {o.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-6">
          <StorageCard />
        </div>
      </div>
    </div>
  );
}
