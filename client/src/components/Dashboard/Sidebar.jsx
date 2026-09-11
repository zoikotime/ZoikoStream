import { NavLink } from "react-router-dom";
import {
  FiX, FiGrid, FiLayers, FiUsers,
  FiActivity, FiFilm, FiPlayCircle, FiRadio, FiUserCheck, FiBarChart2,
  FiCreditCard, FiLifeBuoy, FiSettings,
} from "react-icons/fi";
import { CONSOLE, brand, cx, focusRing, type } from "../../ui/tokens";
import Logo from "../../ui/Logo";

// Organization console navigation.
//
// ONE presentation for every /organization/* route. There used to be two — a grouped
// HOME/BUILD/OPERATE/MANAGE rail for the console and a flat one the dashboard opted into —
// and the seam showed the moment you clicked out of the dashboard: the rail changed
// structure, width and density mid-session. A console that redecorates itself as you move
// through it reads as two applications.
//
// Ordered by how often somebody needs the destination, not by which subsystem owns it:
//
//   primary    the event workflow — what this product is for
//   secondary  the integration surface and org identity, reached occasionally
//   footer     Analytics, Billing, Settings — management, deliberately last
//
// Every `to` is a route that exists in App.jsx. `badge` names a counter on
// /organization/console-state; only non-zero counts render, so a quiet organization shows a
// quiet sidebar. Nothing here links into /admin/*, which an org admin cannot load.
const NAV = [
  {
    items: [
      { to: "/organization/dashboard", label: "Dashboard", icon: FiGrid, end: true },
      { to: "/organization/events", label: "Events", icon: FiRadio, badge: "live_events" },
      { to: "/organization/audience", label: "Audience", icon: FiUserCheck },
      { to: "/organization/users", label: "Members", icon: FiUsers },
      { to: "/organization/recordings", label: "Recordings", icon: FiFilm },
      { to: "/organization/sessions", label: "Streaming Sessions", icon: FiActivity, badge: "streaming_sessions" },
      { to: "/organization/playback", label: "Playback & Access", icon: FiPlayCircle },
    ],
  },
  {
    // Developer Platform, Credentials and Live Inputs used to sit here. All three are
    // developer- or encoder-only, low-frequency, and none is needed to schedule, run or
    // review an event — so they no longer hold permanent top-level space. Their routes are
    // unchanged and every one of them is reachable from Settings -> Developer (and, for a
    // live input, from the event it belongs to).
    rule: true,
    items: [
      { to: "/organization/profile", label: "Organization", icon: FiLayers },
    ],
  },
  {
    // Pushed to the bottom of the rail by `mt-auto`, not merely listed last: management
    // utilities should be findable without ever competing with the event workflow above.
    // Support & Status sits at the very end — it is where you go when something is wrong,
    // which is the least frequent and most deliberate trip in the console.
    rule: true,
    footer: true,
    items: [
      { to: "/organization/analytics", label: "Analytics", icon: FiBarChart2 },
      { to: "/organization/billing", label: "Billing", icon: FiCreditCard },
      { to: "/organization/settings", label: "Settings", icon: FiSettings },
      { to: "/organization/support", label: "Support & Status", icon: FiLifeBuoy },
    ],
  },
];

// Active row: a soft violet pill. The filled brand ramp this replaces was the right answer
// for a dense operational rail, but at this row height and spacing a saturated gradient in
// the sidebar competes with the primary action in the topbar — and the tint still reads
// unmistakably as "you are here".
const NAV_ON = "bg-violet-50 text-violet-700 dark:bg-violet-500/[0.16] dark:text-violet-200";
const NAV_OFF = CONSOLE.navOff;

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

export default function Sidebar({ open, onClose, state, collapsed = false }) {
  const badges = state?.badges || {};
  const person = state?.user;

  // ~44px rows: this rail is navigated, not scanned like a table, and the extra height is
  // what makes it read as a product menu rather than a tool palette.
  const rowShape = "gap-3 px-3 py-2.5 text-[14px]";

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
          {/* No "ORGANIZATION CONSOLE" eyebrow and no workspace chip. Both answered "where
              am I in the console", which is a question the navigation itself answers, and
              between them they pushed the first real destination ~120px down the rail. */}
          <button
            onClick={onClose}
            className={cx("absolute right-4 top-5 shrink-0 lg:hidden", CONSOLE.muted)}
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        <nav
          className={cx(
            // flex column with `gap`, not `space-y`: the footer group positions itself
            // with mt-auto, and space-y sets margin-top on the same element — the two
            // would be fighting over one property.
            "zk-scroll-thin mt-4 flex flex-1 flex-col gap-4 overflow-y-auto overflow-x-hidden pb-4",
            collapsed ? "px-3 lg:px-2.5" : "px-3"
          )}
        >
          {NAV.map((group, gi) => (
            <div
              key={`section-${gi}`}
              className={cx("space-y-1", group.footer && "mt-auto pt-2")}
            >
              {/* A hairline instead of a heading. It separates the three tiers without
                  adding three more labels to read before you can navigate. */}
              {group.rule && (
                <div aria-hidden="true" className={cx("mx-2 mb-2 border-t", CONSOLE.divider)} />
              )}
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
                        "group relative flex items-center rounded-lg font-medium",
                        rowShape,
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
                            // The soft pill IS the active marker now; a second rail-edge
                            // bar alongside it just doubles the signal.
                            "hidden",
                            isActive ? "h-5 bg-violet-500" : "h-2.5 bg-transparent"
                          )}
                        />
                        <span className="relative shrink-0">
                          <Icon
                            className={cx(
                              "shrink-0 transition-colors duration-150 motion-reduce:transition-none",
                              "text-[17px]",
                              isActive
                                ? "text-violet-600 dark:text-violet-300"
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
                              "bg-rose-500/15 text-rose-600 dark:bg-rose-500/20 dark:text-rose-400",
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

        {/* Identity — the role label comes from the server, so it always matches what the
            API will actually authorize. */}
        <div className={cx("shrink-0 border-t", CONSOLE.divider)}>
          <div
            className={cx(
              "group relative flex items-center gap-2.5 px-6 py-3.5",
              collapsed && "lg:justify-center lg:px-0"
            )}
          >
            <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white shadow-sm shadow-violet-900/25", brand.chip)}>
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
