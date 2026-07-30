import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiMenu, FiSun, FiMoon, FiLogOut, FiUser, FiChevronDown, FiPlus } from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { useTheme } from "../../theme/ThemeContext";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import { Select } from "../../ui/forms";
import HealthDot from "../admin/HealthDot";

// Organization console topbar: workspace + range scoping, the primary "request live event"
// action, then health verdict, theme and account.
//
// `filters`/`onFilters` are optional — only the Overview page scopes by window, so other org
// pages render the bar without them rather than showing dead controls.
//
// The notification bell that used to live here was hardcoded to "3" with no data source; it
// is gone rather than lying about unread items.
const RANGES = [
  ["1h", "Last hour"],
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
];

const VERDICT = {
  ok: { status: "ok", label: "Healthy" },
  warn: { status: "warn", label: "Degraded" },
  down: { status: "down", label: "Disrupted" },
  not_configured: { status: "neutral", label: "Not configured" },
};
const UNKNOWN = { status: "neutral", label: "Status unavailable" };

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

function useClickOutside(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);
  return ref;
}

export default function Topbar({ onMenuClick, state, unknown, onRetry, filters, onFilters }) {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside(() => setMenuOpen(false));

  const person = state?.user || { name: user?.full_name, email: user?.email };
  const verdict = unknown ? UNKNOWN : VERDICT[state?.health?.status] || UNKNOWN;
  const workspaces = state?.workspaces || [];
  const single = workspaces.length < 2;

  const iconBtn = cx(
    "grid h-9 w-9 place-items-center rounded-lg transition",
    CONSOLE.muted,
    "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.06] dark:hover:text-white"
  );

  return (
    <header
      ref={menuRef}
      className={cx(
        "sticky top-0 z-20 flex h-16 shrink-0 items-center gap-2 border-b px-3 backdrop-blur sm:gap-3 sm:px-5",
        CONSOLE.bar
      )}
    >
      <button onClick={onMenuClick} className={cx(iconBtn, "lg:hidden")} aria-label="Toggle menu">
        <FiMenu className="text-xl" />
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-2.5">
        {/* Scope controls — rendered only where the page actually reads them. */}
        {filters && onFilters && (
          <>
            <Select
              variant="console"
              aria-label="Workspace"
              value={filters.workspace || ""}
              onChange={(e) => onFilters({ ...filters, workspace: e.target.value || null })}
              className="hidden w-[10rem] sm:block"
              title={single ? "This organization has a single workspace" : undefined}
            >
              <option value="">All workspaces</option>
              {workspaces.map((w) => (
                <option key={w.slug} value={w.slug}>{w.label}</option>
              ))}
            </Select>

            <Select
              variant="console"
              aria-label="Time range"
              value={filters.range}
              onChange={(e) => onFilters({ ...filters, range: e.target.value })}
              className="w-[9.5rem]"
            >
              {RANGES.map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </Select>
          </>
        )}

        {/* Health verdict — real, from /organization/console-state. When that call failed it
            becomes a retry, never a reassuring default. */}
        {unknown ? (
          <button onClick={onRetry} className="hidden md:block" title="Couldn’t reach the API — click to retry">
            <HealthDot status={UNKNOWN.status}>{UNKNOWN.label}</HealthDot>
          </button>
        ) : (
          <span className="hidden md:block">
            <HealthDot status={verdict.status} pulse={verdict.status === "down"}>
              {verdict.label}
            </HealthDot>
          </span>
        )}

        {/* Primary action: schedule a live event for this organization. Reuses the existing
            Events page create flow rather than adding a second creation path. */}
        <button
          onClick={() => navigate("/organization/events?create=true")}
          className={cx(
            "inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-[12px] font-semibold text-white",
            "transition hover:bg-violet-700 active:bg-violet-800",
            focusRing
          )}
        >
          <FiPlus className="text-[14px]" />
          <span className="hidden sm:inline">Request live event</span>
        </button>

        <button
          onClick={toggle}
          className={iconBtn}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <FiSun className="text-[17px]" /> : <FiMoon className="text-[17px]" />}
        </button>

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className={cx(
              "flex items-center gap-2 rounded-full border py-1 pl-1 pr-2 transition",
              "border-slate-200 hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/[0.05]"
            )}
            aria-label="Account menu"
          >
            <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[10px] font-semibold text-white">
              {initials(person.name)}
            </span>
            <FiChevronDown className={cx("hidden text-[13px] sm:block", CONSOLE.faint)} />
          </button>
          {menuOpen && (
            <div
              className={cx(
                "absolute right-0 top-12 z-30 w-56 overflow-hidden rounded-xl border py-1 shadow-xl",
                "border-slate-200 bg-white dark:border-white/10 dark:bg-neutral-950"
              )}
            >
              <div className={cx("border-b px-4 py-2.5", CONSOLE.divider)}>
                <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                  {person.name || "Member"}
                </p>
                <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{person.email}</p>
              </div>
              <button
                onClick={() => { setMenuOpen(false); navigate("/organization/profile"); }}
                className={cx("flex w-full items-center gap-2.5 px-4 py-2.5 text-[13px]", CONSOLE.body,
                  "hover:bg-slate-50 dark:hover:bg-white/[0.05]")}
              >
                <FiUser /> Organization profile
              </button>
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-4 py-2.5 text-[13px] text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
              >
                <FiLogOut /> Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
