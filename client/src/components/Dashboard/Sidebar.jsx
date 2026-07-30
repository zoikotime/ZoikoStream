import { NavLink } from "react-router-dom";
import {
  FiX, FiGrid, FiLayers, FiUsers, FiCode, FiKey, FiLink2, FiUploadCloud,
  FiActivity, FiFilm, FiPlayCircle, FiRadio, FiUserCheck, FiBarChart2,
  FiCreditCard, FiShield, FiLifeBuoy, FiChevronDown,
} from "react-icons/fi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";

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

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

// Workspace identity block. This platform has ONE implicit workspace per organization, so
// the control shows what exists rather than pretending to switch between several — the
// chevron is disabled until real workspaces exist, with a title saying why.
function WorkspaceHeader({ organization, workspace, count }) {
  const name = organization?.name || "Organization";
  return (
    <div className="px-3 pt-3">
      <div className={cx("flex items-center gap-2.5 rounded-lg px-2.5 py-2", CONSOLE.inset)}>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-bold text-white">
          {initials(name)}
        </span>
        <div className="min-w-0 flex-1">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{name}</p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            workspace · {workspace?.label || "—"}
          </p>
        </div>
        <FiChevronDown
          className={cx("shrink-0 text-[14px]", CONSOLE.faint, count < 2 && "opacity-40")}
          title={count < 2 ? "This organization has a single workspace" : "Switch workspace"}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

export default function Sidebar({ open, onClose, state }) {
  const badges = state?.badges || {};
  const person = state?.user;

  return (
    <>
      {open && <div className="fixed inset-0 z-20 bg-black/50 lg:hidden" onClick={onClose} />}

      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-30 flex w-64 shrink-0 flex-col border-r transition-transform lg:static lg:translate-x-0",
          CONSOLE.rail,
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="flex shrink-0 items-start justify-between">
          <WorkspaceHeader
            organization={state?.organization}
            workspace={state?.workspace}
            count={state?.workspaces?.length ?? 1}
          />
          <button
            onClick={onClose}
            className={cx("mr-3 mt-4 shrink-0 lg:hidden", CONSOLE.muted)}
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        <nav className="zk-scroll-thin mt-4 flex-1 space-y-4 overflow-y-auto px-3 pb-4">
          {GROUPS.map((group) => (
            <div key={group.label} className="space-y-px">
              <p className={cx("px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.12em]", CONSOLE.faint)}>
                {group.label}
              </p>
              {group.items.map(({ to, label, icon: Icon, end, badge }) => {
                const count = badges[badge] || 0;
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end={end}
                    onClick={onClose}
                    className={({ isActive }) =>
                      cx(
                        "group relative flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-[13px] font-medium",
                        "transition-colors duration-150 motion-reduce:transition-none",
                        focusRing,
                        isActive ? CONSOLE.navOn : CONSOLE.navOff
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span
                          aria-hidden="true"
                          className={cx(
                            "absolute -left-3 top-1/2 w-[3px] -translate-y-1/2 rounded-r transition-all duration-150 motion-reduce:transition-none",
                            isActive
                              ? "h-5 bg-violet-500"
                              : "h-2.5 bg-transparent group-hover:bg-slate-300 dark:group-hover:bg-neutral-600"
                          )}
                        />
                        <Icon
                          className={cx("shrink-0 text-[15px]", isActive ? CONSOLE.navIconOn : CONSOLE.navIconOff)}
                          aria-hidden="true"
                        />
                        <span className="min-w-0 flex-1 truncate">{label}</span>
                        {count > 0 && (
                          <span
                            className={cx(
                              "grid h-[18px] min-w-[18px] shrink-0 place-items-center rounded-full px-1 text-[10px] font-bold",
                              "bg-rose-500/15 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400"
                            )}
                          >
                            {count}
                          </span>
                        )}
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Identity — the role label comes from the server, so it always matches what the
            API will actually authorize. */}
        <div className={cx("shrink-0 border-t", CONSOLE.divider)}>
          <div className="flex items-center gap-2.5 px-4 py-3.5">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-semibold text-white">
              {initials(person?.name)}
            </span>
            <div className="min-w-0">
              <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                {person?.name || "Member"}
              </p>
              <p className={cx("truncate text-[11px]", type.label, CONSOLE.faint)}>
                {person?.role_label || "—"}
              </p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
