// client/src/components/watch/VideoPlayer.jsx
// Large "broadcast" player surface (dummy — no real stream). Live indicator,
// play/pause, volume control, a scrubber for replays, and real fullscreen.
import { useEffect, useRef, useState } from "react";
import {
  FiPlay, FiPause, FiVolume2, FiVolume1, FiVolumeX,
  FiMaximize, FiMinimize, FiSettings, FiRotateCcw,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { initials } from "../../data/watch";

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

export default function VideoPlayer({ event, viewers }) {
  const isLive = event.status === "Live";
  const isEnded = event.status === "Completed";

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

  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  };

  const showPlayOverlay = !playing || (!isLive && !isEnded);

  return (
    <div
      ref={wrapRef}
      className={cx(
        "group relative aspect-video w-full overflow-hidden rounded-2xl bg-gradient-to-br ring-1 ring-slate-200 dark:ring-slate-800",
        STAGE[event.accent] || STAGE.emerald
      )}
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,rgba(255,255,255,0.12),transparent_60%)]" />

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

      {/* Stage content */}
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
              <p className="text-xs text-white/70">{isLive ? "On air now" : "Host"}</p>
            </div>
          </div>
        )}
      </div>

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
