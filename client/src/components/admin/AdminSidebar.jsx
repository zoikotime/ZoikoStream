import { useState } from "react";
import { NavLink } from "react-router-dom";
import toast from "react-hot-toast";
import { FiX, FiActivity, FiCheckSquare, FiGrid, FiFilm, FiShield, FiUser, FiBarChart2, FiClock, FiSliders, FiGlobe, FiFileText, FiTarget, FiDollarSign, FiTrendingUp, FiFlag, FiPackage, FiLock } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, brand, cx, focusRing, type } from "../../ui/tokens";
import Logo from "../../ui/Logo";

// Platform navigation, grouped the way the console is meant to be read:
//   OPERATE  — what is happening right now and needs a human
//   GOVERN   — the accounts, media and people being governed
//   PLATFORM — the surfaces and controls underneath all of it
// `badge` names a counter on /admin/console-state; only non-zero counts render, so a quiet
// platform shows a quiet sidebar.
// Active row treatment, copied deliberately from the org console's rail
// (components/Dashboard/Sidebar.jsx) so the two consoles read as one product. NOT
// CONSOLE.navOn: that shared token is the filled violet→indigo gradient and is still used
// by PlaybackAccess's segmented control, so changing it there would repaint an unrelated
// screen. The org rail defines its own for exactly the same reason.
const NAV_ON = "bg-violet-50 text-violet-700 dark:bg-violet-500/[0.16] dark:text-violet-200";
const NAV_ICON_ON = "text-violet-600 dark:text-violet-300";

