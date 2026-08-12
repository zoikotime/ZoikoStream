// client/src/components/host/StudioStage.jsx
// The broadcast monitor: a REAL camera preview (native getUserMedia — see
// hooks/useMediaPreview), live overlays and the go-live countdown.
// The on-stage thumbnail strip that used to sit under the monitor is gone: it duplicated
// the People panel's roster, so presence is now surfaced in exactly one place.
//
// What the preview is and isn't: it renders this machine's actual camera and mic, so the
// resolution/frame-rate readout is measured, not echoed back. Once live, hooks/useLiveKitPublish
// actually publishes these same tracks to viewers — `isPublishing` (that hook's `connected`)
// is what switches the banner from "local preview only" to a real confirmation, so the host
// is never left staring at a stale "not sending" warning while they're already on air.
//
// VISUAL TREATMENT: flat slate-950 with a single inset vignette, not a tinted gradient. A
// broadcast monitor must not colour-cast the picture it is monitoring, so the old violet
// radial glow is gone. Overlay rows sit on gradient scrims so chips stay legible over any
// frame without needing opaque boxes, and the stage is height-capped so the control deck
// and KPI row are always reachable on a laptop without scrolling.
import { useEffect, useState } from "react";
import {
  FiMonitor, FiVideoOff, FiEye, FiMic, FiMicOff, FiAlertTriangle, FiCameraOff, FiUploadCloud,
  FiPlay, FiMaximize, FiMinimize, FiX,
} from "react-icons/fi";
import useInterval from "../../hooks/useInterval";
import { cx } from "../../ui/tokens";
import { STUDIO, focusOnStage, t150 } from "./studio";

