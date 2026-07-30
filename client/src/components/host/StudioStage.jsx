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
import {
  FiMonitor, FiVideoOff, FiEye, FiMic, FiMicOff, FiAlertTriangle, FiCameraOff, FiUploadCloud,
} from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { initials, accentFor, QUALITY } from "../../data/host";

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
  broadcast, recording, analytics, participants, media, screenShare, camera, mic,
  countdownUntil, onCountdownDone, publishToken,
}) {
  // Destructured so `videoRef` is a plain binding: passing the whole media bag around makes
  // every `media.*` read look like a ref access to the React hooks lint rules.
  const { videoRef, actual, active: previewActive, error: mediaError } = media;
  const status = broadcast?.status || "preview";
  const live = status === "live";
  const viewers = analytics?.viewers ?? 0;

  // Everyone actually on stage, from presence — hosts and speakers, never the audience.
  const stage = participants
    .filter((p) => !p.waiting && (p.role === "host" || p.role === "speaker" || p.on_stage))
    .slice(0, 6);

  return (
    <div className="space-y-4">
      <div className="relative aspect-video w-full overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 ring-1 ring-slate-200 dark:ring-slate-800">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_35%,rgba(139,92,246,0.18),transparent_60%)]" />

        {/* Real camera feed. Muted so the host doesn't hear themselves — monitoring their
            own mic through the speakers is a feedback loop. */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className={cx(
            "absolute inset-0 h-full w-full object-cover transition-opacity duration-300",
            previewActive && camera && !screenShare ? "opacity-100" : "opacity-0"
          )}
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

        {/* Center: screen share > live camera > reason it's dark */}
        <div className="absolute inset-0 grid place-items-center p-6 text-center">
          {screenShare ? (
            <div className="flex flex-col items-center gap-3 text-slate-300">
              <FiMonitor className="text-5xl text-emerald-400" />
              <p className="text-sm font-medium">You're sharing your screen</p>
              <p className="max-w-sm text-xs text-slate-400">
                Screen capture is controlled from the deck below; the shared surface is chosen by the browser.
              </p>
            </div>
          ) : mediaError ? (
            <div className="flex flex-col items-center gap-2 text-slate-300">
              <FiCameraOff className="text-4xl text-rose-400" />
              <p className="max-w-sm text-sm font-medium">{mediaError}</p>
            </div>
          ) : !previewActive ? (
            <div className="flex flex-col items-center gap-2 text-slate-400">
              <FiVideoOff className="text-4xl" />
              <p className="text-sm font-medium">Preview is off</p>
              <p className="text-xs">Start the preview from the deck to check your camera and mic.</p>
            </div>
          ) : !camera ? (
            <div className="flex flex-col items-center gap-2 text-slate-400">
              <FiVideoOff className="text-4xl" />
              <p className="text-sm font-medium">Your camera is off</p>
            </div>
          ) : null}
        </div>

        {/* Bottom overlay: measured capture + publish honesty banner */}
        <div className="absolute inset-x-0 bottom-0 z-[1] flex flex-wrap items-end justify-between gap-2 p-4">
          <div className="min-w-0 space-y-1">
            {actual?.width && (
              <span className={chip} title="Measured from the live MediaStreamTrack">
                {actual.width}×{actual.height}
                {actual.frameRate ? ` @ ${actual.frameRate}fps` : ""}
              </span>
            )}
            {previewActive && (
              <p className="flex items-center gap-1.5 text-[11px] text-amber-300/90">
                <FiUploadCloud aria-hidden="true" />
                {publishToken
                  ? "Local preview only — a publisher token is ready, but no media is being sent yet."
                  : "Local preview only — this feed is not being published to viewers."}
              </p>
            )}
          </div>
          <span className={cx(chip, mic ? "" : "text-rose-300")}>
            {mic ? <FiMic /> : <FiMicOff />} {mic ? "Mic live" : "Muted"}
          </span>
        </div>
      </div>

      {/* Filmstrip — live presence, so someone joining the stage appears here immediately. */}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {stage.map((p) => {
          const q = QUALITY[p.quality] || QUALITY.excellent;
          return (
            <div
              key={p.identity}
              className={cx(
                "relative aspect-video overflow-hidden rounded-xl bg-slate-800 ring-1 transition motion-safe:animate-[zk-fade-in_.25s]",
                p.speaking ? "ring-2 ring-emerald-500" : "ring-slate-200 dark:ring-slate-800"
              )}
            >
              <div className="absolute inset-0 grid place-items-center">
                <span className={cx("grid h-10 w-10 place-items-center rounded-full text-sm font-semibold", ACCENT[accentFor(p.identity)].chip)}>
                  {initials(p.name)}
                </span>
              </div>
              <span className={cx("absolute right-1.5 top-1.5 h-2 w-2 rounded-full", q.tone)} title={`Connection: ${q.label}`} />
              <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-gradient-to-t from-black/75 to-transparent px-2 py-1">
                <span className="truncate text-[11px] font-medium text-white">{(p.name || "?").split(" ")[0]}</span>
                {p.muted ? <FiMicOff className="shrink-0 text-rose-400" /> : <FiMic className="shrink-0 text-emerald-400" />}
              </div>
            </div>
          );
        })}
        {stage.length === 0 && (
          <div className="col-span-full rounded-xl border border-dashed border-slate-300 py-6 text-center text-sm text-slate-400 dark:border-slate-700">
            Nobody is on stage yet — invite a speaker from the People panel.
          </div>
        )}
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
