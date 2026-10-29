import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { FiArrowRight, FiChevronDown, FiLogOut, FiPlus, FiUser, FiX } from "react-icons/fi";
import { Building2 } from "lucide-react";
import { useAuth } from "../../auth/AuthContext";
import { actionStops, consoleChip, cx, focusRing, z } from "../../ui/tokens";
import UserAvatar from "../../ui/UserAvatar";
import Logo from "../../ui/Logo";
import { activeChildId, groupIsActive, isAdminRole, resolveOrgNav } from "./orgNav";

// Organization console rail (ZST-UX-NAV-001 §4.1, Figure B; ZST-ENG-DASH-001 ORG-01..08):
//
//   logo · current workspace (organization + your role) · Create Event (with create privilege)
//   WORKSPACE — the seven destinations, Developers when enabled
//   service status · you
//
// The rail is navy in both themes, as in every frame: it is the console's frame, not page
// content, so it does not change with the page theme. The ORDER and the gates live in ./orgNav;
// this component only draws. Inside an event the Event Workspace passes its own `nav` and
// everything around it stays (§3.2 "the sidebar changes to event-local navigation without
// spawning another app").

// Active destination: the console action ramp, as in the frames (its stops hold white text
// above AA end to end — see tokens' `actionStops`).
const NAV_ON = `bg-gradient-to-r ${actionStops} text-white shadow-[0_8px_20px_-8px_rgba(217,70,239,0.65)]`;
const NAV_OFF = "text-white/80 hover:bg-white/[0.07] hover:text-white";
const ROW = "h-11 gap-3 px-3 text-[13px]";

// Label shown on hover when the rail is collapsed. Purely visual — the accessible name comes
// from aria-label on the row itself, so a screen reader never depends on this.
export function Tip({ children, show }) {
  if (!show) return null;
  return (
    <span
      aria-hidden="true"
      className={cx(
        "zk-tip-in pointer-events-none absolute left-full top-1/2 z-50 ml-3 hidden -translate-y-1/2 whitespace-nowrap",
        "rounded-lg border px-2.5 py-1.5 text-[12px] font-medium shadow-xl",
        "border-slate-200 bg-white text-slate-700 shadow-slate-900/10",
        "dark:border-white/10 dark:bg-neutral-900 dark:text-neutral-100 dark:shadow-black/60",
        "lg:group-hover:block lg:group-focus-visible:block"
      )}
    >
      {children}
    </span>
  );
}

const PILL = {
  live: "bg-emerald-400/20 text-emerald-200 ring-emerald-300/30",
  warn: "bg-amber-400/20 text-amber-100 ring-amber-300/40",
};

export function CountPill({ count, collapsed, suffix, tone = "live" }) {
  if (!count) return null;
  return (
    <span
      className={cx(
        "inline-flex h-[20px] min-w-[20px] shrink-0 items-center justify-center gap-1 rounded-full px-1.5 text-[10px] font-bold uppercase tracking-wide ring-1 ring-inset",
        PILL[tone] || PILL.live,
        collapsed && "lg:hidden"
      )}
    >
      {count}
      {suffix ? ` ${suffix}` : ""}
    </span>
  );
}

export function RailSection({ children, collapsed }) {
  return (
    <p className={cx("px-3 pb-2 pt-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/55", collapsed && "lg:hidden")}>
      {children}
    </p>
  );
}

