import { NavLink } from "react-router-dom";
import {
  FiX, FiGrid, FiLayers, FiUsers, FiCode, FiKey, FiLink2, FiUploadCloud,
  FiActivity, FiFilm, FiPlayCircle, FiRadio, FiUserCheck, FiBarChart2,
  FiCreditCard, FiShield, FiLifeBuoy, FiChevronDown, FiChevronsLeft, FiChevronsRight,
} from "react-icons/fi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import Logo from "../../ui/Logo";

// Organization console navigation, grouped by what the operator is doing:
//   HOME    — where the organization stands
//   BUILD   — the integration surface they develop against
//   OPERATE — running live sessions and their audience
//   MANAGE  — commercial, security and support
//
// `badge` names a counter on /organization/console-state; only non-zero counts render, so a
// quiet organization shows a quiet sidebar. Every destination is org-scoped — nothing here
// links into /admin/*, which an org admin is not authorized to load.
const GROUPS = [
  {
    label: "Home",
    items: [
      { to: "/organization/dashboard", label: "Overview", icon: FiGrid, end: true },
      { to: "/organization/profile", label: "Organization & Workspaces", icon: FiLayers },
      { to: "/organization/users", label: "Members & Access", icon: FiUsers },
    ],
  },
  {
    label: "Build",
    items: [
      { to: "/organization/developers", label: "Developer Platform", icon: FiCode },
      { to: "/organization/credentials", label: "Credentials", icon: FiKey },
      { to: "/organization/webhooks", label: "Webhooks", icon: FiLink2, badge: "webhooks" },
      { to: "/organization/live-inputs", label: "Live Inputs", icon: FiUploadCloud },
    ],
  },
  {
    label: "Operate",
    items: [
      { to: "/organization/sessions", label: "Streaming Sessions", icon: FiActivity, badge: "streaming_sessions" },
      { to: "/organization/recordings", label: "Media & Replay", icon: FiFilm },
      { to: "/organization/playback", label: "Playback & Access", icon: FiPlayCircle },
      { to: "/organization/events", label: "Live Events", icon: FiRadio, badge: "live_events" },
      { to: "/organization/audience", label: "Audience Access", icon: FiUserCheck },
    ],
  },
  {
    label: "Manage",
    items: [
      { to: "/organization/analytics", label: "Analytics", icon: FiBarChart2 },
      { to: "/organization/billing", label: "Usage & Entitlements", icon: FiCreditCard },
      { to: "/organization/settings", label: "Security & Governance", icon: FiShield },
      { to: "/organization/support", label: "Support & Status", icon: FiLifeBuoy },
    ],
  },
];

// Active row: a filled violet gradient with a soft cast beneath it, so the current
// destination is unmistakable at a glance instead of being a slightly tinted row.
// Declared here rather than in tokens' CONSOLE.navOn because that token is shared with the
// admin console rail, and this treatment is the org console's.
const NAV_ON =
  "bg-gradient-to-r from-violet-600 to-indigo-600 text-white shadow-[0_2px_10px_-2px_rgba(124,58,237,0.5)]";
const NAV_OFF = [
  "text-slate-600 dark:text-neutral-400",
  "hover:bg-slate-100 hover:text-slate-900",
  "dark:hover:bg-white/[0.07] dark:hover:text-white",
].join(" ");

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

