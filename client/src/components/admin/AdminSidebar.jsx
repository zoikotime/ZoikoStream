import { NavLink, Link } from "react-router-dom";
import {
  FiX, FiHome, FiGrid, FiRadio, FiServer, FiGitBranch, FiUsers, FiKey,
  FiCreditCard, FiBarChart2, FiShield, FiFileText, FiLifeBuoy, FiSettings,
  FiActivity, FiFlag, FiPackage,
} from "react-icons/fi";
import Logo from "../../ui/Logo";
import HealthDot from "./HealthDot";
import { services, regions } from "../../data/platform";

// Platform control-center navigation, grouped like AWS / Supabase / Vercel.
// Dedicated to the admin shell so the org dashboard's shared Sidebar stays untouched.
const GROUPS = [
  { items: [{ to: "/admin/dashboard", label: "Dashboard", icon: FiHome, end: true }] },
  {
    label: "Platform",
    items: [
      { to: "/admin/organizations", label: "Organizations", icon: FiGrid },
      { to: "/admin/live-events", label: "Live Events", icon: FiRadio },
      { to: "/admin/infrastructure", label: "Media Infrastructure", icon: FiServer },
      { to: "/admin/developers", label: "Developers", icon: FiGitBranch },
    ],
  },
  {
    label: "Access",
    items: [
      { to: "/admin/users", label: "Users", icon: FiUsers },
      { to: "/admin/roles", label: "Roles & Permissions", icon: FiKey },
    ],
  },
  {
    label: "Business",
    items: [
      { to: "/admin/subscriptions", label: "Subscriptions", icon: FiCreditCard },
      { to: "/admin/analytics", label: "Analytics", icon: FiBarChart2 },
    ],
  },
  {
    label: "Trust & Safety",
    items: [
      { to: "/admin/security", label: "Security", icon: FiShield },
      { to: "/admin/audit", label: "Audit Logs", icon: FiFileText },
    ],
  },
  {
    label: "Operations",
    items: [
      { to: "/admin/status", label: "System Status", icon: FiActivity },
      { to: "/admin/feature-flags", label: "Feature Flags", icon: FiFlag },
      { to: "/admin/releases", label: "Release Center", icon: FiPackage },
      { to: "/admin/support", label: "Support", icon: FiLifeBuoy },
    ],
  },
  { label: "Settings", items: [{ to: "/admin/settings", label: "Platform Settings", icon: FiSettings }] },
];

const linkClass = ({ isActive }) =>
  `group flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition ${
    isActive
      ? "bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300"
      : "text-slate-500 hover:bg-slate-50 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800/60 dark:hover:text-slate-100"
  }`;

export default function AdminSidebar({ open, onClose }) {
  // Derive an overall status from mock infra data (drives the footer widget).
  const issues = [...services, ...regions].filter((s) => s.status !== "ok").length;
  const overall = issues === 0 ? "ok" : issues > 2 ? "down" : "warn";

  return (
    <>
      {open && <div className="fixed inset-0 z-20 bg-black/40 lg:hidden" onClick={onClose} />}

      <aside
        className={`fixed inset-y-0 left-0 z-30 flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white transition-transform lg:static lg:translate-x-0 dark:border-slate-800 dark:bg-slate-900 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {/* Brand + role tag */}
        <div className="flex h-16 items-center justify-between px-5">
          <div className="flex min-w-0 items-center gap-2">
            <Logo height="h-7" />
            <span className="rounded-md bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
              Super Admin
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 lg:hidden dark:hover:text-slate-200"
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        {/* Grouped nav */}
        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
          {GROUPS.map((group, gi) => (
            <div key={group.label || gi} className="space-y-1">
              {group.label && (
                <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  {group.label}
                </p>
              )}
              {group.items.map(({ to, label, icon: Icon, end }) => (
                <NavLink key={to} to={to} end={end} onClick={onClose} className={linkClass}>
                  <Icon className="text-lg shrink-0" />
                  <span className="truncate">{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* System-status footer widget */}
        <Link
          to="/admin/status"
          className="m-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 transition hover:border-violet-300 dark:border-slate-800 dark:bg-slate-800/40 dark:hover:border-violet-500/40"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">System Status</span>
            <HealthDot status={overall} pulse label="" />
          </div>
          <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
            {issues === 0 ? "All systems operational" : `${issues} ${issues === 1 ? "issue" : "issues"} detected`}
          </p>
        </Link>
      </aside>
    </>
  );
}
