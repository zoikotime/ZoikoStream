// client/src/components/watch/VideoPlayer.jsx
// Large "broadcast" player surface. Live indicator, play/pause, volume control, a scrubber,
// and real fullscreen. When `watch` carries a live LiveKit token (GET /events/{id}/watch),
// this connects and renders the host's camera/mic via useLiveKitViewer. Once the event has
// ended, `watch.recording_url` (a time-limited signed GCS link — see services/livekit.py
// signed_url) plays back through a plain <video>, with the scrubber driven by its real
// currentTime/duration. No recording_url (never captured, or LiveKit egress unavailable) ->
// honest "no recording available" placeholder, never a fake scrubber.
import { useEffect, useRef, useState } from "react";
import {
  FiPlay, FiPause, FiVolume2, FiVolume1, FiVolumeX,
  FiMaximize, FiMinimize, FiSettings, FiRotateCcw,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { initials } from "../../data/watch";
import useLiveKitViewer from "../../hooks/useLiveKitViewer";

const STAGE = {
  violet: "from-violet-900 via-slate-900 to-black",
  emerald: "from-emerald-900 via-slate-900 to-black",
  blue: "from-blue-900 via-slate-900 to-black",
  amber: "from-amber-900 via-slate-900 to-black",
  indigo: "from-indigo-900 via-slate-900 to-black",
  rose: "from-rose-900 via-slate-900 to-black",
};

function VolumeIcon({ muted, volume }) {
  if (muted || volume === 0) return <FiVolumeX />;
  return volume < 50 ? <FiVolume1 /> : <FiVolume2 />;
}

const fmtTime = (secs) => {
  const s = Math.max(0, Math.floor(secs || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`
    : `${m}:${String(r).padStart(2, "0")}`;
};

export default function VideoPlayer({ event, viewers, watch }) {
  const isLive = event.status === "Live";
  const isEnded = event.status === "Completed";
  const canStream = Boolean(watch?.status === "live" && watch?.livekit_token);
  const canReplay = isEnded && Boolean(watch?.recording_url);

  const { mediaRef, connected, hasVideo, error: streamError } = useLiveKitViewer({
    enabled: canStream,
    url: watch?.livekit_url,
    token: watch?.livekit_token,
  });

  const replayRef = useRef(null);
  const [replayStarted, setReplayStarted] = useState(false); // stays true once clicked, so
  // pausing mid-watch shows the frozen frame + resume overlay, not the "click to start" gate
  const [replayTime, setReplayTime] = useState(0);
  const [replayDuration, setReplayDuration] = useState(watch?.recording_duration_seconds || 0);

  const wrapRef = useRef(null);
  const [playing, setPlaying] = useState(isLive);
  // Starts muted: an autoplaying <video> (playing=true, no click yet) with sound is blocked
  // outright by the browser — play() rejects with NotAllowedError and audio never starts.
  // Muted autoplay is always allowed; unmuteButton's onClick is a real user gesture, so
  // unmuting from there is guaranteed to work. Same reasoning every major video site uses.
  const [muted, setMuted] = useState(true);
  const [volume, setVolume] = useState(80);
  const [fs, setFs] = useState(false);
  const progress = canReplay && replayDuration ? (replayTime / replayDuration) * 100 : 0;

  useEffect(() => {
    const onFs = () => setFs(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // Real playback: mirror play/pause/volume state onto the actual <video> element —
  // whichever one is live right now (the LiveKit stream, or the recorded replay).
  useEffect(() => {
    if (!canStream || !mediaRef.current) return;
    if (playing) mediaRef.current.play().catch(() => {}); // autoplay can be blocked pre-interaction
    else mediaRef.current.pause();
  }, [canStream, playing, hasVideo, mediaRef]);

  useEffect(() => {
    if (!canStream || !mediaRef.current) return;
    mediaRef.current.muted = muted;
    mediaRef.current.volume = Math.min(1, Math.max(0, volume / 100));
  }, [canStream, muted, volume, mediaRef]);

  useEffect(() => {
    if (!canReplay || !replayRef.current) return;
    if (playing) replayRef.current.play().catch(() => {});
    else replayRef.current.pause();
  }, [canReplay, playing]);

  useEffect(() => {
    if (!canReplay || !replayRef.current) return;
    replayRef.current.muted = muted;
    replayRef.current.volume = Math.min(1, Math.max(0, volume / 100));
  }, [canReplay, muted, volume]);

  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  };

  const onSeek = (pct) => {
    if (!canReplay || !replayRef.current || !replayDuration) return;
    replayRef.current.currentTime = (pct / 100) * replayDuration;
  };

  const showPlayOverlay = !playing || (!isLive && !isEnded);
  const showPlaceholder = canReplay ? !replayStarted : !canStream || !hasVideo;
  // Actually watchable right now, but silent because it started muted (autoplay policy) —
  // worth a visible nudge, since a silently-muted stream with no indicator reads as broken.
  const showUnmutePrompt = muted && !showPlaceholder && ((canStream && hasVideo) || (canReplay && replayStarted));

  return (
    <div
      ref={wrapRef}
      className={cx(
        "group relative aspect-video w-full overflow-hidden rounded-2xl bg-gradient-to-br ring-1 ring-slate-200 dark:ring-slate-800",
        STAGE[event.accent] || STAGE.emerald
      )}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,rgba(255,255,255,0.12),transparent_60%)]" />

      {/* Real video, when there's a live stream to show. Sits under the placeholder/overlay
          layers and simply has nothing to paint until a track is actually subscribed. */}
      {canStream && (
        <video
          ref={mediaRef}
          autoPlay
          playsInline
          // Set here, not just in the effect below: React commits this BEFORE any
          // useEffect runs, so the element is muted from the instant it exists — the
          // effect's play() call (which fires before the effect that syncs .muted) would
          // otherwise see muted=false on first mount and get NotAllowedError-blocked.
          muted={muted}
          className={cx("absolute inset-0 h-full w-full object-contain bg-black", hasVideo ? "opacity-100" : "opacity-0")}
        />
      )}

      {/* Recorded replay — a signed GCS URL, so it plays like any other file. */}
      {canReplay && (
        <video
          ref={replayRef}
          src={watch.recording_url}
          playsInline
          muted={muted}
          onLoadedMetadata={(e) => setReplayDuration(e.currentTarget.duration || replayDuration)}
          onTimeUpdate={(e) => setReplayTime(e.currentTarget.currentTime || 0)}
          onEnded={() => setPlaying(false)}
          className={cx("absolute inset-0 h-full w-full object-contain bg-black", replayStarted ? "opacity-100" : "opacity-0")}
        />
      )}

      {/* Live indicator + viewer count */}
      <div className="absolute inset-x-0 top-0 z-10 flex items-start justify-between p-4">
        {isLive ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-rose-600 px-2.5 py-1 text-xs font-bold tracking-wide text-white">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> LIVE
          </span>
        ) : (
          <span className="rounded-md bg-black/40 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
            {isEnded ? "REPLAY" : "PREVIEW"}
          </span>
        )}
        {isLive && (
          <span className="rounded-md bg-black/40 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur">
            {viewers.toLocaleString()} watching
          </span>
        )}
      </div>

      {/* Stage content — the placeholder. Shown until the host's video track actually
          arrives, so "live but nobody's camera is on yet" reads honestly instead of a
          blank black rectangle. */}
      {showPlaceholder && (
        <div className="absolute inset-0 grid place-items-center px-4 text-center">
          {isEnded && !playing ? (
            canReplay ? (
              <div className="flex flex-col items-center gap-3">
                <p className="text-lg font-semibold text-white">This event has ended</p>
                <button
                  onClick={() => { setPlaying(true); setReplayStarted(true); }}
                  className="inline-flex items-center gap-2 rounded-xl bg-white/15 px-4 py-2 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/25"
                >
                  <FiRotateCcw /> Watch the replay
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2">
                <p className="text-lg font-semibold text-white">This event has ended</p>
                <p className="text-xs text-white/70">No recording is available for this event.</p>
              </div>
            )
          ) : (
            <div className="flex flex-col items-center gap-3">
              <span className="grid h-24 w-24 place-items-center rounded-full bg-gradient-to-br from-white/25 to-white/5 text-3xl font-bold text-white shadow-lg backdrop-blur">
                {initials(event.host)}
              </span>
              <div>
                <p className="text-sm font-semibold text-white">{event.host}</p>
                <p className="text-xs text-white/70">
                  {canStream && streamError
                    ? "Couldn't connect to the stream"
                    : canStream && connected
                      ? "Waiting for the host's camera…"
                      : isLive
                        ? "On air now"
                        : "Host"}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Unmute nudge — the stream is genuinely silent right now purely because the browser
          blocked unmuted autoplay, not because anything's broken. A real click here is a
          user gesture, so the unmute it triggers is guaranteed to succeed. */}
      {showUnmutePrompt && (
        <button
          onClick={() => setMuted(false)}
          className="absolute bottom-16 left-1/2 z-20 -translate-x-1/2 inline-flex items-center gap-2 rounded-full bg-black/70 px-4 py-2 text-sm font-semibold text-white backdrop-blur transition hover:bg-black/85"
        >
          <FiVolumeX /> Tap for sound
        </button>
      )}

      {/* Center play/pause overlay */}
      {/* Suppressed only while the ended-state placeholder owns the click target (its own
          "Watch the replay" / "no recording" message) — once replayStarted, a pause needs
          this resume button back like any other paused player. */}
      {showPlayOverlay && !(isEnded && showPlaceholder) && (
        <button
          onClick={() => setPlaying((p) => !p)}
          className="absolute inset-0 z-10 grid place-items-center bg-black/25 transition"
          aria-label={playing ? "Pause" : "Play"}
        >
          <span className="grid h-16 w-16 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-105 hover:bg-white/25">
            {playing ? <FiPause className="text-2xl" /> : <FiPlay className="ml-1 text-2xl" />}
          </span>
        </button>
      )}

      {/* Control bar */}
      <div className="absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black/80 via-black/40 to-transparent p-3 opacity-100 transition sm:opacity-0 sm:group-hover:opacity-100">
        {/* Replay scrubber — only seekable once there's a real recording under it */}
        {!isLive && (
          <input
            type="range"
            min={0}
            max={100}
            value={progress}
            onChange={(e) => onSeek(Number(e.target.value))}
            disabled={!canReplay}
            aria-label="Seek"
            className="mb-2 h-1 w-full cursor-pointer accent-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          />
        )}

        <div className="flex items-center gap-3 text-white">
          <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"} className="transition hover:text-emerald-400">
            {playing ? <FiPause className="text-xl" /> : <FiPlay className="text-xl" />}
          </button>

          {/* Volume */}
          <div className="flex items-center gap-2">
            <button onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"} className="transition hover:text-emerald-400">
              <VolumeIcon muted={muted} volume={volume} />
            </button>
            <input
              type="range"
              min={0}
              max={100}
              value={muted ? 0 : volume}
              onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }}
              aria-label="Volume"
              className="hidden h-1 w-20 cursor-pointer accent-emerald-500 sm:block"
            />
          </div>

          {isLive ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-rose-500" /> LIVE
            </span>
          ) : (
            <span className="text-xs font-medium tabular-nums text-white/80">
              {canReplay ? `${fmtTime(replayTime)} / ${fmtTime(replayDuration)}` : "—"}
            </span>
          )}

          <div className="ml-auto flex items-center gap-3">
            <button aria-label="Settings" className="transition hover:text-emerald-400"><FiSettings className="text-lg" /></button>
            <button onClick={toggleFs} aria-label={fs ? "Exit fullscreen" : "Fullscreen"} className="transition hover:text-emerald-400">
              {fs ? <FiMinimize className="text-lg" /> : <FiMaximize className="text-lg" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