// Four tiers, seventeen entries, down from twenty flat ones. Nothing was deleted and no
// route was removed. The three that left the rail became TABS on the page they always
// belonged to (pages/admin/mergedPages.jsx), and their own URLs still resolve, so a
// bookmark or a runbook link is unaffected (see App.jsx):
//
//   Roles               -> Identity & Access  (read-only reference data ABOUT the roles that
//                          page assigns; services/admin.roles() is derived from
//                          security._ROLE_RANK and is explicitly not editable)
//   Developer Platform  -> Organizations      (it only ever fetched /admin/organizations)
//   Media Infrastructure-> System Status      (both called the IDENTICAL endpoint,
//                          /admin/platform-health — the clearest duplication in the console)
//
// "Advanced" exists because Governance, Feature Flags and Release Center are real but
// low-frequency: a rail is read top-down under pressure, and burying Live Operations under
// twelve platform links costs more than the links are worth.
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
      { to: "/admin/users", label: "Identity & Access", icon: FiUser },
      { to: "/admin/security", label: "Trust & Safety", icon: FiShield },
      { to: "/admin/audit", label: "Audit", icon: FiFileText },
    ],
  },
  {
    label: "Platform",
    items: [
      { to: "/admin/media", label: "Media", icon: FiFilm },
      { to: "/admin/subscriptions", label: "Usage & Entitlements", icon: FiBarChart2 },
      { to: "/admin/commerce", label: "Commerce", icon: FiDollarSign },
      { to: "/admin/analytics", label: "Analytics", icon: FiTrendingUp },
      { to: "/admin/support", label: "Support Operations", icon: FiClock },
      { to: "/admin/status", label: "System Status", icon: FiShield, badge: "system_status" },
      { to: "/admin/settings", label: "Platform Configuration", icon: FiSliders },
    ],
  },
  {
    label: "Advanced",
    items: [
      { to: "/admin/governance", label: "Governance", icon: FiGlobe },
      { to: "/admin/feature-flags", label: "Feature Flags", icon: FiFlag },
      { to: "/admin/releases", label: "Release Center", icon: FiPackage },
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
      <div className="flex items-center gap-2 px-6 py-2">
        <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
        <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
          Access state unknown · API unreachable
        </p>
      </div>
    );
  }
  if (!elevation) {
    return (
      // Same action, same endpoint — a compact row instead of a dashed card, so the rail
      // keeps the org console's uncluttered look without losing the one control that turns
      // read-only standing access into the ability to act.
      <button
        onClick={onStart}
        disabled={busy}
        title="Standing access — request elevation to act"
        className={cx(
          "mx-3 flex w-[calc(100%-1.5rem)] items-center gap-2 rounded-lg px-3 py-2 text-left disabled:opacity-60",
          "transition-colors duration-150 motion-reduce:transition-none",
          focusRing,
          "hover:bg-slate-100 dark:hover:bg-white/[0.08]"
        )}
      >
        <FiLock className={cx("shrink-0 text-[15px]", CONSOLE.faint)} aria-hidden="true" />
        <span className={cx("truncate text-[12px] font-medium", CONSOLE.body)}>Standing access</span>
        <span className={cx("ml-auto shrink-0 text-[11px]", CONSOLE.faint)}>Elevate</span>
      </button>
    );
  }
  return (
    // Elevated is the one state that SHOULD be loud: it means destructive actions are
    // currently possible. Tightened, not muted.
    <div className="mx-3 rounded-lg bg-amber-50 px-3 py-2 dark:bg-amber-500/[0.10]">
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
        <p className="min-w-0 flex-1 truncate text-[12px] font-semibold text-amber-800 dark:text-amber-300">
          Elevated · {elevation.scope}
        </p>
        <p className={cx(type.mono, "shrink-0 text-[12px] text-amber-900 dark:text-amber-200")}>
          {mmss(seconds)}
        </p>
      </div>
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
        <div className="relative flex shrink-0 flex-col items-center px-6 pb-3 pt-5">
          <Logo height="h-10" />
          {/* Kept, because an operator should be able to tell which console they are in at a
              glance — but demoted to a caption so it never outweighs the navigation. */}
          <p className={cx("mt-1.5 text-center text-[9px] font-medium uppercase tracking-[0.12em]", CONSOLE.faint)}>
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
        <nav className="zk-scroll-thin mt-1 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 pb-4">
          {GROUPS.map((group, gi) => (
            <div key={group.label} className="space-y-1">
              {/* A hairline above each group after the first, as the org rail does, so the
                  three tiers separate without three more labels competing with the nav. The
                  heading stays but is deliberately quieter than any row beneath it. */}
              {gi > 0 && (
                <div aria-hidden="true" className={cx("mx-2 mb-2 border-t", CONSOLE.divider)} />
              )}
              <p className={cx("px-3 pb-0.5 text-[10px] font-medium uppercase tracking-[0.1em]", CONSOLE.faint)}>
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
                        // Same geometry as the org rail: ~44px rows, read as a product
                        // menu rather than a dense tool palette.
                        "group relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-[14px] font-medium",
                        "transition-colors duration-150 motion-reduce:transition-none",
                        focusRing,
                        isActive ? NAV_ON : CONSOLE.navOff
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon
                          className={cx("shrink-0 text-[17px]", isActive ? NAV_ICON_ON : CONSOLE.navIconOff)}
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
                    //
                    // These MUST be the vocabulary security.require_elevation checks
                    // (ELEVATION_SCOPES). They used to read "Platform Operations" /
                    // "organizations:write", which nothing on the server matched, so the one
                    // button that is supposed to unlock the console granted a token that
                    // satisfied no gate — the operator elevated and every protected action
                    // still 403'd. server/test_admin_elevation.py pins this button's payload
                    // against the guarded endpoints so the two cannot drift apart again.
                    //
                    // "support" is deliberately absent: reaching into a customer tenant is
                    // not something an operator grants themselves here. That goes through
                    // /admin/support-access with the organization's own approval (ORG-009).
                    scope: "platform",
                    scopes: ["identity", "broadcast"],
                    reason: "Console session",
                    minutes: 15,
                  }),
                "Elevation granted for 15 minutes"
              )
            }
          />
          <div className={cx("mt-1 flex items-center gap-2.5 border-t px-6 py-3.5", CONSOLE.divider)}>
            <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-full text-[11px] font-semibold text-white", brand.chip)}>
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
