import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  FiHome,
  FiCalendar,
  FiMic,
  FiUsers,
  FiPlayCircle,
  FiBarChart2,
  FiUser,
  FiMail,
  FiCreditCard,
  FiSettings,
  FiBell,
  FiHelpCircle,
  FiChevronDown,
  FiPlus,
  FiMenu,
  FiLogOut,
} from "react-icons/fi";
import { useAuth } from "../auth/AuthContext";
import Logo from "../ui/Logo";

// First letter(s) of a name for the avatar.
const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

const nav = [
  { to: "/dashboard", label: "Dashboard", icon: FiHome, end: true },
  { to: "/events", label: "Events", icon: FiCalendar },
  { to: "/speakers", label: "Speakers", icon: FiMic },
  { to: "/viewers", label: "Viewers", icon: FiUsers },
  { to: "/recordings", label: "Recordings", icon: FiPlayCircle },
  { to: "/analytics", label: "Analytics", icon: FiBarChart2 },
  { to: "/users", label: "Users", icon: FiUser },
  { to: "/invitations", label: "Invitations", icon: FiMail },
  { to: "/billing", label: "Billing", icon: FiCreditCard },
  { to: "/settings", label: "Settings", icon: FiSettings },
];

export default function MainLayout() {
  const [open, setOpen] = useState(false);
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-800">
      <aside
        className={`fixed inset-y-0 left-0 z-30 w-64 shrink-0 flex flex-col border-r border-slate-200 bg-white transition-transform lg:static lg:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="h-16 flex items-center px-6">
          <Logo textClass="text-lg text-slate-900" />
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                  isActive
                    ? "bg-violet-50 text-violet-700"
                    : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
                }`
              }
            >
              <Icon className="text-lg" />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="p-3 space-y-3">
          <div className="rounded-xl border border-slate-200 p-4">
            <p className="text-sm font-semibold text-slate-800">Storage Usage</p>
            <p className="mt-1 text-xs text-slate-500">
              <span className="font-medium text-slate-700">245 GB</span> / 1 TB Used
            </p>
            <div className="mt-2 h-1.5 w-full rounded-full bg-slate-100">
              <div className="h-full w-[24%] rounded-full bg-gradient-to-r from-violet-500 to-indigo-600" />
            </div>
            <button className="mt-3 w-full rounded-lg border border-slate-200 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Upgrade Plan
            </button>
          </div>

          <div className="flex items-center gap-3 rounded-xl px-2 py-2">
            <div className="h-9 w-9 rounded-full bg-gradient-to-br from-emerald-600 to-teal-700 text-white grid place-items-center text-sm font-semibold">
              {initials(user?.full_name)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-800">{user?.full_name}</p>
              <p className="truncate text-xs text-slate-500">{user?.email}</p>
            </div>
            <button
              onClick={logout}
              className="text-slate-400 hover:text-red-500"
              aria-label="Log out"
              title="Log out"
            >
              <FiLogOut />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div className="fixed inset-0 z-20 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <header className="sticky top-0 z-10 h-16 shrink-0 border-b border-slate-200 bg-white flex items-center gap-4 px-4 sm:px-6">
          <button
            className="text-slate-500 hover:text-slate-700"
            onClick={() => setOpen((v) => !v)}
            aria-label="Toggle menu"
          >
            <FiMenu className="text-xl" />
          </button>

          <div className="ml-auto flex items-center gap-3 sm:gap-4">
            <button className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-violet-600 to-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:opacity-90">
              <FiPlus /> Create Event
            </button>

            <button className="relative text-slate-500 hover:text-slate-700" aria-label="Notifications">
              <FiBell className="text-xl" />
              <span className="absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-red-500 text-[10px] font-semibold text-white">
                6
              </span>
            </button>

            <button className="hidden sm:block text-slate-500 hover:text-slate-700" aria-label="Help">
              <FiHelpCircle className="text-xl" />
            </button>

            <button className="flex items-center gap-2.5 rounded-full border border-slate-200 py-1 pl-1 pr-2.5 hover:bg-slate-50">
              <span className="h-8 w-8 rounded-full bg-slate-900 text-white grid place-items-center text-xs font-semibold">
                ZI
              </span>
              <span className="hidden sm:block text-left leading-tight">
                <span className="block text-sm font-medium text-slate-800">Zoiko Industries</span>
                <span className="block text-xs text-slate-500">Organization Admin</span>
              </span>
              <FiChevronDown className="hidden sm:block text-slate-400" />
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
