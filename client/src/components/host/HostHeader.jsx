// client/src/components/host/HostHeader.jsx
// Studio top bar: brand, welcome, current event + status, and a live clock.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FiSun, FiMoon, FiBell, FiLogOut } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { useTheme } from "../../theme/ThemeContext";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";

export default function HostHeader({ event, live }) {
  const { user } = useAuth();
  const { theme, toggle } = useTheme();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const hostName = user?.full_name || "Host";
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const date = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      {/* Brand */}
      <Link to="/organization/dashboard">
        <Logo height="h-7">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Host Studio</span>
        </Logo>
      </Link>

      {/* Welcome + current event */}
      <div className="hidden min-w-0 border-l border-slate-200 pl-4 md:block dark:border-slate-700">
        <p className="text-sm font-semibold text-slate-900 dark:text-white">Welcome back, {hostName} 👋</p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">
          {event.name} · {event.session}
        </p>
      </div>

      {/* Event status */}
      {live ? <Badge status="error" live>LIVE</Badge> : <Badge status="warning" dot>Ready to go live</Badge>}

      {/* Right: live clock + actions */}
      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <div className="text-right leading-tight">
          <p className="text-sm font-semibold tabular-nums text-slate-900 dark:text-white">{time}</p>
          <p className="text-xs text-slate-500 dark:text-slate-400">{date}</p>
        </div>
        <button
          onClick={toggle}
          className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          aria-label="Toggle theme"
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>
        <button
          className="relative hidden text-slate-500 hover:text-slate-700 sm:block dark:text-slate-400 dark:hover:text-slate-200"
          aria-label="Notifications"
        >
          <FiBell className="text-xl" />
          <span className="absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-rose-500 text-[10px] font-semibold text-white">5</span>
        </button>
        <Link
          to="/organization/dashboard"
          className="hidden text-slate-400 hover:text-rose-500 sm:block"
          aria-label="Exit studio"
          title="Exit studio"
        >
          <FiLogOut className="text-xl" />
        </Link>
      </div>
    </header>
  );
}
