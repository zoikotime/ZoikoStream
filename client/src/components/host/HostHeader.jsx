// client/src/components/host/HostHeader.jsx
// Producer header: event identity, broadcast state, and the instrument cluster — viewers,
// peak, roster split, elapsed, recording, health, latency, network, memory and UI load.
//
// Every number here has a real source. The ones this stack cannot measure from a browser
// (OS CPU, encoder bitrate) render "—" with a tooltip saying why, because a broadcast
// console that invents telemetry is worse than one that admits the gap.
//
// LAYOUT: the readouts are grouped into four CLUSTERS — audience, room, signal,
// diagnostics — each a single bordered track with internal dividers. Ten individually
// pilled numbers read as ten competing objects; four tracks read as one instrument panel,
// which is the difference between a dashboard an operator scans and one they have to parse.
// Clusters drop out right-to-left as width tightens (diagnostics first, audience last), so
// what survives on a laptop is what an operator actually needs.
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  FiSun, FiMoon, FiLogOut, FiClock, FiEye, FiUsers, FiTrendingUp, FiMic,
  FiShield, FiVideo, FiActivity, FiWifi, FiWifiOff, FiRefreshCw, FiCpu,
  FiHardDrive, FiMonitor, FiCheckCircle, FiAlertTriangle, FiAlertOctagon,
} from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import useSystemStats from "../../hooks/useSystemStats";
import { useTheme } from "../../theme/ThemeContext";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";
import { STUDIO, SIGNAL, focus, t150 } from "./studio";
import {
  BROADCAST_TONE, BROADCAST_LABEL, HEALTH_TONE, HEALTH_LABEL, NETWORK_TONE, meterTone,
} from "../../data/host";

