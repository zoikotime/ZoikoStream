import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  FiMenu, FiSearch, FiBell, FiPlus, FiGlobe, FiChevronDown, FiSun, FiMoon,
  FiUser, FiLogOut, FiCommand,
} from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { useTheme } from "../../theme/ThemeContext";
import { cx } from "../../ui/tokens";
import AccountModal from "../AccountModal";
import Icon from "./icons";
import HealthDot from "./HealthDot";
import { searchIndex, regionOptions, alerts, quickActions } from "../../data/platform";

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

// Close a dropdown when clicking outside of it.
function useClickOutside(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);
  return ref;
}

// Platform command bar: global search + region + quick create + notifications + theme + profile.
// Admin-only, so the shared org Topbar is left untouched.
export default function AdminTopbar({ onMenuClick }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();

  const [q, setQ] = useState("");
  const [open, setOpen] = useState(null); // "search" | "region" | "create" | "notif" | "profile"
  const [region, setRegion] = useState(regionOptions[0]);
  const [accountOpen, setAccountOpen] = useState(false);
  const searchRef = useRef(null);
  const menuRef = useClickOutside(() => setOpen(null));

  // ⌘K / Ctrl+K focuses the command bar (control-center convention).
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        setOpen("search");
      }
      if (e.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const results = q
    ? searchIndex.filter((r) => r.label.toLowerCase().includes(q.toLowerCase()))
    : searchIndex.slice(0, 6);

  const go = (to) => { setOpen(null); setQ(""); navigate(to); };
  const runAction = (a) => (a.to ? go(a.to) : (setOpen(null), toast(`${a.label} — coming soon`, { icon: "🛠️" })));

  const iconBtn = "grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100";

  return (
    <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white/80 px-3 backdrop-blur sm:px-5 dark:border-slate-800 dark:bg-slate-900/80" ref={menuRef}>
      <button onClick={onMenuClick} className={cx(iconBtn, "lg:hidden")} aria-label="Toggle menu">
        <FiMenu className="text-xl" />
      </button>

      {/* Global search / command bar */}
      <div className="relative min-w-0 flex-1 max-w-xl">
        <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen("search"); }}
          onFocus={() => setOpen("search")}
          placeholder="Search organizations, users, events…"
          className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2 pl-10 pr-16 text-sm text-slate-800 outline-none focus:border-violet-500 focus:ring-2 focus:ring-violet-100 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:focus:ring-violet-500/20"
        />
        <kbd className="pointer-events-none absolute right-3 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-400 sm:flex dark:border-slate-700 dark:bg-slate-900">
          <FiCommand className="text-[10px]" />K
        </kbd>

        {open === "search" && (
          <div className="absolute left-0 right-0 top-12 z-20 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
            <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              {q ? "Results" : "Jump to"}
            </p>
            {results.length === 0 && <p className="px-3 py-3 text-sm text-slate-400">No matches for “{q}”.</p>}
            {results.map((r) => (
              <button
                key={r.label + r.kind}
                onMouseDown={() => go(r.to)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-700/60"
              >
                <Icon name={r.icon} className="text-slate-400" />
                <span className="flex-1 truncate">{r.label}</span>
                <span className="text-[10px] uppercase tracking-wide text-slate-400">{r.kind}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        {/* Region selector */}
        <div className="relative hidden sm:block">
          <button
            onClick={() => setOpen(open === "region" ? null : "region")}
            className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <FiGlobe className="text-slate-400" />
            <span className="hidden max-w-[9rem] truncate lg:inline">{region.name}</span>
            <FiChevronDown className="text-slate-400" />
          </button>
          {open === "region" && (
            <div className="absolute right-0 top-11 z-20 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
              {regionOptions.map((r) => (
                <button
                  key={r.id}
                  onClick={() => { setRegion(r); setOpen(null); }}
                  className={cx(
                    "flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-slate-700/60",
                    r.id === region.id ? "font-semibold text-violet-700 dark:text-violet-300" : "text-slate-700 dark:text-slate-200"
                  )}
                >
                  {r.name}
                  {r.id === region.id && <span className="h-1.5 w-1.5 rounded-full bg-violet-500" />}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Quick create */}
        <div className="relative">
          <button
            onClick={() => setOpen(open === "create" ? null : "create")}
            className="flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-violet-700"
          >
            <FiPlus className="text-base" />
            <span className="hidden sm:inline">Create</span>
          </button>
          {open === "create" && (
            <div className="absolute right-0 top-11 z-20 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
              {quickActions.map((a) => (
                <button
                  key={a.label}
                  onClick={() => runAction(a)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-700/60"
                >
                  <Icon name={a.icon} className="text-slate-400" /> {a.label}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Notifications */}
        <div className="relative">
          <button
            onClick={() => setOpen(open === "notif" ? null : "notif")}
            className={cx(iconBtn, "relative")}
            aria-label="Notifications"
          >
            <FiBell className="text-xl" />
            <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full bg-rose-500 text-[10px] font-semibold text-white">
              {alerts.length}
            </span>
          </button>
          {open === "notif" && (
            <div className="absolute right-0 top-11 z-20 w-80 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-slate-700 dark:bg-slate-800">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5 dark:border-slate-700">
                <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">Alerts</span>
                <button onClick={() => go("/admin/status")} className="text-xs font-medium text-violet-600 dark:text-violet-400">View all</button>
              </div>
              <div className="max-h-80 divide-y divide-slate-100 overflow-y-auto dark:divide-slate-700">
                {alerts.map((a) => (
                  <div key={a.id} className="flex gap-3 px-4 py-3">
                    <HealthDot status={a.severity} label="" className="mt-1" />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{a.title}</p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">{a.detail}</p>
                      <p className="mt-0.5 text-[11px] text-slate-400">{a.when}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Theme toggle */}
        <button onClick={toggle} className={iconBtn} aria-label="Toggle theme" title={theme === "dark" ? "Switch to light" : "Switch to dark"}>
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>

        {/* Profile */}
        <div className="relative">
          <button
            onClick={() => setOpen(open === "profile" ? null : "profile")}
            className="flex items-center gap-2 rounded-full border border-slate-200 py-1 pl-1 pr-2 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
          >
            <span className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-xs font-semibold text-white">
              {initials(user?.full_name)}
            </span>
            <FiChevronDown className="hidden text-slate-400 sm:block" />
          </button>
          {open === "profile" && (
            <div className="absolute right-0 top-12 z-20 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
              <div className="border-b border-slate-100 px-4 py-2.5 dark:border-slate-700">
                <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{user?.full_name || "Super Admin"}</p>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{user?.email}</p>
              </div>
              <button
                onClick={() => { setOpen(null); setAccountOpen(true); }}
                className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-700/60"
              >
                <FiUser /> Profile
              </button>
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-500/10"
              >
                <FiLogOut /> Log out
              </button>
            </div>
          )}
        </div>
      </div>
      <AccountModal open={accountOpen} onClose={() => setAccountOpen(false)} />
    </header>
  );
}