// One rail row: icon, label, optional count / hint. Shared with the Event Workspace rail so the
// two navigations are the same control.
// `reserveEnd`: the row has an expand button laid over its right edge (a sibling, since a
// button cannot sit inside a link), so the link keeps that space clear for its count pill.
export function RailLink({ to, label, icon: Icon, active, current, count = 0, countSuffix, countTone, collapsed, onClick, hint, chevron, reserveEnd = false }) {
  return (
    <Link
      to={to}
      onClick={onClick}
      aria-current={current ? "page" : undefined}
      aria-label={collapsed ? label : undefined}
      className={cx(
        "group relative flex min-w-0 flex-1 items-center rounded-xl font-medium",
        ROW,
        reserveEnd && "pr-9",
        "transition-[background-color,color,box-shadow] duration-150 motion-reduce:transition-none",
        focusRing,
        collapsed && "lg:justify-center lg:px-0",
        active ? NAV_ON : NAV_OFF
      )}
    >
      <Icon aria-hidden="true" className={cx("shrink-0 text-[18px]", active ? "text-white" : "text-white/70 group-hover:text-white")} />
      {count > 0 && (
        <span aria-hidden="true" className={cx("absolute right-2 top-2 hidden h-2 w-2 rounded-full bg-emerald-400", collapsed && "lg:block")} />
      )}
      <span className={cx("min-w-0 flex-1 truncate", collapsed && "lg:hidden")}>{label}</span>
      {hint && !count && (
        // On the active row the hint sits over the ramp's pink end (3.0:1 bare); a dark backing
        // holds it above AA wherever it lands.
        <span className={cx("shrink-0 text-[10px] font-bold uppercase tracking-wide", active ? "rounded bg-black/25 px-1 text-white" : "text-white/50", collapsed && "lg:hidden")}>{hint}</span>
      )}
      <CountPill count={count} collapsed={collapsed} suffix={countSuffix} tone={countTone} />
      {chevron}
      <Tip show={collapsed}>
        {label}
        {count > 0 ? ` · ${count}` : ""}
      </Tip>
    </Link>
  );
}

