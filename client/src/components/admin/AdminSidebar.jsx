import { useState } from "react";
import { NavLink } from "react-router-dom";
import toast from "react-hot-toast";
import {
  FiX, FiActivity, FiCheckSquare, FiGrid, FiFilm, FiShield, FiUser, FiCode,
  FiBarChart2, FiClock, FiSliders, FiGlobe, FiFileText, FiTarget, FiDollarSign,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import Logo from "../../ui/Logo";

// Platform navigation, grouped the way the console is meant to be read:
//   OPERATE  — what is happening right now and needs a human
//   GOVERN   — the accounts, media and people being governed
//   PLATFORM — the surfaces and controls underneath all of it
// `badge` names a counter on /admin/console-state; only non-zero counts render, so a quiet
// platform shows a quiet sidebar.
const GROUPS = [
  {
    label: "Operate",
    items: [
      { to: "/admin/dashboard", label: "Command Center", icon: FiTarget, end: true },
      { to: "/admin/live-events", label: "Live Operations", icon: FiActivity, badge: "live_operations" },
      { to: "/admin/event-readiness", label: "Event Readiness", icon: FiCheckSquare, badge: "event_readiness" },
    ],
  },
  {
    label: "Govern",
    items: [
      { to: "/admin/organizations", label: "Organizations", icon: FiGrid },
      { to: "/admin/media", label: "Media", icon: FiFilm },
      { to: "/admin/security", label: "Trust & Safety", icon: FiShield },
      { to: "/admin/users", label: "Identity & Access", icon: FiUser },
    ],
  },
  {
    label: "Platform",
    items: [
      { to: "/admin/developers", label: "Developer Platform", icon: FiCode },
      { to: "/admin/subscriptions", label: "Usage & Entitlements", icon: FiBarChart2 },
      { to: "/admin/commerce", label: "Live Events Commerce", icon: FiDollarSign },
      { to: "/admin/support", label: "Support Operations", icon: FiClock },
      { to: "/admin/settings", label: "Platform Configuration", icon: FiSliders },
      { to: "/admin/governance", label: "Governance", icon: FiGlobe },
      { to: "/admin/audit", label: "Audit", icon: FiFileText },
      { to: "/admin/status", label: "System Status", icon: FiShield, badge: "system_status" },
    ],
  },
];

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

const mmss = (total) => {
  const s = Math.max(0, total);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

// Elevation widget. A super admin holds standing read access; high-risk actions require a
// scoped, expiring grant — this is where that grant is visible and endable. The countdown
// ticks locally from the server's seconds_remaining so it doesn't need a request per second.
function Elevation({ elevation, seconds, onEnd, onStart, busy, unknown }) {
  // Unknown is not the same as "not elevated". If console-state failed we cannot claim the
  // operator holds only standing access, and we must not offer to elevate against an API
  // we can't reach.
  if (unknown) {
    return (
      <div className={cx("m-3 rounded-lg border border-dashed px-3 py-2.5", "border-slate-300 dark:border-white/15")}>
        <p className={cx(type.caption, "font-semibold", CONSOLE.faint)}>Access state unknown</p>
        <p className={cx("mt-0.5 text-[11px]", CONSOLE.faint)}>Platform API unreachable</p>
      </div>
    );
  }
  if (!elevation) {
    return (
      <button
        onClick={onStart}
        disabled={busy}
        className={cx(
          "m-3 rounded-lg border border-dashed px-3 py-2.5 text-left disabled:opacity-60",
          "transition-colors duration-150 motion-reduce:transition-none",
          focusRing,
          "border-slate-300 hover:border-violet-400 hover:bg-violet-50",
          "dark:border-white/15 dark:hover:border-violet-500/60 dark:hover:bg-white/[0.08]"
        )}
      >
        <p className={cx(type.caption, "font-semibold", CONSOLE.body)}>Standing access</p>
        <p className={cx("mt-0.5 text-[11px]", CONSOLE.faint)}>Request elevation to act</p>
      </button>
    );
  }
  return (
    <div className="m-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 dark:border-amber-500/30 dark:bg-amber-500/[0.08]">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] font-semibold text-amber-800 dark:text-amber-300">
          Elevated · {elevation.scope}
        </p>
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
      </div>
      <p className={cx("mt-1", type.mono, "text-[13px] text-amber-900 dark:text-amber-200")}>
        {mmss(seconds)} remaining
      </p>
      <div className="mt-1.5 flex items-center gap-3 text-[11px]">
        <button
          onClick={() => toast(elevation.scopes?.length ? elevation.scopes.join(", ") : elevation.scope)}
          className="underline decoration-dotted underline-offset-2 text-amber-800 hover:text-amber-900 dark:text-amber-300 dark:hover:text-amber-200"
        >
          View scopes
        </button>
        <button
          onClick={onEnd}
          disabled={busy}
          className="underline decoration-dotted underline-offset-2 text-amber-800 hover:text-amber-900 disabled:opacity-60 dark:text-amber-300 dark:hover:text-amber-200"
        >
          End now
        </button>
      </div>
    </div>
  );
}

export default function AdminSidebar({ open, onClose, state, unknown, onChange }) {
  const { user } = useAuth();
  const [busy, setBusy] = useState(false);
  const badges = state?.badges || {};
  const elevation = state?.elevation || null;

  // Countdown ticks locally from the server's seconds_remaining (no request per second),
  // and re-seeds whenever console-state refreshes or a different grant appears.
  const [seconds, setSeconds] = useState(elevation?.seconds_remaining ?? 0);
  const [seed, setSeed] = useState(elevation?.id);
  if (elevation?.id !== seed) {
    setSeed(elevation?.id);
    setSeconds(elevation?.seconds_remaining ?? 0);
  }
  useInterval(() => setSeconds((s) => Math.max(0, s - 1)), 1000, Boolean(elevation) && seconds > 0);

  const act = async (fn, message) => {
    setBusy(true);
    try {
      await fn();
      toast.success(message);
      onChange?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const person = state?.user || { name: user?.full_name, department: null };

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
        {/* Brand. Kept identical to the org console's rail (components/Dashboard/Sidebar) so
            the two consoles read as one product — see the note there for why the wordmark is
            centred and why the close button is positioned rather than a flex sibling. */}
        <div className="relative flex shrink-0 flex-col items-center px-6 pb-4 pt-5">
          <Logo height="h-10" />
          <p className={cx("mt-2 text-center text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
            Super Admin Console
          </p>
          <button
            onClick={onClose}
            className={cx("absolute right-4 top-5 shrink-0 lg:hidden", CONSOLE.muted)}
            aria-label="Close menu"
          >
            <FiX className="text-xl" />
          </button>
        </div>

        {/* Grouped nav */}
        <nav className="zk-scroll-thin flex-1 space-y-4 overflow-y-auto px-3 pb-4">
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
                        {/* Left rail marker: solid violet when active, a muted stub on hover
                            so the row telegraphs that it is a target before you click. */}
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

        {/* Elevation + identity */}
        <div className={cx("shrink-0 border-t", CONSOLE.divider)}>
          <Elevation
            elevation={elevation}
            seconds={seconds}
            busy={busy}
            unknown={unknown}
            onEnd={() => act(() => api.delete("/admin/elevation"), "Elevation ended")}
            onStart={() =>
              act(
                () =>
                  api.post("/admin/elevation", {
                    // The scope is what is being elevated INTO, not who the operator is —
                    // it names the capability set the grant covers.
                    scope: "Platform Operations",
                    scopes: ["organizations:write", "subscriptions:write", "settings:write"],
                    reason: "Console session",
                    minutes: 15,
                  }),
                "Elevation granted for 15 minutes"
              )
            }
          />
          <div className="flex items-center gap-2.5 px-6 pb-4">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-violet-600 to-indigo-700 text-[11px] font-semibold text-white">
              {initials(person.name)}
            </span>
            <div className="min-w-0">
              <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                {person.name || "Super Admin"}
              </p>
              <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{person.department || "—"}</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
