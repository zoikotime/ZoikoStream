import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  FiMenu, FiSearch, FiCommand, FiSun, FiMoon, FiLogOut, FiUser, FiChevronDown,
} from "react-icons/fi";
import api from "../../api";
import { useAuth } from "../../auth/AuthContext";
import useInterval from "../../hooks/useInterval";
import { useTheme } from "../../theme/ThemeContext";
import { CONSOLE, brand, cx, type } from "../../ui/tokens";
import HealthDot from "./HealthDot";

// Route -> breadcrumb leaf. Keys mirror the sidebar so the crumb always names the page the
// operator is actually on.
const CRUMBS = {
  dashboard: "Command Center",
  "live-events": "Live Operations",
  "event-readiness": "Event Readiness",
  organizations: "Organizations",
  media: "Media",
  security: "Trust & Safety",
  users: "Identity & Access",
  developers: "Developer Platform",
  subscriptions: "Usage & Entitlements",
  support: "Support Operations",
  settings: "Platform Configuration",
  governance: "Governance",
  audit: "Audit",
  status: "System Status",
  analytics: "Analytics",
  roles: "Roles & Permissions",
  "feature-flags": "Feature Flags",
  releases: "Release Center",
};

// There is no default verdict. If the console can't reach its own status endpoint it says
// so — reporting "All systems operational" from a failed request is the one lie this
// console must never tell, because an operator would act on it during an incident.
const VERDICT = {
  ok: { status: "ok", label: "All systems operational" },
  warn: { status: "warn", label: "Degraded performance" },
  down: { status: "down", label: "Service disruption" },
};
const UNKNOWN = { status: "neutral", label: "Status unavailable" };

// The operator's own timezone, named. The console is time-critical, so the zone is stated
// rather than assumed — a 15:00 start means nothing without it.
const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;
const timeFmt = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZoneName: "short",
});