// Label shown on hover when the rail is collapsed. Purely visual — the accessible name
// comes from aria-label on the row itself, so a screen reader never depends on this.
function Tip({ children, show }) {
  if (!show) return null;
  return (
    <span
      aria-hidden="true"
      className={cx(
        // zk-tip-in is a plain class, not a Tailwind variant target: an animation restarts
        // whenever an element goes from display:none to displayed, so it plays on each hover.
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

// Workspace identity block. This platform has ONE implicit workspace per organization, so
// the control shows what exists rather than pretending to switch between several — the
// chevron is disabled until real workspaces exist, with a title saying why. The switcher
// that DOES work lives in the topbar, where it drives the page's scope.
function WorkspaceHeader({ organization, workspace, count, collapsed }) {
  const name = organization?.name || "Organization";
  const scope = `workspace · ${workspace?.label || "—"}`;
  return (
    <div className={cx("group relative shrink-0", collapsed ? "px-3 lg:px-2.5" : "px-3")}>
      <div
        className={cx(
          "flex items-center gap-2.5 rounded-lg px-3 py-2",
          "transition-colors duration-150 motion-reduce:transition-none",
          CONSOLE.inset,
          collapsed && "lg:justify-center lg:px-2"
        )}
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-bold text-white shadow-sm shadow-violet-900/25">
          {initials(name)}
        </span>
        <div className={cx("min-w-0 flex-1", collapsed && "lg:hidden")}>
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{name}</p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{scope}</p>
        </div>
        <FiChevronDown
          className={cx(
            "shrink-0 text-[14px]",
            CONSOLE.faint,
            count < 2 && "opacity-40",
            collapsed && "lg:hidden"
          )}
          title={count < 2 ? "This organization has a single workspace" : "Switch workspace"}
          aria-hidden="true"
        />
      </div>
      <Tip show={collapsed}>
        {name} · {scope}
      </Tip>
    </div>
  );
}

export default function Sidebar({ open, onClose, state, collapsed = false, onToggleCollapse }) {
  const badges = state?.badges || {};
  const person = state?.user;

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-20 bg-slate-900/60 backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}

      {/* On desktop the rail is STICKY and exactly viewport-tall, not `static`. The org shell
          is min-h-screen (AppShell without fullHeight, unlike the admin console), so a static
          rail grows with the page and its overflow-y-auto nav never gets a height to scroll
          inside — the last nav items ended up below the fold, unreachable. h-screen bounds it;
          bottom-auto stops `inset-y-0` making it stick to top AND bottom.
          `collapsed` is a DESKTOP-only state: the mobile drawer is always full width, so every
          collapse rule is written `lg:` and the drawer is unaffected. */}
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-30 flex w-64 shrink-0 flex-col border-r",
          "transition-[transform,width] duration-200 ease-out motion-reduce:transition-none",
          "lg:sticky lg:top-0 lg:bottom-auto lg:h-screen lg:self-start lg:translate-x-0",
          CONSOLE.rail,
          collapsed ? "lg:w-[4.75rem]" : "lg:w-64",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        {/* Brand. Same wordmark size, centring and eyebrow treatment as the admin console's
            rail (components/admin/AdminSidebar) so the two consoles read as one product. The
            workspace identity sits BELOW it — the logo says which product you are in, the chip
            under it says which workspace, and they answer different questions.

            Collapsed swaps the wordmark for the square mark: the wordmark is ~2.9:1, so at any
            legible height it is wider than a 76px rail. */}
        <div className="relative flex shrink-0 flex-col items-center px-6 pb-4 pt-5">
          <span className={cx(collapsed && "lg:hidden")}>
            <Logo height="h-10" />
          </span>
          <img
            src="/zoiko-mark.png"
            alt="ZoikoStream"
            className={cx("hidden h-9 w-9 object-contain", collapsed && "lg:block")}
          />
          <p
            className={cx(
              "mt-2 text-center text-[10px] font-semibold uppercase tracking-[0.14em]",
              CONSOLE.faint,
              collapsed && "lg:hidden"
            )}
          >
            Organization Console
          </p>
          <button
            onClick={onClose}
            className={cx("absolute right-4 top-5 shrink-0 lg:hidden", CONSOLE.muted)}
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        <WorkspaceHeader
          organization={state?.organization}
          workspace={state?.workspace}
          count={state?.workspaces?.length ?? 1}
          collapsed={collapsed}
        />

        <nav
          className={cx(
            "zk-scroll-thin mt-4 flex-1 space-y-4 overflow-y-auto overflow-x-hidden pb-4",
            collapsed ? "px-3 lg:px-2.5" : "px-3"
          )}
        >
          {GROUPS.map((group) => (
            <div key={group.label} className="space-y-px">
              {/* Collapsed, the heading text would not fit — a rule keeps the grouping
                  legible instead of running all sixteen icons together. */}
              <p
                className={cx(
                  "px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.12em]",
                  CONSOLE.faint,
                  collapsed && "lg:hidden"
                )}
              >
                {group.label}
              </p>
              <div
                aria-hidden="true"
                className={cx("mx-2 mb-2 hidden border-t", CONSOLE.divider, collapsed && "lg:block")}
              />
              {group.items.map(({ to, label, icon: Icon, end, badge }) => {
                const count = badges[badge] || 0;
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end={end}
                    onClick={onClose}
                    aria-label={collapsed ? label : undefined}
                    className={({ isActive }) =>
                      cx(
                        "group relative flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] font-medium",
                        "transition-[background-color,color,box-shadow] duration-150 motion-reduce:transition-none",
                        focusRing,
                        collapsed && "lg:justify-center lg:px-0",
                        isActive ? NAV_ON : NAV_OFF
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span
                          aria-hidden="true"
                          className={cx(
                            "absolute -left-3 top-1/2 w-[3px] -translate-y-1/2 rounded-r transition-all duration-150 motion-reduce:transition-none",
                            collapsed && "lg:hidden",
                            isActive
                              ? "h-5 bg-violet-500"
                              : "h-2.5 bg-transparent group-hover:bg-slate-300 dark:group-hover:bg-neutral-600"
                          )}
                        />
                        <span className="relative shrink-0">
                          <Icon
                            className={cx(
                              "shrink-0 text-[15px] transition-colors duration-150 motion-reduce:transition-none",
                              isActive
                                ? "text-white"
                                : "text-slate-400 group-hover:text-slate-600 dark:text-neutral-500 dark:group-hover:text-neutral-200"
                            )}
                            aria-hidden="true"
                          />
                          {/* Collapsed, a count has nowhere to sit — a dot on the icon keeps the
                              "needs attention" signal without the number. */}
                          {count > 0 && (
                            <span
                              aria-hidden="true"
                              className={cx(
                                "absolute -right-1 -top-1 hidden h-2 w-2 rounded-full bg-rose-500 ring-2",
                                "ring-white dark:ring-black",
                                collapsed && "lg:block"
                              )}
                            />
                          )}
                        </span>
                        <span className={cx("min-w-0 flex-1 truncate", collapsed && "lg:hidden")}>
                          {label}
                        </span>
                        {count > 0 && (
                          <span
                            className={cx(
                              "grid h-[18px] min-w-[18px] shrink-0 place-items-center rounded-full px-1 text-[10px] font-bold",
                              isActive
                                ? "bg-white/20 text-white"
                                : "bg-rose-500/15 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400",
                              collapsed && "lg:hidden"
                            )}
                          >
                            {count}
                          </span>
                        )}
                        <Tip show={collapsed}>
                          {label}
                          {count > 0 ? ` · ${count}` : ""}
                        </Tip>
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Collapse control — desktop only, since the mobile drawer opens and closes instead. */}
        <div className={cx("hidden shrink-0 border-t px-3 py-2 lg:block", CONSOLE.divider)}>
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cx(
              "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[12px] font-medium",
              "transition-colors duration-150 motion-reduce:transition-none",
              CONSOLE.muted,
              "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.07] dark:hover:text-white",
              focusRing,
              collapsed && "lg:justify-center lg:px-0"
            )}
          >
            {collapsed ? (
              <FiChevronsRight className="shrink-0 text-[15px]" aria-hidden="true" />
            ) : (
              <FiChevronsLeft className="shrink-0 text-[15px]" aria-hidden="true" />
            )}
            <span className={cx(collapsed && "lg:hidden")}>Collapse</span>
          </button>
        </div>

        {/* Identity — the role label comes from the server, so it always matches what the
            API will actually authorize. */}
        <div className={cx("shrink-0 border-t", CONSOLE.divider)}>
          <div
            className={cx(
              "group relative flex items-center gap-2.5 px-6 py-3.5",
              collapsed && "lg:justify-center lg:px-0"
            )}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-semibold text-white shadow-sm shadow-violet-900/25">
              {initials(person?.name)}
            </span>
            <div className={cx("min-w-0", collapsed && "lg:hidden")}>
              <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                {person?.name || "Member"}
              </p>
              <p className={cx("truncate text-[11px]", type.label, CONSOLE.faint)}>
                {person?.role_label || "—"}
              </p>
            </div>
            <Tip show={collapsed}>
              {person?.name || "Member"}
              {person?.role_label ? ` · ${person.role_label}` : ""}
            </Tip>
          </div>
        </div>
      </aside>
    </>
  );
}
