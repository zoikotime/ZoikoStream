// client/src/components/host/HostHeader.jsx
// Producer header: event identity, broadcast state, and the instrument cluster — viewers,
// peak, roster split, elapsed, recording, health, latency, network, memory and UI load.
//
// Every number here has a real source. The ones this stack cannot measure from a browser
// (OS CPU, encoder bitrate) render "—" with a tooltip saying why, because a broadcast
// console that invents telemetry is worse than one that admits the gap.
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSun, FiMoon, FiLogOut, FiClock, FiEye, FiUsers, FiTrendingUp, FiMic,
  FiShield, FiVideo, FiActivity, FiWifi, FiWifiOff, FiRefreshCw, FiCpu,
  FiHardDrive, FiHeart, FiMonitor,
} from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import useSystemStats from "../../hooks/useSystemStats";
import { useTheme } from "../../theme/ThemeContext";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";
import {
  BROADCAST_TONE, BROADCAST_LABEL, HEALTH_TONE, HEALTH_LABEL, NETWORK_TONE, meterTone,
} from "../../data/host";

const pad = (n) => String(n).padStart(2, "0");
const fmtElapsed = (s) =>
  `${Math.floor(s / 3600) > 0 ? `${Math.floor(s / 3600)}:` : ""}${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;

const CONNECTION = {
  open: { icon: FiWifi, label: "Connected", tone: "text-emerald-600 dark:text-emerald-400" },
  connecting: { icon: FiRefreshCw, label: "Connecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  reconnecting: { icon: FiRefreshCw, label: "Reconnecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  offline: { icon: FiWifiOff, label: "Offline", tone: "text-rose-600 dark:text-rose-400" },
  unauthorized: { icon: FiWifiOff, label: "Not authorized", tone: "text-rose-600 dark:text-rose-400" },
};

const latencyTone = (ms) =>
  ms == null ? "text-slate-400" : ms < 200 ? "text-emerald-500" : ms < 600 ? "text-amber-500" : "text-rose-500";

const TONE_TEXT = {
  success: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-600 dark:text-amber-400",
  danger: "text-rose-600 dark:text-rose-400",
  neutral: "text-slate-500 dark:text-slate-400",
};

// One pill shape for the whole cluster, so it reads as a single instrument panel.
function Pill({ icon: Icon, title, tone, children, className = "" }) {
  return (
    <div
      title={title}
      className={cx("flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1.5 dark:bg-slate-800", className)}
    >
      <Icon className={cx("shrink-0 text-sm", tone || "text-slate-500 dark:text-slate-400")} aria-hidden="true" />
      {children}
    </div>
  );
}

const num = "text-sm font-semibold tabular-nums text-slate-900 dark:text-white";
const small = "text-xs font-semibold tabular-nums";

export default function HostHeader({
  event,
  broadcast,
  analytics,
  health,
  recording,
  connection = "connecting",
  latency,
  attempt = 0,
  canHost = false,
  recovering = false,
  media,                 // { actual } from useMediaPreview — measured capture, may be null
}) {
  const { theme, toggle } = useTheme();
  const stats = useSystemStats(true);
  const conn = CONNECTION[connection] || CONNECTION.connecting;

  const status = broadcast?.status || "preview";
  const live = status === "live";
  const a = analytics || {};

  // Elapsed live time, anchored to the server's start timestamp minus paused time — so it
  // survives a reconnect without drifting and doesn't count the pauses.
  const startedAt = broadcast?.started_at ? new Date(broadcast.started_at).getTime() : null;
  const pausedMs = broadcast?.paused_ms || 0;
  const frozenAt = broadcast?.paused_at ? new Date(broadcast.paused_at).getTime() : null;
  const elapsedOf = () => {
    if (!startedAt) return 0;
    const end = frozenAt || Date.now();
    return Math.max(0, Math.floor((end - startedAt - pausedMs) / 1000));
  };
  const [elapsed, setElapsed] = useState(elapsedOf);
  useInterval(() => setElapsed(elapsedOf()), 1000, !!startedAt && status !== "ended");

  const net = stats.network;
  const netTone = net ? NETWORK_TONE[net.effectiveType] || "neutral" : "neutral";
  const resolution = media?.actual?.width ? `${media.actual.width}×${media.actual.height}` : null;

  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      <Link to="/organization/dashboard">
        <Logo height="h-7">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Producer Console</span>
        </Logo>
      </Link>

      <div className="min-w-0 dark:border-slate-700 md:border-l md:border-slate-200 md:pl-3">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">{event?.name || "Live event"}</p>
        <p className="hidden truncate text-xs text-slate-500 md:block dark:text-slate-400">
          {event?.host ? `Hosted by ${event.host}` : "No host assigned"}
        </p>
      </div>

      {live ? (
        <Badge status="error" live>LIVE</Badge>
      ) : (
        <Badge tone={BROADCAST_TONE[status]} dot>{BROADCAST_LABEL[status] || status}</Badge>
      )}

      {recording && (
        <Badge
          status={recording.status === "paused" ? "warning" : "error"}
          dot
          title={recording.enforced
            ? `Recording ${recording.status} · ${recording.quality || ""}`
            : "Recording state is tracked, but LiveKit egress isn't capturing a file"}
        >
          <FiVideo aria-hidden="true" /> {recording.status === "paused" ? "REC PAUSED" : "REC"}
          {!recording.enforced && " (not captured)"}
        </Badge>
      )}

      {recovering && (
        <Badge tone="danger" dot title="The media room dropped. The broadcast is held open for the publisher to reconnect.">
          Auto-recovery
        </Badge>
      )}

      {health && (
        <Badge
          tone={HEALTH_TONE[health.level]}
          dot
          className="hidden lg:inline-flex"
          title={health.issues?.length ? health.issues.join(" · ") : "All broadcast signals normal"}
        >
          <FiHeart aria-hidden="true" /> {HEALTH_LABEL[health.level] || health.level}
        </Badge>
      )}

      {!canHost && (
        <Badge tone="warning" dot className="hidden lg:inline-flex" title="You aren't assigned as host, so broadcast controls are disabled">
          <FiShield aria-hidden="true" /> View only
        </Badge>
      )}

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        <Pill icon={FiClock} title="Live time, excluding paused periods">
          <span className={num}>{startedAt ? fmtElapsed(elapsed) : "—"}</span>
        </Pill>

        <Pill icon={FiEye} title="Viewers watching now">
          <span className={num}>{(a.viewers ?? 0).toLocaleString()}</span>
        </Pill>

        <Pill icon={FiTrendingUp} title="Peak concurrent viewers this broadcast" className="hidden sm:flex">
          <span className={num}>{(a.peak_viewers ?? 0).toLocaleString()}</span>
        </Pill>

        {/* Roster split — speakers / moderators / everyone connected. */}
        <Pill icon={FiUsers} title="On stage · moderators · total connected" className="hidden md:flex">
          <span className={small}>
            <span className="text-emerald-600 dark:text-emerald-400">{a.speakers ?? 0}</span>
            <span className="text-slate-400"> / </span>
            <span className="text-blue-600 dark:text-blue-400">{a.moderators ?? 0}</span>
            <span className="text-slate-400"> / </span>
            <span className="text-slate-900 dark:text-white">{a.participants ?? 0}</span>
          </span>
        </Pill>

        {a.hands > 0 && (
          <Pill icon={FiMic} title={`${a.hands} raised hand(s)`} tone="text-amber-500">
            <span className={cx(small, "text-amber-600 dark:text-amber-400")}>{a.hands} ✋</span>
          </Pill>
        )}

        {/* Capture: measured from the actual MediaStreamTrack, not the requested target. */}
        <Pill
          icon={FiMonitor}
          title={resolution
            ? `Capture actually granted by the camera: ${resolution} @ ${media.actual.frameRate ?? "?"}fps (target ${broadcast?.settings?.resolution ?? "—"})`
            : "Start the preview to read the real capture resolution and frame rate"}
          className="hidden xl:flex"
        >
          <span className={cx(small, "text-slate-700 dark:text-slate-200")}>
            {resolution ? `${resolution}${media.actual.frameRate ? ` @${media.actual.frameRate}` : ""}` : "—"}
          </span>
        </Pill>

        <Pill
          icon={FiActivity}
          tone={latencyTone(latency)}
          title="Round-trip latency to the control server (target under 200ms). Encoder/stream bitrate needs a publishing peer connection, which isn't wired yet."
        >
          <span className={cx(small, latencyTone(latency))}>{latency == null ? "—" : `${latency} ms`}</span>
        </Pill>

        <Pill
          icon={net ? FiWifi : FiWifiOff}
          tone={TONE_TEXT[netTone]}
          title={net
            ? `Network: ${net.effectiveType || "unknown"}${net.downlinkMbps != null ? ` · ~${net.downlinkMbps} Mbps down` : ""}${net.rttMs != null ? ` · ${net.rttMs}ms RTT` : ""}`
            : "This browser doesn't expose the Network Information API"}
          className="hidden lg:flex"
        >
          <span className={cx(small, TONE_TEXT[netTone])}>{net?.effectiveType?.toUpperCase() || "—"}</span>
        </Pill>

        {/* "Load" is measured main-thread frame budget, NOT OS CPU — no browser exposes
            system CPU to a page, and the tooltip says exactly that. */}
        <Pill
          icon={FiCpu}
          tone={TONE_TEXT[meterTone(stats.load)]}
          title={stats.load == null
            ? "UI load unavailable"
            : `Console UI load ${stats.load}% (main-thread frame time ${stats.frameMs}ms${stats.cores ? `, ${stats.cores} cores` : ""}). Browsers can't report system CPU.`}
          className="hidden xl:flex"
        >
          <span className={cx(small, TONE_TEXT[meterTone(stats.load)])}>
            {stats.load == null ? "—" : `${stats.load}%`}
          </span>
        </Pill>

        <Pill
          icon={FiHardDrive}
          tone={TONE_TEXT[meterTone(stats.memory?.percent)]}
          title={stats.memory
            ? `JS heap ${stats.memory.usedMb}MB of ${stats.memory.limitMb}MB. This is the tab's memory, not system RAM.`
            : "Memory reporting is Chromium-only (performance.memory)"}
          className="hidden xl:flex"
        >
          <span className={cx(small, TONE_TEXT[meterTone(stats.memory?.percent)])}>
            {stats.memory ? `${stats.memory.percent}%` : "—"}
          </span>
        </Pill>

        <Pill
          icon={conn.icon}
          tone={cx(conn.tone, conn.spin && "animate-spin motion-reduce:animate-none")}
          title={attempt > 0 ? `${conn.label} — auto-reconnect attempt ${attempt}` : conn.label}
        >
          <span className={cx(small, conn.tone)}>
            {conn.label}{attempt > 0 && connection !== "open" ? ` ·${attempt}` : ""}
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
        <Link to="/organization/dashboard" className="hidden text-slate-400 hover:text-rose-500 sm:block" aria-label="Exit studio" title="Exit studio">
          <FiLogOut className="text-xl" />
        </Link>
      </div>
    </header>
  );
}
