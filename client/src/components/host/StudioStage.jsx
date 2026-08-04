// client/src/components/host/StudioStage.jsx
// The broadcast monitor: a REAL camera preview (native getUserMedia — see
// hooks/useMediaPreview), live overlays, the go-live countdown, and a filmstrip built from
// live presence rather than a fixed list.
//
// What the preview is and isn't: it renders this machine's actual camera and mic, so the
// resolution/frame-rate readout is measured, not echoed back. It is NOT yet published to
// LiveKit — that needs livekit-client — and the banner says so instead of implying that
// viewers can see it.
import { useEffect, useState } from "react";
import { FiEye, FiMic, FiMicOff, FiAlertTriangle, FiUploadCloud } from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import useStageFeeds from "../../hooks/useStageFeeds";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import VideoGrid from "./VideoGrid";

const pad = (n) => String(n).padStart(2, "0");
const fmt = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;

const chip = "inline-flex items-center gap-1.5 rounded-md bg-black/45 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur";

// Recording elapsed comes from the server's timestamps (minus paused time), so it matches
// the recording log rather than counting from when this tab happened to open.
function RecordingTimer({ recording }) {
  const started = recording.started_at ? new Date(recording.started_at).getTime() : null;
  const frozen = recording.status === "paused" && recording.paused_at
    ? new Date(recording.paused_at).getTime() : null;
  const calc = () => (started
    ? Math.max(0, Math.floor(((frozen || Date.now()) - started - (recording.paused_ms || 0)) / 1000))
    : 0);
  const [secs, setSecs] = useState(calc);
  useInterval(() => setSecs(calc()), 1000, !!started && recording.status === "recording");
  return <span className="tabular-nums">{fmt(secs)}</span>;
}

// Shared go-live countdown: every console counts down to ONE server deadline, so the host
// and the moderators see the same number.
function Countdown({ until, onDone }) {
  const target = new Date(until).getTime();
  const [left, setLeft] = useState(() => Math.max(0, Math.ceil((target - Date.now()) / 1000)));
  useInterval(() => setLeft(Math.max(0, Math.ceil((target - Date.now()) / 1000))), 250, left > 0);
  useEffect(() => {
    if (left <= 0) onDone?.();
  }, [left, onDone]);
  if (left <= 0) return null;
  return (
    <div className="absolute inset-0 z-10 grid place-items-center bg-slate-950/70 backdrop-blur-sm">
      <div className="text-center">
        <p className="text-xs font-semibold uppercase tracking-widest text-slate-300">Going live in</p>
        <p key={left} className="text-7xl font-bold tabular-nums text-white motion-safe:animate-[zk-fade-in_.3s]">{left}</p>
      </div>
    </div>
  );
}