const pad = (n) => String(n).padStart(2, "0");
const fmt = (s) => `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;

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
    <div className="absolute inset-0 z-20 grid place-items-center bg-slate-950/80 backdrop-blur-sm">
      <div className="text-center">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-slate-300">
          Going live in
        </p>
        <p
          key={left}
          className="mt-2 text-7xl font-semibold tabular-nums leading-none text-white motion-safe:animate-[zk-fade-in_.3s]"
        >
          {left}
        </p>
      </div>
    </div>
  );
}

// The status word in the top-left. On-air is the only saturated fill on the stage.
const STATE_CHIP = {
  live: "bg-rose-600 text-white",
  paused: "bg-amber-500 text-white",
  ended: "bg-slate-700 text-slate-100",
  preview: "bg-slate-950/70 text-slate-200 ring-1 ring-white/15 backdrop-blur-sm",
};
const STATE_LABEL = { live: "On air", paused: "Paused", ended: "Ended", preview: "Preview" };

export default function StudioStage({
  broadcast, recording, analytics, media, screenShare, screenVideoRef, camera, mic,
  countdownUntil, onCountdownDone, publishToken, isPublishing, isReconnecting, publishError,
  onTogglePreview,
}) {
  // Destructured so `videoRef` is a plain binding: passing the whole media bag around makes
  // every `media.*` read look like a ref access to the React hooks lint rules.
  const { videoRef, actual, active: previewActive, error: mediaError } = media;
  const status = broadcast?.status || "preview";
  const live = status === "live";
  const viewers = analytics?.viewers ?? 0;

  // Fullscreen is a CSS state change on the EXISTING monitor node, never a re-parent into a
  // portal. hooks/useMediaPreview assigns `videoRef.current.srcObject` imperatively inside
  // the effect that acquires the stream, so if React unmounted and remounted this <video>
  // the effect would not re-run and the host's feed would go black. Toggling classNames on
  // a node that never leaves the tree keeps the MediaStream attached.
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setExpanded(false); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [expanded]);

  return (
    // Flex column so the monitor can absorb the leftover height instead of dictating it.
    <div className="flex min-h-0 flex-col gap-2 lg:h-full">
      {/* ── monitor ───────────────────────────────────────────────────────────── */}
      {/* The stage PANEL spans the full width of the workspace and takes whatever height the
          KPI row, filmstrip and alerts leave behind — the monitor-bay shape in the reference.
          A raw 16:9 panel is 838px tall at 1490px of main column and buries the filmstrip and
          deck, so height is still capped by the flex row rather than by the ratio; what
          changed is that the panel no longer derives its WIDTH from that height, which used
          to leave page-coloured gutters either side of the frame.
          The CAMERA's own ratio is untouched and still `object-contain` (see the <video>
          below): it is letterboxed inside the panel against the same slate-950, so the feed
          stays complete and uncropped and simply sits centred in a wider bay.
          Below lg the page scrolls instead, so `aspect-video w-full` sizes the panel. */}
      <div
        className={cx(
          "flex min-w-0",
          expanded ? "lg:flex-none" : "lg:min-h-0 lg:flex-1"
        )}
      >
      <div
        className={cx(
          // `position` is set in EXACTLY ONE branch. Listing `relative` in a shared base and
          // `fixed` in the expanded branch does not work: Tailwind emits `.relative` AFTER
          // `.fixed` in its stylesheet, so `relative` wins regardless of attribute order and
          // the "fullscreen" view stays boxed inside the main column.
          "overflow-hidden",
          STUDIO.stage,
          expanded
            // Fullscreen: same node, viewport-anchored via inset-0 (not w-screen/h-screen —
            // 100vw includes the scrollbar and would force horizontal overflow). z-50 sits
            // below ui/Overlay's z-[60], so opening Settings or Invite while expanded still
            // layers on top correctly instead of disappearing behind the video.
            ? "fixed inset-0 z-50"
            : "relative aspect-video w-full shrink-0 rounded-xl ring-1 ring-slate-300 lg:aspect-auto lg:h-full lg:min-h-[180px] dark:ring-slate-800"
        )}
      >
        <div className={cx("pointer-events-none absolute inset-0 z-[1]", STUDIO.stageVignette)} />

        {/* Real camera feed. Muted so the host doesn't hear themselves — monitoring their
            own mic through the speakers is a feedback loop.
            object-CONTAIN, not -cover: a producer is checking their own framing here, and
            `cover` crops whatever doesn't match the frame — it was cutting the host's head
            and shoulders out of the shot they were trying to verify. Contain shows the whole
            sensor output; any letterbox falls on the same slate-950 as the stage, so it reads
            as a clean monitor rather than as bars. */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          className={cx(
            "absolute inset-0 h-full w-full object-contain transition-opacity duration-300",
            previewActive && camera && !screenShare ? "opacity-100" : "opacity-0"
          )}
        />

        {/* Real screen-share feed (hooks/useLiveKitPublish swaps this in for the camera
            track once live). object-contain, not -cover: cropping a shared screen can cut
            off exactly the content the host meant to show. */}
        <video
          ref={screenVideoRef}
          autoPlay
          playsInline
          muted
          className={cx(
            "absolute inset-0 h-full w-full bg-black object-contain transition-opacity duration-300",
            screenShare ? "opacity-100" : "opacity-0"
          )}
        />

        {countdownUntil && <Countdown until={countdownUntil} onDone={onCountdownDone} />}

        {/* ── top overlay ─────────────────────────────────────────────────────── */}
        <div className="pointer-events-none absolute inset-x-0 top-0 z-[2]">
          <div className={cx("h-20", STUDIO.scrimTop)} />
          <div className="absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-3">
            <span
              className={cx(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-bold uppercase leading-none tracking-wide",
                STATE_CHIP[status] || STATE_CHIP.preview
              )}
            >
              {live && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white motion-reduce:animate-none" />
              )}
              {STATE_LABEL[status] || status}
            </span>

            <div className="flex flex-wrap items-center justify-end gap-1.5">
              {screenShare && (
                <span className={STUDIO.chip}>
                  <FiMonitor aria-hidden="true" className="text-green-400" /> Sharing screen
                </span>
              )}
              {recording && (
                <span
                  className={STUDIO.chip}
                  title={recording.enforced
                    ? "Capturing to file"
                    : "Timer only — LiveKit egress isn't capturing"}
                >
                  <span
                    aria-hidden="true"
                    className={cx(
                      "h-1.5 w-1.5 rounded-full bg-rose-500",
                      recording.status === "recording" && "animate-pulse motion-reduce:animate-none"
                    )}
                  />
                  REC <RecordingTimer recording={recording} />
                  {!recording.enforced && (
                    <FiAlertTriangle className="text-amber-400" title="Not captured" />
                  )}
                </span>
              )}
              <span className={STUDIO.chip} title="Viewers watching now">
                <FiEye aria-hidden="true" />
                <span className="tabular-nums">{viewers.toLocaleString()}</span>
              </span>

              {/* Second, unmissable way out of fullscreen (Esc and the corner button are the
                  others) — a labelled button, because an icon in a corner is not an obvious
                  exit when the picture fills the whole screen. */}
              {expanded && (
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  className={cx(
                    "pointer-events-auto inline-flex items-center gap-1.5 rounded-md bg-slate-950/75 px-2 py-1 text-[11px] font-semibold leading-none text-white ring-1 ring-white/15 backdrop-blur-sm hover:bg-slate-900 hover:ring-white/30",
                    t150,
                    focusOnStage
                  )}
                  title="Exit fullscreen (Esc)"
                >
                  <FiX aria-hidden="true" /> Exit fullscreen
                </button>
              )}
            </div>
          </div>
        </div>

        {/* ── centre: why the feed is dark ────────────────────────────────────── */}
        {/* Never shown while screen share has a real video on screen above.
            pointer-events-none so this full-bleed layer can't swallow interaction with the
            video beneath it once the preview is running; the CTA re-enables its own. */}
        <div className="pointer-events-none absolute inset-0 z-[2] grid place-items-center p-6 text-center">
          {screenShare ? null : mediaError ? (
            <div className="flex max-w-sm flex-col items-center gap-2.5">
              <span className="grid h-12 w-12 place-items-center rounded-full bg-rose-500/10 ring-1 ring-rose-500/30">
                <FiCameraOff aria-hidden="true" className="text-xl text-rose-400" />
              </span>
              <p className="text-sm font-semibold text-white">Camera unavailable</p>
              <p className="text-xs leading-relaxed text-slate-400">{mediaError}</p>
            </div>
          ) : !previewActive ? (
            // Deliberate empty state, not an unfinished one: it names the state, says why
            // it matters, and offers the one action that resolves it — the same
            // togglePreview handler the deck's Preview button already calls.
            <div className="flex max-w-sm flex-col items-center gap-3">
              <span className="grid h-12 w-12 place-items-center rounded-full bg-white/[0.04] ring-1 ring-white/10">
                <FiVideoOff aria-hidden="true" className="text-xl text-slate-400" />
              </span>
              <div>
                <p className="text-sm font-semibold text-white">Preview is off</p>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">
                  Check your camera and microphone here before anyone else can see you.
                </p>
              </div>
              {onTogglePreview && (
                <button
                  type="button"
                  onClick={onTogglePreview}
                  className={cx(
                    "pointer-events-auto inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-500",
                    t150,
                    focusOnStage
                  )}
                >
                  <FiPlay aria-hidden="true" /> Start preview
                </button>
              )}
            </div>
          ) : !camera ? (
            <div className="flex flex-col items-center gap-2.5">
              <span className="grid h-12 w-12 place-items-center rounded-full bg-white/[0.04] ring-1 ring-white/10">
                <FiVideoOff aria-hidden="true" className="text-xl text-slate-400" />
              </span>
              <p className="text-sm font-semibold text-white">Your camera is off</p>
              <p className="text-xs text-slate-400">Your microphone is still {mic ? "live" : "muted"}.</p>
            </div>
          ) : null}
        </div>

        {/* ── bottom overlay: measured capture + publish honesty banner ───────── */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[2]">
          <div className={cx("h-24", STUDIO.scrimBottom)} />
          <div className="absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-2 p-3">
            <div className="flex min-w-0 flex-col items-start gap-1.5">
              {actual?.width && (
                <span className={STUDIO.chip} title="Measured from the live MediaStreamTrack">
                  <span className="tabular-nums">
                    {actual.width}×{actual.height}
                    {actual.frameRate ? ` @ ${actual.frameRate}fps` : ""}
                  </span>
                </span>
              )}

              {/* Publish state, in the operator's words. Every branch is a real condition
                  reported by useLiveKitPublish — none of them is a placeholder. */}
              {previewActive && isPublishing && (
                <p className="flex items-center gap-1.5 text-[11px] font-medium text-green-300">
                  <FiUploadCloud aria-hidden="true" />
                  Live — this feed is being published to viewers.
                </p>
              )}
              {previewActive && !isPublishing && isReconnecting && (
                <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-300">
                  <FiAlertTriangle aria-hidden="true" />
                  Connection dropped — reconnecting to viewers…
                </p>
              )}
              {previewActive && !isPublishing && !isReconnecting && publishError && (
                <p className="flex items-center gap-1.5 text-[11px] font-medium text-rose-300">
                  <FiAlertTriangle aria-hidden="true" />
                  Not publishing — {publishError}
                </p>
              )}
              {previewActive && !isPublishing && !isReconnecting && !publishError && (
                <p className="flex items-center gap-1.5 text-[11px] font-medium text-amber-300/90">
                  <FiUploadCloud aria-hidden="true" />
                  {live
                    ? "Connecting the publisher — this feed will reach viewers in a moment."
                    : publishToken
                      ? "Local preview only — a publisher token is ready, but no media is being sent yet."
                      : "Local preview only — this feed is not being published to viewers."}
                </p>
              )}
            </div>

            {/* Bottom-right cluster: mic status, then the expand control at the extreme
                corner where every video player puts it. */}
            <div className="flex shrink-0 items-center gap-1.5">
              <span
                className={cx(STUDIO.chip, mic ? "text-green-300" : "text-rose-300")}
                title={mic ? "Your microphone is open" : "Your microphone is muted"}
              >
                {mic ? <FiMic aria-hidden="true" /> : <FiMicOff aria-hidden="true" />}
                {mic ? "Mic live" : "Muted"}
              </span>
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                aria-pressed={expanded}
                aria-label={expanded ? "Exit fullscreen" : "Expand video to fullscreen"}
                title={expanded ? "Exit fullscreen (Esc)" : "Expand to fullscreen"}
                className={cx(
                  "pointer-events-auto grid h-7 w-7 shrink-0 place-items-center rounded-md bg-slate-950/70 text-white ring-1 ring-white/15 backdrop-blur-sm hover:bg-slate-900 hover:ring-white/30",
                  t150,
                  focusOnStage
                )}
              >
                {expanded
                  ? <FiMinimize aria-hidden="true" className="text-[13px]" />
                  : <FiMaximize aria-hidden="true" className="text-[13px]" />}
              </button>
            </div>
          </div>
        </div>
      </div>
      </div>

      {/* Waiting room is an action prompt, not decoration: it names the count twice (badge
          and sentence) so it isn't a colour-only signal. */}
      {analytics?.waiting > 0 && (
        <div className="flex shrink-0 items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs dark:border-amber-500/30 dark:bg-amber-500/10">
          <span className="shrink-0 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-white">
            {analytics.waiting}
          </span>
          <span className="font-medium text-amber-900 dark:text-amber-300">
            {analytics.waiting} {analytics.waiting === 1 ? "person is" : "people are"} in the
            waiting room — admit them from the People panel.
          </span>
        </div>
      )}
    </div>
  );
}
