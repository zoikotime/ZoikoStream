import { useState } from "react";
import { Outlet } from "react-router-dom";
import {
  FiHome,
  FiCalendar,
  FiPlayCircle,
  FiBarChart2,
  FiUsers,
  FiCreditCard,
  FiSettings,
} from "react-icons/fi";
import Sidebar from "../components/Dashboard/Sidebar";
import Topbar from "../components/Dashboard/Topbar";
import { useAuth } from "../auth/AuthContext";

const nav = [
  { to: "/organization/dashboard", label: "Dashboard", icon: FiHome, end: true },
  { to: "/organization/events", label: "Events", icon: FiCalendar },
  { to: "/organization/recordings", label: "Recordings", icon: FiPlayCircle },
  { to: "/organization/analytics", label: "Analytics", icon: FiBarChart2 },
  { to: "/organization/users", label: "Users", icon: FiUsers },
  { to: "/organization/billing", label: "Billing", icon: FiCreditCard },
  { to: "/organization/settings", label: "Settings", icon: FiSettings },
];

// Shell for all /organization/* pages: sidebar + topbar + routed content.
export default function OrganizationLayout() {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();

  // ponytail: org name isn't on the user payload yet — show a sensible default until it is.
  const orgName = user?.organization_name || "Zoiko Organization";

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      <Sidebar nav={nav} open={open} onClose={() => setOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar orgName={orgName} subtitle="Organization" onMenuClick={() => setOpen((v) => !v)} />
        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