const pad = (n) => String(n).padStart(2, "0");
const fmtElapsed = (s) =>
  `${Math.floor(s / 3600) > 0 ? `${Math.floor(s / 3600)}:` : ""}${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;

// Connection state carries a LABEL as well as a colour — status must never be conveyed by
// hue alone (a red and a green wifi glyph are the same glyph to a colourblind operator).
const CONNECTION = {
  open: { icon: FiWifi, label: "Connected", tone: SIGNAL.good },
  connecting: { icon: FiRefreshCw, label: "Connecting", tone: SIGNAL.warn, spin: true },
  reconnecting: { icon: FiRefreshCw, label: "Reconnecting", tone: SIGNAL.warn, spin: true },
  offline: { icon: FiWifiOff, label: "Offline", tone: SIGNAL.bad },
  unauthorized: { icon: FiWifiOff, label: "Not authorized", tone: SIGNAL.bad },
};

// Health carries a glyph keyed to the LEVEL, like CONNECTION above. It used to be FiHeart at
// every level — Feather's heart is the romantic ♡, so a degraded broadcast rendered as a "like"
// next to the word "At risk", and a single glyph told an operator nothing the label didn't.
// FiActivity is deliberately not reused here: it is already the UI-load readout below, and one
// glyph meaning two things on the same bar is worse than no glyph.
const HEALTH_ICON = { ok: FiCheckCircle, warn: FiAlertTriangle, down: FiAlertOctagon };

const latencyTone = (ms) =>
  ms == null ? SIGNAL.neutral : ms < 200 ? SIGNAL.good : ms < 600 ? SIGNAL.warn : SIGNAL.bad;

const TONE_TEXT = {
  success: SIGNAL.good,
  warning: SIGNAL.warn,
  danger: SIGNAL.bad,
  neutral: SIGNAL.neutral,
};

// ── instrument cluster primitives ─────────────────────────────────────────────
// One bordered track holding N readouts, split by hairlines. `items-stretch` + `divide-x`
// means the dividers run the full height of the track with no per-item padding maths.
function Cluster({ className = "", children }) {
  return (
    <div
      className={cx(
        "flex items-stretch divide-x overflow-hidden",
        STUDIO.inset,
        STUDIO.divideX,
        className
      )}
    >
      {children}
    </div>
  );
}

// One readout. `title` is the honest explanation of the number (and of the "—" when there
// isn't one); `srLabel` names it for a screen reader, since the visual label is an icon.
function Readout({ icon: Icon, title, srLabel, tone, spin = false, children, className = "" }) {
  return (
    <div
      title={title}
      className={cx("flex shrink-0 items-center gap-1.5 px-2.5 py-1.5", className)}
    >
      <Icon
        aria-hidden="true"
        className={cx(
          "shrink-0 text-[13px]",
          tone || STUDIO.faint,
          spin && "animate-spin motion-reduce:animate-none"
        )}
      />
      <span className="sr-only">{srLabel}: </span>
      {children}
    </div>
  );
}

const val = cx("text-[13px] font-semibold leading-none tabular-nums", STUDIO.heading);
const valSm = "text-[13px] font-semibold leading-none tabular-nums";

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
    <header
      className={cx(
        "flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b px-3 py-2 sm:px-4",
        STUDIO.chrome
      )}
    >
      {/* ── identity ─────────────────────────────────────────────────────────── */}
      <Link
        to="/organization/dashboard"
        className={cx("shrink-0 rounded-lg", focus)}
        title="Back to the organization dashboard"
      >
        <Logo height="h-6">
          <span className={cx("hidden sm:block", STUDIO.eyebrow, STUDIO.muted)}>
            Producer&nbsp;Console
          </span>
        </Logo>
      </Link>

      <div className={cx("min-w-0 border-l pl-3", STUDIO.divider)}>
        <p className={cx("truncate text-[15px] font-semibold leading-tight", STUDIO.heading)}>
          {event?.name || "Live event"}
        </p>
        <p className={cx("truncate text-[11px] leading-tight", STUDIO.muted)}>
          {event?.host ? `Hosted by ${event.host}` : "No host assigned"}
        </p>
      </div>

      {/* ── broadcast state ──────────────────────────────────────────────────── */}
      {/* No on-air badge here: the monitor carries its own saturated ON AIR chip
          (StudioStage's STATE_CHIP), and two of them in one viewport is one signal too many.
          The non-live states still show, because the monitor can be scrolled or expanded away
          while this header is always visible — so "preview"/"paused"/"ended" is the one piece
          of broadcast state that would otherwise have nowhere to appear. */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        {!live && (
          <Badge tone={BROADCAST_TONE[status]} dot>{BROADCAST_LABEL[status] || status}</Badge>
        )}

        {recording && (
          <Badge
            tone={recording.status === "paused" ? "warning" : "danger"}
            dot
            title={recording.enforced
              ? `Recording ${recording.status} · ${recording.quality || ""}`
              : "Recording state is tracked, but LiveKit egress isn't capturing a file"}
          >
            <FiVideo aria-hidden="true" /> {recording.status === "paused" ? "Rec paused" : "Rec"}
            {!recording.enforced && " · not captured"}
          </Badge>
        )}

        {recovering && (
          <Badge tone="danger" dot title="The media room dropped. The broadcast is held open for the publisher to reconnect.">
            Auto-recovery
          </Badge>
        )}

        {health && (() => {
          const HealthIcon = HEALTH_ICON[health.level] || FiAlertTriangle;
          return (
            <Badge
              tone={HEALTH_TONE[health.level]}
              dot
              className="hidden xl:inline-flex"
              // The reasons ARE the useful part of a non-ok verdict, so they lead the tooltip
              // instead of sitting behind a generic label.
              title={
                health.issues?.length
                  ? `${HEALTH_LABEL[health.level] || health.level}: ${health.issues.join(" · ")}`
                  : "All broadcast signals normal"
              }
            >
              <HealthIcon aria-hidden="true" /> {HEALTH_LABEL[health.level] || health.level}
            </Badge>
          );
        })()}

        {!canHost && (
          <Badge tone="warning" dot title="You aren't assigned as host, so broadcast controls are disabled">
            <FiShield aria-hidden="true" /> View only
          </Badge>
        )}
      </div>

      {/* ── instrument clusters ──────────────────────────────────────────────── */}
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {/* Audience — the numbers a producer watches continuously. Never hidden. */}
        <Cluster>
          <Readout
            icon={FiClock}
            srLabel="Elapsed live time"
            title="Live time, excluding paused periods"
          >
            <span className={val}>{startedAt ? fmtElapsed(elapsed) : "—"}</span>
          </Readout>
          <Readout icon={FiEye} srLabel="Viewers now" title="Viewers watching now">
            <span className={val}>{(a.viewers ?? 0).toLocaleString()}</span>
          </Readout>
          <Readout
            icon={FiTrendingUp}
            srLabel="Peak viewers"
            title="Peak concurrent viewers this broadcast"
            className="hidden sm:flex"
          >
            <span className={val}>{(a.peak_viewers ?? 0).toLocaleString()}</span>
          </Readout>
        </Cluster>

        {/* Room — who is in it. Roster split is stage / moderators / total connected. */}
        <Cluster className="hidden md:flex">
          <Readout
            icon={FiUsers}
            srLabel="On stage, moderators, total connected"
            title="On stage · moderators · total connected"
          >
            <span className={valSm}>
              <span className="text-green-600 dark:text-green-400">{a.speakers ?? 0}</span>
              <span className={STUDIO.faint}> / </span>
              <span className="text-blue-600 dark:text-blue-400">{a.moderators ?? 0}</span>
              <span className={STUDIO.faint}> / </span>
              <span className={STUDIO.heading}>{a.participants ?? 0}</span>
            </span>
          </Readout>
          {a.hands > 0 && (
            <Readout
              icon={FiMic}
              srLabel="Raised hands"
              title={`${a.hands} raised hand(s)`}
              tone={SIGNAL.warn}
            >
              <span className={cx(valSm, SIGNAL.warn)}>{a.hands} raised</span>
            </Readout>
          )}
        </Cluster>

        {/* Signal — the health of this console's own link to the event. */}
        <Cluster>
          <Readout
            icon={FiActivity}
            srLabel="Control-server latency"
            tone={latencyTone(latency)}
            title="Round-trip latency to the control server (target under 200ms). Encoder/stream bitrate needs a publishing peer connection, which isn't wired yet."
          >
            <span className={cx(valSm, latencyTone(latency))}>
              {latency == null ? "—" : `${latency} ms`}
            </span>
          </Readout>
          <Readout
            icon={net ? FiWifi : FiWifiOff}
            srLabel="Network type"
            tone={TONE_TEXT[netTone]}
            title={net
              ? `Network: ${net.effectiveType || "unknown"}${net.downlinkMbps != null ? ` · ~${net.downlinkMbps} Mbps down` : ""}${net.rttMs != null ? ` · ${net.rttMs}ms RTT` : ""}`
              : "This browser doesn't expose the Network Information API"}
            className="hidden lg:flex"
          >
            <span className={cx(valSm, TONE_TEXT[netTone])}>
              {net?.effectiveType?.toUpperCase() || "—"}
            </span>
          </Readout>
          <Readout
            icon={conn.icon}
            srLabel="Control connection"
            tone={conn.tone}
            spin={conn.spin}
            title={attempt > 0 ? `${conn.label} — auto-reconnect attempt ${attempt}` : conn.label}
          >
            <span className={cx(valSm, conn.tone)}>
              {conn.label}{attempt > 0 && connection !== "open" ? ` ·${attempt}` : ""}
            </span>
          </Readout>
        </Cluster>

        {/* Diagnostics — real but secondary. First cluster to go as width tightens.
            "Capture" is measured from the actual MediaStreamTrack, not the requested
            target; "Load" is main-thread frame budget, NOT OS CPU (no browser exposes
            system CPU to a page) — both tooltips say so rather than implying otherwise. */}
        <Cluster className="hidden 2xl:flex">
          <Readout
            icon={FiMonitor}
            srLabel="Granted capture resolution"
            title={resolution
              ? `Capture actually granted by the camera: ${resolution} @ ${media.actual.frameRate ?? "?"}fps (target ${broadcast?.settings?.resolution ?? "—"})`
              : "Start the preview to read the real capture resolution and frame rate"}
          >
            <span className={cx(valSm, STUDIO.body)}>
              {resolution ? `${resolution}${media.actual.frameRate ? ` @${media.actual.frameRate}` : ""}` : "—"}
            </span>
          </Readout>
          <Readout
            icon={FiCpu}
            srLabel="Console UI load"
            tone={TONE_TEXT[meterTone(stats.load)]}
            title={stats.load == null
              ? "UI load unavailable"
              : `Console UI load ${stats.load}% (main-thread frame time ${stats.frameMs}ms${stats.cores ? `, ${stats.cores} cores` : ""}). Browsers can't report system CPU.`}
          >
            <span className={cx(valSm, TONE_TEXT[meterTone(stats.load)])}>
              {stats.load == null ? "—" : `${stats.load}%`}
            </span>
          </Readout>
          <Readout
            icon={FiHardDrive}
            srLabel="Tab memory"
            tone={TONE_TEXT[meterTone(stats.memory?.percent)]}
            title={stats.memory
              ? `JS heap ${stats.memory.usedMb}MB of ${stats.memory.limitMb}MB. This is the tab's memory, not system RAM.`
              : "Memory reporting is Chromium-only (performance.memory)"}
          >
            <span className={cx(valSm, TONE_TEXT[meterTone(stats.memory?.percent)])}>
              {stats.memory ? `${stats.memory.percent}%` : "—"}
            </span>
          </Readout>
        </Cluster>

        {/* ── console actions ────────────────────────────────────────────────── */}
        <div className={cx("flex items-center gap-1 border-l pl-2", STUDIO.divider)}>
          <button
            type="button"
            onClick={toggle}
            className={cx(
              "grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white",
              t150,
              focus
            )}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          >
            {theme === "dark" ? <FiSun className="text-base" /> : <FiMoon className="text-base" />}
          </button>
          <Link
            to="/organization/dashboard"
            className={cx(
              "grid h-8 w-8 place-items-center rounded-lg text-slate-500 hover:bg-rose-50 hover:text-rose-600 dark:text-slate-400 dark:hover:bg-rose-500/10 dark:hover:text-rose-400",
              t150,
              focus
            )}
            aria-label="Exit studio"
            title="Exit studio"
          >
            <FiLogOut className="text-base" />
          </Link>
        </div>
      </div>
    </header>
  );
}