// The sub-items under the open group: a bulleted list on a guide line, as in the frames.
export function RailChildren({ id, items }) {
  return (
    <ul id={id} className="mb-1 ml-[1.3rem] mt-1 space-y-0.5 border-l border-white/15 pl-3">
      {items.map((item) => (
        <li key={item.key}>
          {item.gated ? (
            <span aria-disabled="true" className="flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px] text-white/40">
              <span className="truncate">• {item.label}</span>
              <span className="text-[9px] font-bold uppercase tracking-[0.1em] text-white/40">Gated</span>
            </span>
          ) : (
            <Link
              to={item.to}
              onClick={item.onClick}
              aria-current={item.current ? "page" : undefined}
              aria-label={item.ariaLabel}
              className={cx(
                "flex items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-[12.5px]",
                "transition-colors duration-150 motion-reduce:transition-none",
                focusRing,
                item.current ? "bg-white/[0.12] font-semibold text-white" : "text-white/65 hover:bg-white/[0.06] hover:text-white"
              )}
            >
              <span className="truncate">
                <span aria-hidden="true" className={cx("mr-1.5", item.current ? "text-fuchsia-300" : "text-white/40")}>•</span>
                {item.label}
              </span>
              {item.count ? <CountPill count={item.count} /> : null}
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

function OrgNav({ state, role, collapsed, onNavigate }) {
  const { pathname, search, hash } = useLocation();
  const badges = state?.badges || {};
  const items = useMemo(
    () => resolveOrgNav({ role, features: state?.features || {}, pathname }),
    [role, state?.features, pathname]
  );
  const activeGroup = items.find((item) => groupIsActive(item, pathname))?.id || null;

  // Manual open/close, remembered only for the group you are in: moving to another group
  // discards it, so only that one opens. Keyed by group rather than reset in an effect.
  const [toggled, setToggled] = useState({ group: activeGroup, open: {} });
  const manual = toggled.group === activeGroup ? toggled.open : {};
  const setManual = (fn) => setToggled({ group: activeGroup, open: fn(manual) });

  return (
    <nav
      aria-label="Organization"
      className={cx(
        "zk-scroll-thin mt-4 flex flex-1 flex-col overflow-y-auto overflow-x-hidden pb-4",
        collapsed ? "px-3 lg:px-2.5" : "px-3"
      )}
    >
      <RailSection collapsed={collapsed}>Workspace</RailSection>
      <ul className="space-y-1">
        {items.map((item) => {
          const inGroup = item.id === activeGroup;
          const childId = inGroup ? activeChildId(item, pathname, search, hash) : null;
          const hasChildren = Boolean(item.children?.length);
          const open = hasChildren && !collapsed && (manual[item.id] ?? inGroup);
          const listId = `rail-${item.id.replace(/\./g, "-")}`;
          return (
            <li key={item.id}>
              <div className="relative flex items-center">
                <RailLink
                  to={item.to}
                  label={item.label}
                  icon={item.icon}
                  active={inGroup}
                  // A collapsed rail shows no children, so the group itself is where you are.
                  current={inGroup && (!childId || collapsed)}
                  count={badges[item.badge] || 0}
                  countSuffix={item.badge ? "live" : undefined}
                  collapsed={collapsed}
                  reserveEnd={hasChildren}
                  onClick={(e) => {
                    if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || (e.button && e.button !== 0))) {
                      return;
                    }
                    onNavigate?.(e);
                    if (hasChildren) {
                      setManual((m) => ({ ...m, [item.id]: true }));
                    }
                    const itemPath = item.to.split("?")[0];
                    if (pathname === itemPath && typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
                      window.dispatchEvent(new CustomEvent("zoiko:route-reset", { detail: { path: itemPath } }));
                    }
                  }}
                />
                {hasChildren && (
                  <button
                    type="button"
                    onClick={() => setManual((m) => ({ ...m, [item.id]: !open }))}
                    aria-expanded={open}
                    aria-controls={listId}
                    aria-label={`${open ? "Collapse" : "Expand"} ${item.label}`}
                    className={cx(
                      "absolute right-1.5 grid h-8 w-7 place-items-center rounded-md",
                      inGroup ? "text-white/90 hover:bg-white/15" : "text-white/55 hover:bg-white/10 hover:text-white",
                      focusRing,
                      collapsed && "lg:hidden"
                    )}
                  >
                    <FiChevronDown
                      aria-hidden="true"
                      className={cx("transition-transform duration-150 motion-reduce:transition-none", open && "rotate-180")}
                    />
                  </button>
                )}
              </div>
              {open && (
                <RailChildren
                  id={listId}
                  items={item.children.map((child) => ({
                    key: child.id,
                    label: child.label,
                    ariaLabel: child.aria,
                    to: child.to,
                    current: child.id === childId,
                    count: badges[child.badge] || 0,
                    onClick: (e) => {
                      if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || (e.button && e.button !== 0))) {
                        return;
                      }
                      onNavigate?.(e);
                      const childPath = child.to.split("?")[0];
                      if (pathname === childPath && child.to === item.to && typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
                        window.dispatchEvent(new CustomEvent("zoiko:route-reset", { detail: { path: childPath } }));
                      }
                    },
                  }))}
                />
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

const HEALTH_UNKNOWN = { status: "neutral", label: "Status unavailable" };
const DOT = {
  ok: "bg-emerald-400", success: "bg-emerald-400", warn: "bg-amber-400", warning: "bg-amber-400",
  down: "bg-rose-500", error: "bg-rose-500", critical: "bg-rose-500",
};

// The user card at the foot of the rail. It opens the same account actions as the topbar
// menu — Profile and Log out — upward, inside the rail.
function AccountCard({ person, collapsed, onNavigate }) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const item = cx(
    "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] text-white/85 hover:bg-white/10 hover:text-white",
    focusRing
  );
  return (
    <div ref={ref} className={cx("relative px-3 pb-3", collapsed && "lg:px-2")}>
      {open && (
        <div role="menu" aria-label="Your account" className="absolute bottom-full left-3 right-3 mb-2 rounded-xl border border-white/15 bg-[#121a3f] p-1 shadow-2xl">
          <button role="menuitem" className={item} onClick={() => { setOpen(false); onNavigate?.(); navigate("/organization/profile"); }}>
            <FiUser aria-hidden="true" /> Profile
          </button>
          <button role="menuitem" className={cx(item, "text-rose-300 hover:text-rose-200")} onClick={logout}>
            <FiLogOut aria-hidden="true" /> Log out
          </button>
        </div>
      )}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${person?.name || "Member"}, ${person?.role_label || "member"} — account`}
        className={cx(
          "group relative flex w-full items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.05] p-2.5 text-left",
          "hover:bg-white/[0.09]",
          focusRing,
          collapsed && "lg:justify-center lg:border-0 lg:bg-transparent lg:p-1"
        )}
      >
        <UserAvatar
          name={person?.name}
          src={person?.avatar_url}
          className={cx("h-9 w-9 rounded-lg text-[12px] font-semibold", consoleChip)}
        />
        <span className={cx("min-w-0 flex-1", collapsed && "lg:hidden")}>
          <span className="block truncate text-[13px] font-semibold text-white">{person?.name || "Member"}</span>
          <span className="block truncate text-[11px] text-white/55">{person?.role_label || "—"}</span>
        </span>
        <FiChevronDown aria-hidden="true" className={cx("shrink-0 text-white/60 transition-transform", open && "rotate-180", collapsed && "lg:hidden")} />
        <Tip show={collapsed}>
          {person?.name || "Member"}
          {person?.role_label ? ` · ${person.role_label}` : ""}
        </Tip>
      </button>
    </div>
  );
}

export default function Sidebar({ open, onClose, state, collapsed = false, nav = null }) {
  const { user } = useAuth();
  const person = state?.user;
  // The server's view of the role first (it is what the API authorizes), the token's second.
  const role = state?.user?.role || user?.role;
  const canCreate = isAdminRole(role);
  const health = state?.health || HEALTH_UNKNOWN;

  return (
    <>
      {/* The mobile drawer and its backdrop are an overlay, so they sit on z.overlay, above
          the topbar's z.header. At z-20/z-30 they were BELOW it: the bar covered the drawer's
          top, and its Close button could not be tapped. The desktop rail keeps lg:z-30. */}
      {open && (
        <div
          className={cx("fixed inset-0 bg-slate-900/60 backdrop-blur-sm lg:hidden", z.overlay)}
          onClick={onClose}
        />
      )}

      {/* Sticky and exactly viewport-tall on desktop so the nav scrolls inside it and the
          footer never drops below the fold. `collapsed` is desktop-only; the mobile drawer is
          always full width. */}
      <aside
        className={cx(
          "fixed inset-y-0 left-0 flex w-64 shrink-0 flex-col border-r border-white/[0.06]",
          z.overlay,
          "lg:z-30",
          // Same status-bar clearance as the topbar (0 on the web build).
          "pt-[env(safe-area-inset-top)]",
          "bg-gradient-to-b from-[#121b44] via-[#0f1838] to-[#0b1230] text-white",
          "transition-[transform,width,visibility] duration-200 ease-out motion-reduce:transition-none",
          "lg:sticky lg:top-0 lg:bottom-auto lg:h-screen lg:self-start lg:translate-x-0",
          collapsed ? "lg:w-[4.5rem]" : "lg:w-[16.25rem]",
          // Closed on a small screen, the drawer is also invisible: translated away alone, its
          // ~30 links stayed in the Tab order (focus vanished off-screen) and were read out.
          // `visibility` switches at the end of the transition, so the slide-out still shows.
          open ? "translate-x-0" : "-translate-x-full max-lg:invisible"
        )}
      >
        <div className="relative flex shrink-0 items-center justify-center border-b border-white/[0.07] px-5 pb-4 pt-5">
          <span className={cx(collapsed && "lg:hidden")}>
            <Logo height="h-11" />
          </span>
          <img
            src="/zoiko-mark.png"
            alt="ZoikoStream"
            className={cx("hidden h-9 w-9 rounded-lg bg-white/95 object-contain p-1", collapsed && "lg:block")}
          />
          <button
            onClick={onClose}
            className={cx("absolute right-3 top-5 shrink-0 rounded-md p-1 text-white/70 lg:hidden", focusRing)}
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        {/* Which organization, and as whom. The active tenancy must always be explicit
            (§3.2) — the rail is where it is read on every page. No switcher chevron: a member
            belongs to one organization, so there is nothing to switch to. Inside an event
            the event card names the organization instead (EVT frames), so this one steps aside. */}
        <div className={cx("shrink-0 px-3 pt-4", (collapsed || nav) && "lg:hidden", nav && "hidden")}>
          <div className="flex items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.05] p-2.5">
            <span aria-hidden="true" className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg", consoleChip)}>
              <Building2 className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-white/50">Current workspace</p>
              <p className="truncate text-[13px] font-semibold text-white" title={state?.organization?.name || undefined}>
                {state?.organization?.name || "Your organization"}
              </p>
              <p className="truncate text-[11.5px] text-white/55">
                {person?.role_label || "Member"}
                {state?.organization?.plan ? ` · ${state.organization.plan}` : ""}
              </p>
            </div>
          </div>
        </div>

        {canCreate && !nav && (
          <div className={cx("shrink-0 px-3 pt-3", collapsed && "lg:px-2.5")}>
            <Link
              to="/organization/events?create=true"
              onClick={onClose}
              aria-label={collapsed ? "Create event" : undefined}
              className={cx(
                "group relative flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-[13px] font-semibold text-white",
                "bg-gradient-to-r shadow-[0_10px_22px_-10px_rgba(217,70,239,0.8)]",
                actionStops,
                "hover:brightness-110 transition-[filter] duration-150 motion-reduce:transition-none",
                focusRing
              )}
            >
              <FiPlus aria-hidden="true" className="text-[16px]" />
              <span className={cx(collapsed && "lg:hidden")}>Create event</span>
              <Tip show={collapsed}>Create event</Tip>
            </Link>
          </div>
        )}

        {nav || <OrgNav state={state} role={role} collapsed={collapsed} onNavigate={onClose} />}

        <div className="shrink-0 border-t border-white/[0.07] pt-2">
          {/* Both controls open the service-health section on Support; neither wraps the other. */}
          <div
            className={cx(
              "mx-3 mb-2 flex items-center justify-between gap-2 rounded-lg px-2 py-2 text-[12.5px] text-white/70 hover:bg-white/[0.06] hover:text-white",
              collapsed && "lg:mx-2 lg:justify-center"
            )}
          >
            <Link
              to="/organization/support#service-health"
              onClick={onClose}
              aria-label={`Service status: ${health.label || "unknown"}`}
              title={`Service status: ${health.label || "unknown"}`}
              className={cx("group relative inline-flex min-w-0 items-center gap-2 rounded-md", focusRing)}
            >
              <span aria-hidden="true" className={cx("h-2 w-2 shrink-0 rounded-full ring-2 ring-white/10", DOT[health.status] || "bg-slate-400")} />
              <span className={cx("truncate", collapsed && "lg:hidden")}>Service status</span>
              <Tip show={collapsed}>Service status · {health.label || "Unknown"}</Tip>
            </Link>
            <Link
              to="/organization/support#service-health"
              onClick={onClose}
              aria-label="View service status details"
              className={cx("inline-flex shrink-0 items-center gap-1 rounded-md text-white/60", focusRing, collapsed && "lg:hidden")}
            >
              View status <FiArrowRight aria-hidden="true" />
            </Link>
          </div>
          <AccountCard person={person} collapsed={collapsed} onNavigate={onClose} />
        </div>
      </aside>
    </>
  );
}
