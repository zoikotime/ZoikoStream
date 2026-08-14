import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FiMenu, FiLogOut, FiUser, FiChevronDown, FiPlus, FiClock, FiRefreshCw,
} from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import Dropdown from "../../ui/Dropdown";
import ThemeToggle from "../../ui/ThemeToggle";
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
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside(() => setMenuOpen(false));

  const person = state?.user || { name: user?.full_name, email: user?.email };
  const verdict = unknown ? UNKNOWN : VERDICT[state?.health?.status] || UNKNOWN;
  const workspaces = state?.workspaces || [];
  const single = workspaces.length < 2;
  const orgName = state?.organization?.name || "Organization";

  // Workspace options carry the org identity in the trigger, so the bar answers "which
  // organization, which workspace" without a second control. Options come from
  // /organization/console-state — nothing here is a hardcoded workspace.
  // `dot` is Dropdown's leading-node slot — an element, not a component type, so it does
  // not remount the avatar on every render the way an inline icon component would.
  const orgAvatar = (
    <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-gradient-to-br from-violet-600 to-indigo-700 text-[9px] font-bold text-white">
      {initials(orgName)}
    </span>
  );
  const workspaceOptions = [
    { value: "", label: "All workspaces", hint: orgName, dot: orgAvatar },
    ...workspaces.map((w) => ({
      value: w.slug,
      label: w.label,
      hint: w.name || orgName,
      dot: orgAvatar,
    })),
  ];

  const iconBtn = cx(
    "grid h-9 w-9 place-items-center rounded-lg transition-colors duration-150 motion-reduce:transition-none",
    CONSOLE.muted,
    "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.06] dark:hover:text-white",
    focusRing
  );

  return (
    <header
      ref={menuRef}
      className={cx(
        "sticky top-0 z-20 flex h-16 shrink-0 items-center gap-2 border-b px-3 backdrop-blur-xl sm:gap-3 sm:px-5",
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
            <Dropdown
              label="Workspace"
              className="hidden w-[13rem] sm:block"
              width="w-[15rem]"
              value={filters.workspace || ""}
              onChange={(v) => onFilters({ ...filters, workspace: v || null })}
              options={workspaceOptions}
              title={single ? "This organization has a single workspace" : undefined}
            />

            <Dropdown
              label="Time range"
              className="w-[10.5rem]"
              width="w-[10.5rem]"
              value={filters.range}
              onChange={(v) => onFilters({ ...filters, range: v })}
              options={RANGES.map(([value, label]) => ({ value, label }))}
              icon={FiClock}
            />
          </>
        )}

        {/* Health verdict — real, from /organization/console-state. When that call failed it
            becomes a retry, never a reassuring default. The pill pulses only while the
            platform is actually healthy or actually down: a static amber reads as "look at
            me later", a pulsing one as "look now". */}
        {unknown ? (
          <button
            onClick={onRetry}
            className={cx(
              "hidden items-center gap-1.5 rounded-full border px-2.5 py-1.5 md:inline-flex",
              "border-slate-200 bg-slate-50 dark:border-white/10 dark:bg-white/[0.04]",
              "transition-colors duration-150 hover:bg-slate-100 dark:hover:bg-white/[0.08]",
              "motion-reduce:transition-none",
              focusRing
            )}
            title="Couldn’t reach the API — click to retry"
          >
            <HealthDot status={UNKNOWN.status}>{UNKNOWN.label}</HealthDot>
            <FiRefreshCw className={cx("text-[12px]", CONSOLE.faint)} aria-hidden="true" />
          </button>
        ) : (
          <span
            className={cx(
              "hidden items-center rounded-full border px-2.5 py-1.5 md:inline-flex",
              "border-slate-200 bg-slate-50 dark:border-white/10 dark:bg-white/[0.04]"
            )}
            title={`Platform status: ${verdict.label}`}
          >
            <HealthDot
              status={verdict.status}
              pulse={verdict.status === "ok" || verdict.status === "down"}
            >
              {verdict.label}
            </HealthDot>
          </span>
        )}

        {/* Primary action: schedule a live event for this organization. Reuses the existing
            Events page create flow rather than adding a second creation path. */}
        <button
          onClick={() => navigate("/organization/events?create=true")}
          className={cx(
            "group inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-semibold text-white",
            "bg-gradient-to-r from-violet-600 to-indigo-600",
            "shadow-sm shadow-violet-600/25 hover:shadow-md hover:shadow-violet-600/35",
            "hover:from-violet-500 hover:to-indigo-500 active:scale-[0.98]",
            "transition-[background-image,box-shadow,transform] duration-150 ease-out",
            "motion-reduce:transition-none motion-reduce:active:scale-100",
            focusRing
          )}
        >
          <FiPlus
            className="text-[14px] transition-transform duration-150 group-hover:rotate-90 motion-reduce:transition-none motion-reduce:group-hover:rotate-0"
            aria-hidden="true"
          />
          <span className="hidden sm:inline">Request live event</span>
        </button>

        <ThemeToggle />

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className={cx(
              "flex items-center gap-2 rounded-full border py-1 pl-1 pr-2",
              "transition-colors duration-150 motion-reduce:transition-none",
              "border-slate-200 hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/[0.05]",
              menuOpen && "bg-slate-50 dark:bg-white/[0.05]",
              focusRing
            )}
            aria-label="Account menu"
          >
            <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[10px] font-semibold text-white shadow-sm shadow-violet-900/25">
              {initials(person.name)}
            </span>
            <FiChevronDown
              className={cx(
                "hidden text-[13px] transition-transform duration-150 sm:block motion-reduce:transition-none",
                CONSOLE.faint,
                menuOpen && "rotate-180"
              )}
              aria-hidden="true"
            />
          </button>
          {menuOpen && (
            <div
              role="menu"
              className={cx(
                "zk-pop-in absolute right-0 top-12 z-30 w-60 overflow-hidden rounded-xl border p-1 shadow-xl",
                "border-slate-200 bg-white shadow-slate-900/10",
                "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60"
              )}
            >
              <div className="flex items-center gap-2.5 px-3 py-2.5">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-semibold text-white">
                  {initials(person.name)}
                </span>
                <div className="min-w-0">
                  <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                    {person.name || "Member"}
                  </p>
                  <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
                    {person.role_label || person.email}
                  </p>
                </div>
              </div>
              <div className={cx("my-1 border-t", CONSOLE.divider)} />
              <button
                role="menuitem"
                onClick={() => { setMenuOpen(false); navigate("/organization/profile"); }}
                className={cx(
                  "flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px]",
                  "transition-colors duration-150 motion-reduce:transition-none",
                  CONSOLE.body,
                  "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.07] dark:hover:text-white",
                  focusRing
                )}
              >
                <FiUser className="shrink-0 text-[15px]" aria-hidden="true" />
                Organization profile
              </button>
              <button
                role="menuitem"
                onClick={logout}
                className={cx(
                  "flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] font-medium",
                  "text-rose-600 transition-colors duration-150 motion-reduce:transition-none",
                  "hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10",
                  focusRing
                )}
              >
                <FiLogOut className="shrink-0 text-[15px]" aria-hidden="true" />
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
