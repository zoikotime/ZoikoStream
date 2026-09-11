import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FiMenu, FiLogOut, FiUser, FiChevronDown, FiPlus, FiRefreshCw,
} from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { CONSOLE, brand, brandButton, cx, focusRing } from "../../ui/tokens";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import ThemeToggle from "../../ui/ThemeToggle";
import HealthDot from "../admin/HealthDot";
import QuickActionsMenu from "../organization/QuickActionsMenu";

// Organization console topbar. One presentation for every /organization/* route: the
// primary "request live event" action, theme, and the account menu.
//
// What it deliberately no longer carries:
//   * the workspace and time-range selectors — no page read them any more, so they were
//     dead controls occupying the bar on every screen
//   * a permanent health verdict — a "Healthy" pill the reader cannot act on, duplicated on
//     every page; the real verdict and its history live on Support & Status
// The FAILURE case is kept: if /organization/console-state cannot be reached the shell has
// no identity, no badges and no verdict, and that is worth saying with a retry attached.
//
// `quickActions` swaps in the Quick Actions menu for the Profile page, which asks for it
// through useOrgScope — the control lives here, the decision belongs to the page.
//
// The notification bell that used to live here was hardcoded to "3" with no data source; it
// is gone rather than lying about unread items.
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

export default function Topbar({
  onMenuClick,
  state,
  unknown,
  onRetry,
  quickActions = false,
  collapsed = false,
  onToggleCollapse,
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside(() => setMenuOpen(false));

  const person = state?.user || { name: user?.full_name, email: user?.email };

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
      {/* Two controls, one job each, and never both at once.
          Under lg the rail is an overlay drawer, so the hamburger opens and closes it.
          At lg and up the rail is always present and this collapses it to an icon strip.
          The desktop control sits at the very start of the bar, against the boundary it
          acts on — it reads as a handle on the sidebar rather than a page action. */}
      <button onClick={onMenuClick} className={cx(iconBtn, "lg:hidden")} aria-label="Toggle menu">
        <FiMenu className="text-xl" />
      </button>

      <button
        type="button"
        onClick={onToggleCollapse}
        // Icon-only, so the accessible name has to carry the whole meaning — and it names
        // the ACTION, not the state, because that is what a click will do.
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className={cx(iconBtn, "hidden lg:grid")}
      >
        {collapsed ? (
          <PanelLeftOpen className="h-[18px] w-[18px]" aria-hidden="true" />
        ) : (
          <PanelLeftClose className="h-[18px] w-[18px]" aria-hidden="true" />
        )}
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-2.5">
        {/* Silent when things are fine, actionable when they are not.
            A permanent "Healthy" pill on every page is a readout nobody can act on, and it
            is what made the console feel like a monitoring tool; the real verdict, with its
            history, lives on Support & Status. What is NOT dropped is the failure case: when
            /organization/console-state cannot be reached the shell has no identity, no
            badges and no verdict, and the reader needs to know that and be able to retry.
            So: healthy renders nothing, unreachable renders a retry. */}
        {quickActions ? (
          // `md:inline-flex` on the pill it replaces hid the verdict on phones; the menu is
          // an ACTION, not a readout, so it stays available at every width.
          <QuickActionsMenu />
        ) : unknown ? (
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
        ) : null}

        {/* Primary action: schedule a live event for this organization. Reuses the existing
            Events page create flow rather than adding a second creation path. */}
        <button
          onClick={() => navigate("/organization/events?create=true")}
          className={cx(
            "group inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-semibold",
            // Same brand ramp as the console's primary Button and the rail's active row.
            brandButton,
            "active:scale-[0.98]",
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
            <span className={cx("grid h-7 w-7 place-items-center rounded-full text-[10px] font-semibold text-white shadow-sm shadow-violet-900/25", brand.chip)}>
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
                <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white", brand.chip)}>
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
