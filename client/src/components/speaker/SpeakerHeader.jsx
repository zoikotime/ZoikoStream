// client/src/components/speaker/SpeakerHeader.jsx
// The speaker's instrument cluster: session name, live status, their own stage grant, the audience
// figure they are entitled to, a session timer and the connection state.
//
// Smaller than the host's HostHeader on purpose. A speaker is not producing — they need to know
// whether they are on air, whether the room can hear them, and how long they have been talking.
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  FiActivity, FiClock, FiEye, FiLogOut, FiMic, FiMoon, FiRefreshCw, FiSun,
  FiUploadCloud, FiWifi, FiWifiOff, FiAlertTriangle, FiMonitor,
} from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import { useTheme } from "../../theme/ThemeContext";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Logo from "../../ui/Logo";

const pad = (n) => String(n).padStart(2, "0");
const fmtElapsed = (s) => {
  const h = Math.floor(s / 3600);
  return `${h > 0 ? `${h}:` : ""}${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
};

const CONNECTION = {
  open: { icon: FiWifi, label: "Connected", tone: "text-emerald-600 dark:text-emerald-400" },
  connecting: { icon: FiRefreshCw, label: "Connecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  reconnecting: { icon: FiRefreshCw, label: "Reconnecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  offline: { icon: FiWifiOff, label: "Offline", tone: "text-rose-600 dark:text-rose-400" },
  unauthorized: { icon: FiWifiOff, label: "Not authorized", tone: "text-rose-600 dark:text-rose-400" },
};

const latencyTone = (ms) =>
  ms == null ? "text-slate-400" : ms < 200 ? "text-emerald-500" : ms < 600 ? "text-amber-500" : "text-rose-500";

function Pill({ icon: Icon, children, title, className = "", iconClass = "" }) {
  return (
    <div title={title}
         className={cx("flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1.5 dark:bg-slate-800", className)}>
      <Icon className={cx("shrink-0", iconClass || "text-slate-500 dark:text-slate-400")} aria-hidden="true" />
      {children}
    </div>
  );
}

const num = "text-sm font-semibold tabular-nums text-slate-900 dark:text-white";

export default function SpeakerHeader({
  event, audience, stage, speaking, publisher, connection = "connecting", latency, attempt = 0,
  canSpeak = false,
}) {
  const { theme, toggle } = useTheme();
  const live = event?.status === "live";
  const conn = CONNECTION[connection] || CONNECTION.connecting;
  const sources = stage?.sources || [];

  const startedAt = event?.started_at ? new Date(event.started_at).getTime() : null;
  const [elapsed, setElapsed] = useState(() => (startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0));
  useInterval(() => setElapsed(startedAt ? Math.floor((Date.now() - startedAt) / 1000) : 0), 1000, live);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      <Link to="/speaker/dashboard">
        <Logo height="h-7">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Speaker
          </span>
        </Logo>
      </Link>

      <div className="min-w-0 dark:border-slate-700 md:border-l md:border-slate-200 md:pl-4">
        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
          {event?.name || "Live session"}
        </p>
        <p className="hidden truncate text-xs text-slate-500 md:block dark:text-slate-400">
          {event?.host ? `Hosted by ${event.host}` : "No host assigned"}
        </p>
      </div>

      {live ? <Badge status="error" live>LIVE</Badge> : <Badge status="neutral">{event?.status || "Offline"}</Badge>}

      {event?.recording && (
        <Badge status="error" dot className="hidden sm:inline-flex" title="This session is being recorded">
          REC
        </Badge>
      )}

      {/* The single most important thing on this bar: can the room actually receive me. */}
      {canSpeak ? (
        <Badge
          status={publisher?.publishing ? "success" : stage?.on_stage ? "warning" : "neutral"}
          dot
          title={
            publisher?.publishing
              ? `Publishing: ${sources.join(", ") || "nothing"}`
              : stage?.on_stage
                ? "You're on stage but nothing is being sent yet"
                : "Waiting for the host to bring you on stage"
          }
        >
          <FiUploadCloud aria-hidden="true" />
          {publisher?.publishing ? "On air" : stage?.on_stage ? "On stage" : "Backstage"}
        </Badge>
      ) : (
        <Badge status="warning" dot title="You aren't assigned as a speaker on this event">
          View only
        </Badge>
      )}

      {stage?.on_stage && !sources.includes("screen_share") && (
        <Badge tone="neutral" size="sm" className="hidden xl:inline-flex"
               title="A host has to grant screen share for this event">
          <FiMonitor aria-hidden="true" /> no share
        </Badge>
      )}

      <div className="ml-auto flex flex-wrap items-center justify-end gap-2 sm:gap-3">
        <Pill icon={FiClock} title="Time since the session went live">
          <span className={num}>{startedAt ? fmtElapsed(elapsed) : "—"}</span>
        </Pill>

        <Pill icon={FiMic} title="How long you have held the floor — accumulated server-side"
              className="hidden sm:flex">
          <span className={num}>{speaking?.seconds ? fmtElapsed(speaking.seconds) : "—"}</span>
        </Pill>

        <Pill icon={FiEye} title="People watching now">
          <span className={num}>{(audience?.viewers ?? 0).toLocaleString()}</span>
        </Pill>

        {/* Encoder figures, present only while actually publishing — a zero here would read as a
            measurement. */}
        {publisher?.publishing && publisher.stats?.bitrateKbps != null && (
          <Pill icon={FiUploadCloud} className="hidden lg:flex"
                title="Measured on your peer connection: outbound bitrate and packet loss">
            <span className={num}>{publisher.stats.bitrateKbps} kbps</span>
            {publisher.stats.packetLoss ? (
              <span className="text-xs text-amber-500">{publisher.stats.packetLoss}% loss</span>
            ) : null}
          </Pill>
        )}

        <Pill
          icon={conn.icon}
          iconClass={cx(conn.tone, conn.spin && "animate-spin motion-reduce:animate-none")}
          title={attempt > 0 ? `${conn.label} — auto-reconnect attempt ${attempt}` : conn.label}
        >
          <span className={cx("text-xs font-semibold", conn.tone)}>
            {conn.label}{attempt > 0 && connection !== "open" ? ` ·${attempt}` : ""}
          </span>
        </Pill>

        <Pill icon={FiActivity} iconClass={latencyTone(latency)} className="hidden md:flex"
              title="Round-trip latency to the server">
          <span className={cx("text-xs font-semibold tabular-nums", latencyTone(latency))}>
            {latency == null ? "—" : `${latency} ms`}
          </span>
        </Pill>

        {publisher?.error && (
          <Pill icon={FiAlertTriangle} iconClass="text-rose-500" title={publisher.error}>
            <span className="max-w-[12rem] truncate text-xs font-semibold text-rose-600 dark:text-rose-400">
              {publisher.error}
            </span>
          </Pill>
        )}

        <button
          onClick={toggle}
          className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
          aria-label="Toggle theme"
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        >
          {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
        </button>
        <Link to="/speaker/dashboard" className="hidden text-slate-400 hover:text-rose-500 sm:block"
              aria-label="Leave session" title="Leave session">
          <FiLogOut className="text-xl" />
        </Link>
      </div>
    </header>
  );
}
