import { useState } from "react";
import { FiMenu, FiBell, FiChevronDown, FiLogOut, FiUser, FiSun, FiMoon } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { useTheme } from "../../theme/ThemeContext";

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

export default function Topbar({ orgName, subtitle = "Organization", onMenuClick }) {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="sticky top-0 z-10 flex h-16 shrink-0 items-center gap-4 border-b border-slate-200 bg-white px-4 sm:px-6 dark:border-neutral-800 dark:bg-neutral-900">
      <button
        onClick={onMenuClick}
        className="text-slate-500 hover:text-slate-700 lg:hidden dark:text-neutral-400 dark:hover:text-neutral-200"
        aria-label="Toggle menu"
      >
        <FiMenu className="text-xl" />
      </button>

      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">{orgName}</p>
        <p className="hidden text-xs text-slate-500 sm:block dark:text-neutral-400">{subtitle}</p>
      </div>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {/* Theme toggle */}
        <button
          onClick={toggle}
          className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
          aria-label="Toggle theme"
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>

        <button
          className="relative text-slate-500 hover:text-slate-700 dark:text-neutral-400 dark:hover:text-neutral-200"
          aria-label="Notifications"
        >
          <FiBell className="text-xl" />
          <span className="absolute -right-1.5 -top-1.5 grid h-4 w-4 place-items-center rounded-full bg-red-500 text-[10px] font-semibold text-white">
            3
          </span>
        </button>

        {/* Profile + logout menu */}
        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-2.5 rounded-full border border-slate-200 py-1 pl-1 pr-2.5 hover:bg-slate-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            <span className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-xs font-semibold text-white">
              {initials(user?.full_name)}
            </span>
            <span className="hidden text-left leading-tight sm:block">
              <span className="block text-sm font-medium text-slate-800 dark:text-neutral-100">
                {user?.full_name}
              </span>
              <span className="block text-xs capitalize text-slate-500 dark:text-neutral-400">
                {user?.role?.replace("_", " ")}
              </span>
            </span>
            <FiChevronDown className="hidden text-slate-400 sm:block" />
          </button>

          {menuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-0 z-20 mt-2 w-52 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-neutral-700 dark:bg-neutral-800">
                <div className="border-b border-slate-100 px-4 py-2 dark:border-neutral-700">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-neutral-100">
                    {user?.full_name}
                  </p>
                  <p className="truncate text-xs text-slate-500 dark:text-neutral-400">{user?.email}</p>
                </div>
                <button className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-slate-600 hover:bg-slate-50 dark:text-neutral-300 dark:hover:bg-neutral-700">
                  <FiUser /> Profile
                </button>
                <button
                  onClick={logout}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-500/10"
                >
                  <FiLogOut /> Log out
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
