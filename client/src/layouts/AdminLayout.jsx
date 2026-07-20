import { useState } from "react";
import { Outlet } from "react-router-dom";
import {
  FiHome,
  FiGrid,
  FiUsers,
  FiCalendar,
  FiBarChart2,
  FiCreditCard,
  FiSettings,
} from "react-icons/fi";
import Sidebar from "../components/Dashboard/Sidebar";
import Topbar from "../components/Dashboard/Topbar";

// Platform-wide nav for the super admin.
const nav = [
  { to: "/admin/dashboard", label: "Dashboard", icon: FiHome, end: true },
  { to: "/admin/organizations", label: "Organizations", icon: FiGrid },
  { to: "/admin/users", label: "Users", icon: FiUsers },
  { to: "/admin/events", label: "Events", icon: FiCalendar },
  { to: "/admin/analytics", label: "Analytics", icon: FiBarChart2 },
  { to: "/admin/billing", label: "Billing", icon: FiCreditCard },
  { to: "/admin/settings", label: "Settings", icon: FiSettings },
];

// Shell for all /admin/* pages (super admin only).
export default function AdminLayout() {
  const [open, setOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-800 dark:bg-neutral-950 dark:text-neutral-200">
      <Sidebar nav={nav} open={open} onClose={() => setOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar orgName="ZoikoStream" subtitle="Platform Admin" onMenuClick={() => setOpen((v) => !v)} />
        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
