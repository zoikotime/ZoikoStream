// client/src/components/moderation/ModeratorHeader.jsx
// Header: brand, event name, live status, and the console's health strip — viewer count,
// elapsed time, connection, recording, latency and network health.
import { useState } from "react";
import useInterval from "../../hooks/useInterval";
import { Link } from "react-router-dom";
import {
  FiUsers, FiClock, FiSun, FiMoon, FiLogOut, FiWifi, FiWifiOff,
  FiRefreshCw, FiActivity, FiVideo, FiShield, FiEye,
} from "react-icons/fi";
import { useTheme } from "../../theme/ThemeContext";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";

// Elapsed time since the event went live, as H:MM:SS (or MM:SS under an hour).
const fmtElapsed = (s) => {
  const pad = (n) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  const m = pad(Math.floor((s % 3600) / 60));
  return `${h > 0 ? `${h}:` : ""}${m}:${pad(s % 60)}`;
};

// Socket state -> what the moderator needs to know. "reconnecting" carries the auto-
// reconnect attempt, so a blip reads as "recovering" rather than "broken".
const CONNECTION = {
  open: { icon: FiWifi, label: "Connected", tone: "text-emerald-600 dark:text-emerald-400" },
  connecting: { icon: FiRefreshCw, label: "Connecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  reconnecting: { icon: FiRefreshCw, label: "Reconnecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  offline: { icon: FiWifiOff, label: "Offline", tone: "text-rose-600 dark:text-rose-400" },
  unauthorized: { icon: FiWifiOff, label: "Not authorized", tone: "text-rose-600 dark:text-rose-400" },
};

// Latency thresholds: the spec's target is sub-200ms end to end.
const latencyTone = (ms) =>
  ms == null ? "text-slate-400" : ms < 200 ? "text-emerald-500" : ms < 600 ? "text-amber-500" : "text-rose-500";

// One pill shape for every indicator, so the strip reads as a single instrument cluster.
function Pill({ icon: Icon, children, title, className = "", iconClass = "" }) {
  return (
    <div
      title={title}
      className={cx("flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 dark:bg-slate-800", className)}
    >
      <Icon className={cx("shrink-0", iconClass || "text-slate-500 dark:text-slate-400")} aria-hidden="true" />
      {children}
    </div>
  );
}

const num = "text-sm font-semibold tabular-nums text-slate-900 dark:text-white";

export default function ModeratorHeader({
  event,
  viewers = 0,
  participants = 0,
  connection = "connecting",
  latency,
  attempt = 0,
  canModerate = false,
  enforced = false,
}) {
  const { theme, toggle } = useTheme();
  const live = event?.status === "live";
  const conn = CONNECTION[connection] || CONNECTION.connecting;

  // Elapsed is derived from the server's start timestamp, so a reconnect can't leave the
  // clock drifting — it re-anchors instead of continuing a local count.
  const startedAt = event?.started_at ? new Date(event.started_at).getTime() : null;
  const [elapsed, setElapsed] = useState(() => (startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0));
  useInterval(() => setElapsed(startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0), 1000, live);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      <Link to="/moderator/dashboard">
        <Logo height="h-7">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Moderator Console</span>
        </Logo>
      </Link>

      <div className="min-w-0 dark:border-slate-700 md:border-l md:border-slate-200 md:pl-4">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">{event?.name || "Live event"}</p>
        <p className="hidden truncate text-xs text-slate-500 md:block dark:text-slate-400">
          {event?.host ? `Hosted by ${event.host}` : "No host assigned"}
        </p>
      </div>

      {live ? <Badge status="error" live>LIVE</Badge> : <Badge status="neutral">{event?.status || "Offline"}</Badge>}

      {event?.recording && (
        <Badge status="error" dot className="hidden sm:inline-flex" title="This event is being recorded">
          <FiVideo aria-hidden="true" /> REC
        </Badge>
      )}

      {/* Moderator status — read-only consoles must say so, not silently drop actions. */}
      <Badge
        status={canModerate ? "success" : "warning"}
        dot
        className="hidden lg:inline-flex"
        title={canModerate ? "You can moderate this event" : "You are not assigned as a moderator — actions are disabled"}
      >
        <FiShield aria-hidden="true" /> {canModerate ? "Moderator" : "View only"}
      </Badge>

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2 sm:gap-3">
        <Pill icon={FiClock} title="Time since the event went live">
          <span className={num}>{startedAt ? fmtElapsed(elapsed) : "—"}</span>
        </Pill>

        <Pill icon={FiEye} title="Viewers watching now">
          <span className={num}>{viewers.toLocaleString()}</span>
          <span className="hidden text-xs text-slate-500 sm:inline dark:text-slate-400">viewers</span>
        </Pill>

        <Pill icon={FiUsers} title="Everyone connected to this event" className="hidden sm:flex">
          <span className={num}>{participants.toLocaleString()}</span>
        </Pill>

        {/* Connection + auto-reconnect. The attempt count is the honest signal that the
            console is recovering by itself and the moderator needn't touch anything. */}
        <Pill
          icon={conn.icon}
          iconClass={cx(conn.tone, conn.spin && "animate-spin motion-reduce:animate-none")}
          title={attempt > 0 ? `${conn.label} — auto-reconnect attempt ${attempt}` : conn.label}
        >
          <span className={cx("text-xs font-semibold", conn.tone)}>
            {conn.label}
            {attempt > 0 && connection !== "open" ? ` ·${attempt}` : ""}
          </span>
        </Pill>

        <Pill
          icon={FiActivity}
          iconClass={latencyTone(latency)}
          title={
            latency == null
              ? "Round-trip latency — measured once connected"
              : `Round-trip latency to the server (target under 200ms)${enforced ? "" : " · LiveKit not connected"}`
          }
          className="hidden md:flex"
        >
          <span className={cx("text-xs font-semibold tabular-nums", latencyTone(latency))}>
            {latency == null ? "—" : `${latency} ms`}
          </span>
        </Pill>

        <button
          onClick={toggle}
          className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          aria-label="Toggle theme"
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>
        {/* Back to the moderator's own landing page — not the org console, which a moderator
            has no access to (RoleRoute allow=["org_admin"]). */}
        <Link to="/moderator/dashboard" className="hidden text-slate-400 hover:text-rose-500 sm:block" aria-label="Exit console" title="Exit console">
          <FiLogOut className="text-xl" />
        </Link>
      </div>
    </header>
  );
}