function useClickOutside(onClose) {
  const ref = useRef(null);
  useEffect(() => {
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [onClose]);
  return ref;
}

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

export default function AdminTopbar({ onMenuClick, state, unknown, onRetry }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();

  const [q, setQ] = useState("");
  const [hits, setHits] = useState({ term: "", items: [] });
  const [open, setOpen] = useState(null); // "search" | "profile"
  const searchRef = useRef(null);
  const menuRef = useClickOutside(() => setOpen(null));

  const [clock, setClock] = useState(() => timeFmt.format(new Date()));
  useInterval(() => setClock(timeFmt.format(new Date())), 1000);

  const crumb = CRUMBS[pathname.split("/")[2]] || "Console";
  const verdict = unknown ? UNKNOWN : VERDICT[state?.health?.overall] || UNKNOWN;

  // ⌘K / Ctrl+K focuses the command bar (console convention).
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        setOpen("search");
      }
      if (e.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Real search against /admin/search, debounced. Results are organizations, events and
  // users that actually exist — no client-side index to drift out of date.
  //
  // Hits are stored WITH the term they belong to, so results for a previous query can
  // never paint under a newer one and the effect never has to clear state synchronously.
  const term = q.trim();
  useEffect(() => {
    if (term.length < 2) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .get("/admin/search", { params: { q: term }, signal: controller.signal })
        .then((r) => setHits({ term, items: r.data }))
        .catch(() => {}); // aborted or failed — the panel just shows no matches
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [term]);
  const results = hits.term === term ? hits.items : [];

  const go = (to) => { setOpen(null); setQ(""); navigate(to); };
  const iconBtn = cx(
    "grid h-9 w-9 place-items-center rounded-lg transition",
    CONSOLE.muted,
    "hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-white/[0.06] dark:hover:text-white"
  );

  const person = useMemo(
    () => state?.user || { name: user?.full_name, email: user?.email },
    [state, user]
  );

  return (
    <header
      ref={menuRef}
      className={cx(
        "sticky top-0 z-20 flex h-16 shrink-0 items-center gap-3 border-b px-3 backdrop-blur sm:px-5",
        CONSOLE.bar
      )}
    >
      <button onClick={onMenuClick} className={cx(iconBtn, "lg:hidden")} aria-label="Toggle menu">
        <FiMenu className="text-xl" />
      </button>

      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="hidden shrink-0 items-center gap-1.5 text-[13px] md:flex">
        <Link to="/admin/dashboard" className={cx(CONSOLE.faint, "hover:underline")}>
          Super Admin
        </Link>
        <span className={CONSOLE.faint}>/</span>
        <span className={cx("font-semibold", CONSOLE.heading)}>{crumb}</span>
      </nav>

      {/* Command bar */}
      <div className="relative mx-auto min-w-0 w-full max-w-md">
        <FiSearch className={cx("pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[15px]", CONSOLE.faint)} />
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen("search"); }}
          onFocus={() => setOpen("search")}
          placeholder="Search organizations, events, sessions…"
          aria-label="Search organizations, events and sessions"
          className={cx(
            "w-full rounded-full border py-2 pl-9 pr-14 text-[13px] outline-none transition",
            "border-slate-200 bg-slate-100/70 text-slate-800 placeholder:text-slate-400 focus:border-violet-400 focus:bg-white",
            "dark:border-white/10 dark:bg-white/[0.05] dark:text-neutral-100 dark:placeholder:text-neutral-500 dark:focus:border-violet-500/60"
          )}
        />
        <kbd
          className={cx(
            "pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded border px-1.5 py-0.5 text-[10px] font-medium sm:flex",
            "border-slate-200 bg-white text-slate-400 dark:border-white/10 dark:bg-white/[0.04] dark:text-neutral-500"
          )}
        >
          <FiCommand className="text-[9px]" />K
        </kbd>

        {open === "search" && term.length >= 2 && (
          <div
            className={cx(
              "absolute left-0 right-0 top-11 z-30 overflow-hidden rounded-xl border py-1 shadow-xl",
              "border-slate-200 bg-white dark:border-white/10 dark:bg-neutral-950"
            )}
          >
            {results.length === 0 ? (
              <p className={cx("px-3 py-3 text-[13px]", CONSOLE.faint)}>No matches for “{term}”.</p>
            ) : (
              results.map((r) => (
                <button
                  key={`${r.kind}:${r.label}`}
                  onMouseDown={() => go(r.to)}
                  className={cx(
                    "flex w-full items-center gap-3 px-3 py-2 text-left text-[13px]",
                    CONSOLE.body,
                    "hover:bg-slate-50 dark:hover:bg-white/[0.05]"
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{r.label}</span>
                  <span className={cx("shrink-0 truncate text-[11px]", CONSOLE.faint)}>{r.detail}</span>
                  <span className={cx("shrink-0 text-[10px] uppercase tracking-wide", CONSOLE.faint)}>
                    {r.kind}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
        {/* Systems verdict — real, from /admin/console-state. When that call failed this is
            a retry button rather than a link, because the useful action is to re-ask. */}
        {unknown ? (
          <button
            onClick={onRetry}
            className="hidden lg:block"
            title="Couldn’t reach the platform API — click to retry"
          >
            <HealthDot status={UNKNOWN.status}>{UNKNOWN.label}</HealthDot>
          </button>
        ) : (
          <Link
            to="/admin/status"
            className="hidden lg:block"
            title={`${state?.health?.degraded ?? 0} of ${state?.health?.total ?? 0} degraded`}
          >
            <HealthDot status={verdict.status} pulse={verdict.status !== "ok"}>
              {verdict.label}
            </HealthDot>
          </Link>
        )}

        {/* Operator timezone + live clock */}
        <span className={cx("hidden items-center gap-1.5 text-[12px] xl:flex", CONSOLE.faint)}>
          <span>{ZONE}</span>
          <span aria-hidden="true">·</span>
          <span className={cx(type.mono, "tabular-nums")}>{clock}</span>
        </span>

        <button onClick={toggle} className={iconBtn} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <FiSun className="text-[17px]" /> : <FiMoon className="text-[17px]" />}
        </button>

        {/* Profile */}
        <div className="relative">
          <button
            onClick={() => setOpen(open === "profile" ? null : "profile")}
            className={cx(
              "flex items-center gap-2 rounded-full border py-1 pl-1 pr-2 transition",
              "border-slate-200 hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/[0.05]"
            )}
            aria-label="Account menu"
          >
            <span className={cx("grid h-7 w-7 place-items-center rounded-full text-[10px] font-semibold text-white", brand.chip)}>
              {initials(person.name)}
            </span>
            <FiChevronDown className={cx("hidden text-[13px] sm:block", CONSOLE.faint)} />
          </button>
          {open === "profile" && (
            <div
              className={cx(
                "absolute right-0 top-12 z-30 w-56 overflow-hidden rounded-xl border py-1 shadow-xl",
                "border-slate-200 bg-white dark:border-white/10 dark:bg-neutral-950"
              )}
            >
              <div className={cx("border-b px-4 py-2.5", CONSOLE.divider)}>
                <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>
                  {person.name || "Super Admin"}
                </p>
                <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{person.email}</p>
              </div>
              <button
                onClick={() => go("/admin/settings")}
                className={cx("flex w-full items-center gap-2.5 px-4 py-2.5 text-[13px]", CONSOLE.body, "hover:bg-slate-50 dark:hover:bg-white/[0.05]")}
              >
                <FiUser /> Profile
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
