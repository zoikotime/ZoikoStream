import { useEffect, useRef, useState } from "react";
import { Link, useMatch, useNavigate } from "react-router-dom";
import {
  FiArrowLeft, FiChevronDown, FiHelpCircle, FiLogOut, FiMenu, FiPlus, FiRefreshCw, FiSearch, FiUser,
} from "react-icons/fi";
import { useAuth } from "../../auth/AuthContext";
import { CONSOLE, brandButton, consoleChip, cx, focusRing, z } from "../../ui/tokens";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import ThemeToggle from "../../ui/ThemeToggle";
import HealthDot from "../admin/HealthDot";
import QuickActionsMenu from "../organization/QuickActionsMenu";
import UserAvatar from "../../ui/UserAvatar";
import { isAdminRole } from "./orgNav";

// Organization console topbar (ZST-ENG-DASH-001 ORG-01..08, EVT-01..07): search on the left,
// help and who you are on the right; inside an event, the way back to the console first.
//
// The search is real, not a decoration: in the console it searches events by name (the
// Events list, filtered on the server); inside an event it searches that event's Activity &
// Audit ledger (actions, actors, request references). Both are ordinary navigations.
//
// What it deliberately does not carry:
//   * a notification bell — there is no notification feed to back it, and a bell with nothing
//     behind it is a control that lies (one with a hardcoded "3" was removed earlier)
//   * a permanent health verdict — the rail's footer carries service status; the FAILURE case
//     (console state unreachable) still shows here, with a retry attached.
const UNKNOWN = { status: "neutral", label: "Status unavailable" };

function useClickOutside(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);
  return ref;
}

function SearchBox({ eventId }) {
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const submit = (e) => {
    e.preventDefault();
    const q = term.trim();
    if (!q) return;
    navigate(
      eventId
        ? `/organization/events/${eventId}/activity?q=${encodeURIComponent(q)}`
        : `/organization/events?q=${encodeURIComponent(q)}`
    );
    setTerm("");
  };
  const label = eventId ? "Search this event's activity, actors and request IDs" : "Search events by name";
  return (
    <form role="search" onSubmit={submit} className="relative hidden min-w-0 max-w-xl flex-1 md:block">
      <FiSearch aria-hidden="true" className={cx("pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2", CONSOLE.faint)} />
      <input
        type="search"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        aria-label={label}
        // Says what it searches. The frames read "events, recordings, people and settings";
        // only events are searchable by the API today, so the field claims nothing more.
        placeholder={eventId ? "Search this event's activity, actors and request IDs…" : "Search events by name or description…"}
        className={cx(
          "h-11 w-full rounded-xl border pl-10 pr-3 text-[13.5px]",
          "border-slate-200 bg-slate-50/70 text-slate-800 placeholder:text-slate-400",
          "hover:border-slate-300 focus-visible:border-violet-500 focus-visible:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/30",
          "dark:border-white/[0.14] dark:bg-white/[0.05] dark:text-neutral-100 dark:placeholder:text-neutral-500 dark:focus-visible:bg-white/[0.08]"
        )}
      />
    </form>
  );
}

