// client/src/components/moderation/ModeratorHeader.jsx
// Header: brand, event name, live status, and the current viewer count.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FiUsers, FiClock, FiSun, FiMoon, FiLogOut } from "react-icons/fi";
import { useTheme } from "../../theme/ThemeContext";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";

// Elapsed time since the event went live, as H:MM:SS (or MM:SS under an hour).
const fmtElapsed = (s) => {
  const pad = (n) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  const m = pad(Math.floor((s % 3600) / 60));
  return `${h > 0 ? `${h}:` : ""}${m}:${pad(s % 60)}`;
};

export default function ModeratorHeader({ event, viewers }) {
  const { theme, toggle } = useTheme();
  const live = event.status === "Live";

  // Tick the event timer forward once a second while the event is live.
  const [elapsed, setElapsed] = useState(event.startedAgo ?? 0);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [live]);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      <Link to="/organization/dashboard">
        <Logo height="h-7">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Moderator Console</span>
        </Logo>
      </Link>

      <div className="min-w-0 dark:border-slate-700 md:border-l md:border-slate-200 md:pl-4">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">{event.name}</p>
        <p className="hidden truncate text-xs text-slate-500 md:block dark:text-slate-400">Hosted by {event.host}</p>
      </div>

      {live ? <Badge status="error" live>LIVE</Badge> : <Badge status="neutral">Offline</Badge>}

      <div className="ml-auto flex items-center gap-2 sm:gap-4">
        <div className="flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 dark:bg-slate-800" title="Event timer">
          <FiClock className="text-slate-500 dark:text-slate-400" />
          <span className="text-sm font-semibold tabular-nums text-slate-900 dark:text-white">{fmtElapsed(elapsed)}</span>
        </div>
        <div className="flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 dark:bg-slate-800">
          <FiUsers className="text-slate-500 dark:text-slate-400" />
          <span className="text-sm font-semibold tabular-nums text-slate-900 dark:text-white">{viewers.toLocaleString()}</span>
          <span className="hidden text-xs text-slate-500 sm:inline dark:text-slate-400">viewers</span>
        </div>
        <button
          onClick={toggle}
          className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          aria-label="Toggle theme"
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>
        <Link to="/organization/dashboard" className="hidden text-slate-400 hover:text-rose-500 sm:block" aria-label="Exit console" title="Exit console">
          <FiLogOut className="text-xl" />
        </Link>
      </div>
    </header>
  );
}