export default function StudioStage({
  broadcast, recording, analytics, participants, media, camera, mic,
  countdownUntil, onCountdownDone, publisher, layout, pinnedIdentity, onPin,
}) {
  const { actual, active: previewActive, error: mediaError, stream } = media;
  const status = broadcast?.status || "preview";
  const live = status === "live";
  const viewers = analytics?.viewers ?? 0;
  const publishing = publisher?.publishing;
  const stats = publisher?.stats;

  // The stage roster. Shared with the speaker console via hooks/useStageFeeds so the two never
  // disagree about which tile is whose.
  const feeds = useStageFeeds({ participants, publisher, stream, previewActive, camera, mic });

  return (
    <div className="space-y-4">
      <div className="relative w-full overflow-hidden rounded-2xl ring-1 ring-slate-200 dark:ring-slate-800">
        <VideoGrid
          layout={layout}
          pinnedIdentity={pinnedIdentity}
          feeds={feeds}
          onPin={onPin}
        />

        {countdownUntil && <Countdown until={countdownUntil} onDone={onCountdownDone} />}

        {/* Top overlays */}
        <div className="absolute inset-x-0 top-0 z-[1] flex items-start justify-between gap-2 p-4">
          <span
            className={cx(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-bold tracking-wide",
              live ? "bg-rose-600 text-white"
                : status === "paused" ? "bg-amber-500 text-white"
                  : "bg-white/10 text-slate-200 backdrop-blur"
            )}
          >
            {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white motion-reduce:animate-none" />}
            {live ? "LIVE" : status === "paused" ? "PAUSED" : status === "ended" ? "ENDED" : "PREVIEW"}
          </span>

          <div className="flex flex-wrap items-center justify-end gap-2">
            {recording && (
              <span className={chip} title={recording.enforced ? "Capturing to file" : "Timer only — LiveKit egress isn't capturing"}>
                <span className={cx("h-2 w-2 rounded-full bg-rose-500", recording.status === "recording" && "animate-pulse motion-reduce:animate-none")} />
                REC <RecordingTimer recording={recording} />
                {!recording.enforced && <FiAlertTriangle className="text-amber-400" title="Not captured" />}
              </span>
            )}
            <span className={chip}><FiEye /> {viewers.toLocaleString()}</span>
          </div>
        </div>

        {/* The publisher's REAL condition. Every branch reports something measured — a
            broadcast console that says "live" while nothing is being sent is the one failure a
            host cannot recover from, because they find out from the audience. */}
        {(mediaError || publisher?.error || (live && !publishing)) && (
          <div className="pointer-events-none absolute inset-x-0 top-14 z-[2] flex justify-center px-4">
            <p className="inline-flex max-w-lg items-center gap-2 rounded-lg bg-rose-600/95 px-3 py-2 text-xs font-medium text-white shadow-lg">
              <FiAlertTriangle className="shrink-0" aria-hidden="true" />
              {mediaError || publisher?.error
                || "You're live but no media is being published — check your camera and network."}
            </p>
          </div>
        )}

        {/* Bottom overlay: what the hardware granted, and what the encoder is actually sending. */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[1] flex flex-wrap items-end justify-between gap-2 p-4">
          <div className="min-w-0 space-y-1">
            {actual?.width && (
              <span className={chip} title="Measured from the live MediaStreamTrack">
                {actual.width}×{actual.height}
                {actual.frameRate ? ` @ ${actual.frameRate}fps` : ""}
              </span>
            )}
            {publishing && stats?.bitrateKbps != null && (
              <span
                className={chip}
                title="Measured on the peer connection: outbound bitrate, encoded frame rate, packet loss and round-trip"
              >
                <FiUploadCloud aria-hidden="true" />
                {stats.bitrateKbps} kbps
                {stats.fps != null ? ` · ${stats.fps}fps` : ""}
                {stats.packetLoss != null ? ` · ${stats.packetLoss}% loss` : ""}
                {stats.rttMs != null ? ` · ${stats.rttMs}ms` : ""}
              </span>
            )}
            {/* The honest reason a stream looks soft, straight from the encoder. */}
            {publishing && stats?.qualityLimitation && (
              <p className="text-[11px] text-amber-300/90">
                Encoder limited by {stats.qualityLimitation}.
              </p>
            )}
            {previewActive && !publishing && (
              <p className="flex items-center gap-1.5 text-[11px] text-amber-300/90">
                <FiUploadCloud aria-hidden="true" />
                Local preview only — not being sent to viewers yet.
              </p>
            )}
          </div>
          <span className={cx(chip, mic ? "" : "text-rose-300")}>
            {mic ? <FiMic /> : <FiMicOff />} {mic ? "Mic live" : "Muted"}
          </span>
        </div>
      </div>

      {analytics?.waiting > 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm dark:border-amber-500/30 dark:bg-amber-500/10">
          <Badge tone="warning" dot>{analytics.waiting} waiting</Badge>
          <span className="text-amber-800 dark:text-amber-300">
            {analytics.waiting} {analytics.waiting === 1 ? "person is" : "people are"} in the waiting room.
          </span>
        </div>
      )}
    </div>
  );
}