export default function Topbar({
  onMenuClick,
  state,
  unknown,
  onRetry,
  quickActions = false,
  collapsed = false,
  onToggleCollapse,
  // Whether the mobile navigation drawer is open, so the hamburger can say what a tap will do.
  drawerOpen = false,
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useClickOutside(() => setMenuOpen(false));
  const inEvent = useMatch("/organization/events/:id/*");
  const eventId = inEvent?.params?.id;

  // The console-state user when it has loaded, falling back field by field to the signed-in
  // account, so the header never shows an empty name while the account has one.
  const person = {
    ...(state?.user || {}),
    name: state?.user?.name || user?.full_name,
    email: state?.user?.email || user?.email,
    // The signed-in account's own record first: it is re-read the moment the photo changes
    // (ProfilePhotoEditor), a beat before /console-state catches up.
    avatar_url: user ? user.avatar_url ?? null : state?.user?.avatar_url ?? null,
  };
  const canCreate = isAdminRole(state?.user?.role ?? user?.role ?? "org_admin");

  const iconBtnBase = cx(
    "grid place-items-center rounded-lg transition-colors duration-150 motion-reduce:transition-none",
    CONSOLE.muted,
    "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.06] dark:hover:text-white",
    focusRing
  );
  const iconBtn = cx("h-9 w-9", iconBtnBase);

  return (
    <header
      ref={menuRef}
      className={cx(
        // z.header, not z-20: pages have their own `sticky top-0 z-20` bars (Settings, and
        // every OrganizationPageHeader), and at an equal z-index the later one in the DOM wins,
        // so the account menu dropped under them and its upper half went blank. The shared
        // scale keeps the topbar above page content and below every portalled overlay.
        // Safe area: on Android devices that draw the app under the status bar (edge-to-edge,
        // despite bridge.js's overlay:false) the bar's controls sat in the status-bar strip,
        // where the system takes the taps. The inset pads the bar clear of it; the controls
        // keep their 64px row. It is 0 on the web build (no viewport-fit=cover there).
        "sticky top-0 flex h-[calc(4rem+env(safe-area-inset-top))] shrink-0 items-center gap-2 border-b px-3 pt-[env(safe-area-inset-top)] backdrop-blur-xl sm:gap-3 sm:px-5",
        z.header,
        CONSOLE.bar
      )}
    >
      {/* Two controls, one job each, and never both at once.
          Under lg the rail is an overlay drawer, so the hamburger opens and closes it.
          At lg and up the rail is always present and this collapses it to an icon strip.
          The desktop control sits at the very start of the bar, against the boundary it
          acts on — it reads as a handle on the sidebar rather than a page action. */}
      {/* 44×44 touch target (the 36px icon buttons are fine for a cursor, not a thumb); -ml-1
          keeps the icon exactly where it was. The label names what a tap will do. */}
      <button
        type="button"
        onClick={onMenuClick}
        aria-expanded={drawerOpen}
        aria-label={drawerOpen ? "Close navigation menu" : "Open navigation menu"}
        className={cx("-ml-1 h-11 w-11", iconBtnBase, "lg:hidden")}
      >
        <FiMenu className="text-xl" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={onToggleCollapse}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className={cx(iconBtn, "hidden border-transparent bg-transparent lg:grid dark:border-transparent dark:bg-transparent")}
      >
        {collapsed ? (
          <PanelLeftOpen className="h-[18px] w-[18px]" aria-hidden="true" />
        ) : (
          <PanelLeftClose className="h-[18px] w-[18px]" aria-hidden="true" />
        )}
      </button>

      {eventId && (
        <nav aria-label="Console" className="hidden shrink-0 items-center gap-2 text-[13.5px] font-semibold xl:flex">
          <Link to="/organization/events" className={cx("inline-flex items-center gap-2 rounded", CONSOLE.heading, focusRing)}>
            <FiArrowLeft aria-hidden="true" /> Organization Console
          </Link>
          <span aria-hidden="true" className={CONSOLE.faint}>/</span>
          <span className={CONSOLE.heading}>Event Workspace</span>
        </nav>
      )}

      <SearchBox key={eventId || "org"} eventId={eventId} />

      <div className="ml-auto flex items-center gap-2 sm:gap-2.5">
        {quickActions ? (
          <QuickActionsMenu />
        ) : unknown ? (
          <button
            onClick={onRetry}
            className={cx(
              "hidden items-center gap-1.5 rounded-full border px-2.5 py-1.5 md:inline-flex",
              "border-slate-200 bg-slate-50 dark:border-white/10 dark:bg-white/[0.04]",
              "transition-colors duration-150 hover:bg-slate-100 dark:hover:bg-white/[0.08] motion-reduce:transition-none",
              focusRing
            )}
            title="Couldn’t reach the API — click to retry"
          >
            <HealthDot status={UNKNOWN.status}>{UNKNOWN.label}</HealthDot>
            <FiRefreshCw className={cx("text-[12px]", CONSOLE.faint)} aria-hidden="true" />
          </button>
        ) : null}

        {/* Primary action below lg (the rail carries it on desktop), only with the privilege
            to create — no disabled CTA otherwise (ZST-UX-NAV-001 §3.1). */}
        {canCreate && (
          <button
            onClick={() => navigate("/organization/events?create=true")}
            className={cx(
              "group inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-[12px] font-semibold lg:hidden",
              brandButton,
              "active:scale-[0.98] transition-[background-image,box-shadow,transform] duration-150 ease-out",
              "motion-reduce:transition-none motion-reduce:active:scale-100",
              focusRing
            )}
          >
            <FiPlus className="text-[14px]" aria-hidden="true" />
            <span className="hidden sm:inline">Request live event</span>
          </button>
        )}

        <Link to="/organization/support" aria-label="Help and support" title="Help and support" className={iconBtn}>
          <FiHelpCircle className="text-[18px]" aria-hidden="true" />
        </Link>

        <ThemeToggle />

        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Account menu"
            className={cx(
              "flex items-center gap-2.5 rounded-xl py-1 pl-1 pr-2",
              "transition-colors duration-150 motion-reduce:transition-none",
              "hover:bg-slate-100 dark:hover:bg-white/[0.06]",
              menuOpen && "bg-slate-100 dark:bg-white/[0.06]",
              focusRing
            )}
          >
            <UserAvatar
              name={person.name}
              src={person.avatar_url}
              alt=""
              className={cx("h-9 w-9 rounded-full text-[12px] font-semibold", consoleChip)}
            />
            <span className="hidden min-w-0 text-left lg:block">
              <span className={cx("block max-w-[10rem] truncate text-[13px] font-semibold", CONSOLE.heading)}>{person.name || "Member"}</span>
              <span className={cx("block max-w-[10rem] truncate text-[11px]", CONSOLE.faint)}>{person.role_label || person.email}</span>
            </span>
            <FiChevronDown
              className={cx("hidden text-[14px] transition-transform duration-150 sm:block motion-reduce:transition-none", CONSOLE.faint, menuOpen && "rotate-180")}
              aria-hidden="true"
            />
          </button>
          {menuOpen && (
            <div
              role="menu"
              className={cx(
                "zk-pop-in absolute right-0 top-14 z-30 w-60 overflow-hidden rounded-xl border p-1 shadow-xl",
                "border-slate-200 bg-white shadow-slate-900/10",
                "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60"
              )}
            >
              <div className="flex items-center gap-2.5 px-3 py-2.5">
                <UserAvatar
                  name={person.name}
                  src={person.avatar_url}
                  className={cx("h-9 w-9 text-[11px] font-semibold", consoleChip)}
                />
                <div className="min-w-0">
                  <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{person.name || "Member"}</p>
                  <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{person.role_label || person.email}</p>
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
                {/* Your own profile lives here, in the account menu, not in the rail
                    (ZST-UX-NAV-001 §2: profile is an account utility, not a destination). */}
                Profile
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
