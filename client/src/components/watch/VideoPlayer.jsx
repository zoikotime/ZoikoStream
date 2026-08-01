// client/src/components/watch/VideoPlayer.jsx
// Large "broadcast" player surface. Live indicator, play/pause, volume control, a scrubber
// for replays, and real fullscreen — all unchanged. What's new: when `watch` carries a live
// LiveKit token (GET /events/{id}/watch), this actually connects and renders the host's
// camera/mic via useLiveKitViewer instead of the static avatar placeholder. No token yet,
// or the event isn't live -> same placeholder as before (there's still no recording
// playback wired, so "ended" stays a mock replay scrubber).
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

export default function VideoPlayer({ event, viewers, watch }) {
  const isLive = event.status === "Live";
  const isEnded = event.status === "Completed";
  const canStream = Boolean(watch?.status === "live" && watch?.livekit_token);

  const { mediaRef, connected, hasVideo, error: streamError } = useLiveKitViewer({
    enabled: canStream,
    url: watch?.livekit_url,
    token: watch?.livekit_token,
  });

  const wrapRef = useRef(null);
  const [playing, setPlaying] = useState(isLive);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [progress, setProgress] = useState(32); // replay scrubber (%)
  const [fs, setFs] = useState(false);

  useEffect(() => {
    const onFs = () => setFs(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // Real playback: mirror play/pause/volume state onto the actual <video> element.
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

  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  };

  const showPlayOverlay = !playing || (!isLive && !isEnded);
  const showPlaceholder = !canStream || !hasVideo;

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
          className={cx("absolute inset-0 h-full w-full object-contain bg-black", hasVideo ? "opacity-100" : "opacity-0")}
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
            <div className="flex flex-col items-center gap-3">
              <p className="text-lg font-semibold text-white">This event has ended</p>
              <button
                onClick={() => setPlaying(true)}
                className="inline-flex items-center gap-2 rounded-xl bg-white/15 px-4 py-2 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/25"
              >
                <FiRotateCcw /> Watch the replay
              </button>
            </div>
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

      {/* Center play/pause overlay */}
      {showPlayOverlay && !(isEnded && !playing) && (
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
        {/* Replay scrubber */}
        {!isLive && (
          <input
            type="range"
            min={0}
            max={100}
            value={progress}
            onChange={(e) => setProgress(Number(e.target.value))}
            aria-label="Seek"
            className="mb-2 h-1 w-full cursor-pointer accent-emerald-500"
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
              {Math.floor(progress * 0.72)}:12 / 01:12:40
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
