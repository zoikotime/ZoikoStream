// client/src/components/media/MediaPlayer.jsx
// The enterprise replay player: speed, captions, seek/skip, quality, Picture-in-Picture,
// fullscreen, chapters, bookmarks and timestamp sharing.
//
// Built on the native <video> element rather than a player library. The browser already ships
// adaptive buffering, range requests, PiP, fullscreen, captions via <track>, playbackRate and a
// media session — a library would mostly re-skin controls we then have to keep accessible. What is
// custom here is only what the platform actually needs on top: a chapter/bookmark scrubber and a
// keyboard map.
//
// Quality selection is deliberately a READOUT, not a switcher: a recording is one MP4 at the
// quality it was captured at (LiveKit composite egress, see services/livekit.py), so offering
// 480p/720p/1080p would be four labels for one file. It says the real encoded quality instead.
// Multiple renditions need an HLS ladder and a transcoding step this deployment does not have.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FiPlay, FiPause, FiVolume2, FiVolumeX, FiMaximize, FiMinimize, FiRotateCcw, FiRotateCw,
  FiBookmark, FiShare2, FiSettings, FiType, FiExternalLink,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { notify } from "../../ui/Toast";
import { SPEEDS, SKIP_SECONDS, fmtClock, timestampLink } from "../../data/media";

const btn =
  "grid h-9 w-9 shrink-0 place-items-center rounded-lg text-white/85 transition hover:bg-white/15 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/60 disabled:opacity-40";

export default function MediaPlayer({
  src,
  poster,
  captionsUrl,
  durationMs = 0,
  chapters = [],
  marks = [],
  quality,
  watermark,
  startAtMs = 0,
  recordingId,
  onAddMark,
  onFirstPlay,
  onSeekRequest,
}) {
  const videoRef = useRef(null);
  const wrapRef = useRef(null);
  // The PiP mirror is gone: unlike the live stage (which composes several tracks), a replay is one
  // element, so the browser's own PiP works directly on it.
  const [playing, setPlaying] = useState(false);
  const [positionMs, setPositionMs] = useState(startAtMs);
  const [loadedMs, setLoadedMs] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [captions, setCaptions] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [menu, setMenu] = useState(null); // "speed" | null
  const [chrome, setChrome] = useState(true);
  const reported = useRef(false);
  const hideTimer = useRef(null);

  // Real length: the file's own metadata once it loads, falling back to the stored figure so the
  // scrubber has a scale before the first byte arrives.
  const total = durationMs || 0;

  // ── element wiring ─────────────────────────────────────────────────────────
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return undefined;

    const onTime = () => setPositionMs(video.currentTime * 1000);
    const onProgress = () => {
      if (video.buffered.length) {
        setLoadedMs(video.buffered.end(video.buffered.length - 1) * 1000);
      }
    };
    const onPlay = () => {
      setPlaying(true);
      // One view per mounted player, counted on the first real play rather than on page load —
      // opening a detail page is not watching it.
      if (!reported.current) {
        reported.current = true;
        onFirstPlay?.();
      }
    };
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    const onError = () => {
      // A signed URL that has expired is the common cause, and it is fixable by reloading.
      notify.error("Playback failed. The signed link may have expired — reload the page.");
      setPlaying(false);
    };

    video.addEventListener("timeupdate", onTime);
    video.addEventListener("progress", onProgress);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onEnded);
    video.addEventListener("error", onError);
    return () => {
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("progress", onProgress);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("ended", onEnded);
      video.removeEventListener("error", onError);
    };
  }, [onFirstPlay]);

  // Seek to the ?t= position once the file can actually be seeked.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !startAtMs) return undefined;
    const seek = () => {
      video.currentTime = startAtMs / 1000;
    };
    if (video.readyState >= 1) seek();
    else video.addEventListener("loadedmetadata", seek, { once: true });
    return () => video.removeEventListener("loadedmetadata", seek);
  }, [startAtMs, src]);

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const seekTo = useCallback((ms) => {
    const video = videoRef.current;
    if (!video) return;
    const target = Math.max(0, Math.min(ms, (video.duration || total / 1000) * 1000));
    video.currentTime = target / 1000;
    setPositionMs(target);
  }, [total]);

  // Exposed upward so a transcript cue or a chapter row elsewhere on the page can drive playback.
  useEffect(() => {
    if (onSeekRequest) onSeekRequest(seekTo);
  }, [onSeekRequest, seekTo]);

  const toggle = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play().catch(() => notify.error("This browser blocked playback"));
    else video.pause();
  }, []);

  const nudge = useCallback((seconds) => {
    const video = videoRef.current;
    if (video) seekTo((video.currentTime + seconds) * 1000);
  }, [seekTo]);

  // ── keyboard ───────────────────────────────────────────────────────────────
  // Scoped to the player, not the document: a global handler would swallow the space bar while
  // somebody is typing a note in the panel beside it.
  const onKeyDown = (event) => {
    const keys = {
      " ": () => toggle(),
      k: () => toggle(),
      ArrowLeft: () => nudge(-SKIP_SECONDS),
      ArrowRight: () => nudge(SKIP_SECONDS),
      j: () => nudge(-SKIP_SECONDS),
      l: () => nudge(SKIP_SECONDS),
      ArrowUp: () => changeVolume(Math.min(1, volume + 0.1)),
      ArrowDown: () => changeVolume(Math.max(0, volume - 0.1)),
      m: () => toggleMute(),
      f: () => toggleFullscreen(),
      c: () => toggleCaptions(),
      b: () => onAddMark?.(positionMs),
      Home: () => seekTo(0),
      End: () => seekTo(total),
    };
    // Number keys jump to deciles, the one convention every video site shares.
    if (/^[0-9]$/.test(event.key)) {
      event.preventDefault();
      seekTo((total * Number(event.key)) / 10);
      return;
    }
    const action = keys[event.key];
    if (action) {
      event.preventDefault();
      action();
    }
  };

  const changeVolume = (value) => {
    setVolume(value);
    setMuted(value === 0);
    if (videoRef.current) {
      videoRef.current.volume = value;
      videoRef.current.muted = value === 0;
    }
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    if (videoRef.current) videoRef.current.muted = next;
  };

  const changeSpeed = (value) => {
    setSpeed(value);
    setMenu(null);
    if (videoRef.current) videoRef.current.playbackRate = value;
  };

  const toggleCaptions = () => {
    const video = videoRef.current;
    if (!video || !captionsUrl) return;
    const next = !captions;
    setCaptions(next);
    // The <track> is always mounted; showing it is a mode change on the TextTrack, which is the
    // only way to control native captions.
    Array.from(video.textTracks || []).forEach((track) => {
      track.mode = next ? "showing" : "hidden";
    });
  };

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.().catch(() => notify.error("Fullscreen was blocked"));
  };

  const togglePip = async () => {
    const video = videoRef.current;
    if (!video) return;
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await video.requestPictureInPicture();
    } catch {
      notify.error("Picture-in-Picture is not available in this browser");
    }
  };

  const share = async () => {
    const url = timestampLink(recordingId, positionMs);
    try {
      await navigator.clipboard.writeText(url);
      notify.success(`Link copied at ${fmtClock(positionMs)}`);
    } catch {
      // Clipboard access can be refused outright (insecure context, permissions policy).
      notify.info(url);
    }
  };

  // Auto-hide the controls while playing, and always show them on hover or focus.
  const wake = () => {
    setChrome(true);
    clearTimeout(hideTimer.current);
    if (playing) hideTimer.current = setTimeout(() => setChrome(false), 2600);
  };
  useEffect(() => () => clearTimeout(hideTimer.current), []);

  const scrubMarks = useMemo(() => {
    if (!total) return [];
    return [
      ...chapters.map((c) => ({ at: c.at_ms, kind: "chapter", label: c.title })),
      ...marks.map((m) => ({ at: m.at_ms, kind: m.note ? "note" : "bookmark",
        label: m.note || "Bookmark" })),
    ].filter((m) => m.at >= 0 && m.at <= total);
  }, [chapters, marks, total]);

  const percent = total ? (positionMs / total) * 100 : 0;
  const bufferPercent = total ? Math.min(100, (loadedMs / total) * 100) : 0;

  if (!src) {
    return (
      <div className="grid aspect-video place-items-center rounded-2xl bg-slate-900 text-center text-sm text-slate-400">
        <div className="max-w-sm px-6">
          <p className="font-medium text-slate-200">No playable file</p>
          <p className="mt-1">
            This recording has no stored video. Either the capture failed, or object storage is not
            configured on this deployment.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      className="group relative overflow-hidden rounded-2xl bg-black focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-emerald-500/60"
      onMouseMove={wake}
      onMouseLeave={() => playing && setChrome(false)}
      onKeyDown={onKeyDown}
      role="region"
      aria-label="Recording player"
      tabIndex={0}
    >
      {/* The <track> below is the caption, mounted only when a transcript exists — a recording
          without one has none to offer. */}
      <video
        ref={videoRef}
        src={src}
        poster={poster || undefined}
        className="aspect-video w-full bg-black"
        preload="metadata"
        playsInline
        onClick={toggle}
      >
        {captionsUrl && (
          <track kind="captions" src={captionsUrl} srcLang="en" label="Captions" default={false} />
        )}
      </video>

      {/* Viewer-identity watermark. A deterrent against re-recording the screen, which is the only
          thing a watermark can do here — the DOWNLOADED file cannot carry one without a re-encode
          (stated in the download panel, not hidden). */}
      {watermark && (
        <div className="pointer-events-none absolute right-3 top-3 select-none rounded bg-black/35 px-2 py-1 text-[11px] font-medium text-white/70 backdrop-blur">
          {watermark}
        </div>
      )}

      {!playing && (
        <button
          onClick={toggle}
          aria-label="Play"
          className="absolute inset-0 grid place-items-center bg-black/25 transition hover:bg-black/35"
        >
          <span className="grid h-16 w-16 place-items-center rounded-full bg-white/20 text-white backdrop-blur transition hover:scale-105">
            <FiPlay className="ml-1 text-2xl" />
          </span>
        </button>
      )}

      {/* Controls */}
      <div
        className={cx(
          "absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/55 to-transparent px-3 pb-2 pt-8 transition-opacity",
          chrome || !playing ? "opacity-100" : "opacity-0 group-hover:opacity-100"
        )}
      >
        {/* Scrubber */}
        <div className="relative h-6">
          <div className="pointer-events-none absolute inset-x-0 top-2.5 h-1 overflow-hidden rounded-full bg-white/25">
            <div className="h-full bg-white/30" style={{ width: `${bufferPercent}%` }} />
          </div>
          <div
            className="pointer-events-none absolute top-2.5 h-1 rounded-full bg-emerald-400"
            style={{ width: `${percent}%` }}
          />
          {scrubMarks.map((mark, i) => (
            <span
              key={`${mark.kind}-${mark.at}-${i}`}
              title={`${fmtClock(mark.at)} · ${mark.label}`}
              className={cx(
                "pointer-events-none absolute top-1.5 h-3 w-[3px] rounded-full",
                mark.kind === "chapter" ? "bg-white/70" : "bg-amber-400"
              )}
              style={{ left: `${(mark.at / total) * 100}%` }}
            />
          ))}
          <input
            type="range"
            min={0}
            max={total || 0}
            step={250}
            value={Math.min(positionMs, total || 0)}
            onChange={(e) => seekTo(Number(e.target.value))}
            aria-label="Seek"
            aria-valuetext={fmtClock(positionMs)}
            className="absolute inset-x-0 top-0 h-6 w-full cursor-pointer appearance-none bg-transparent [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:w-3.5 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-white"
          />
        </div>

        <div className="flex items-center gap-0.5">
          <button className={btn} onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
            {playing ? <FiPause /> : <FiPlay />}
          </button>
          <button className={btn} onClick={() => nudge(-SKIP_SECONDS)}
            aria-label={`Back ${SKIP_SECONDS} seconds`} title={`Back ${SKIP_SECONDS}s (←)`}>
            <FiRotateCcw />
          </button>
          <button className={btn} onClick={() => nudge(SKIP_SECONDS)}
            aria-label={`Forward ${SKIP_SECONDS} seconds`} title={`Forward ${SKIP_SECONDS}s (→)`}>
            <FiRotateCw />
          </button>

          <div className="ml-1 flex items-center gap-1.5">
            <button className={btn} onClick={toggleMute} aria-label={muted ? "Unmute" : "Mute"}>
              {muted || volume === 0 ? <FiVolumeX /> : <FiVolume2 />}
            </button>
            <input
              type="range" min={0} max={1} step={0.05}
              value={muted ? 0 : volume}
              onChange={(e) => changeVolume(Number(e.target.value))}
              aria-label="Volume"
              className="hidden h-1 w-16 cursor-pointer appearance-none rounded-full bg-white/30 sm:block [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-white [&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:bg-white"
            />
          </div>

          <span className="ml-2 shrink-0 text-xs tabular-nums text-white/85">
            {fmtClock(positionMs)} <span className="text-white/45">/ {fmtClock(total)}</span>
          </span>

          <div className="ml-auto flex items-center gap-0.5">
            {onAddMark && (
              <button className={btn} onClick={() => onAddMark(positionMs)}
                aria-label="Bookmark this moment" title="Bookmark (B)">
                <FiBookmark />
              </button>
            )}
            <button className={btn} onClick={share}
              aria-label="Copy a link to this moment" title="Copy link at current time">
              <FiShare2 />
            </button>
            {captionsUrl && (
              <button
                className={cx(btn, captions && "bg-white/20 text-white")}
                onClick={toggleCaptions}
                aria-label="Toggle captions" aria-pressed={captions} title="Captions (C)"
              >
                <FiType />
              </button>
            )}

            <div className="relative">
              <button
                className={cx(btn, menu === "speed" && "bg-white/20 text-white")}
                onClick={() => setMenu(menu === "speed" ? null : "speed")}
                aria-label="Playback settings" aria-expanded={menu === "speed"}
              >
                <FiSettings />
              </button>
              {menu === "speed" && (
                <div className="absolute bottom-11 right-0 w-48 overflow-hidden rounded-xl border border-white/10 bg-slate-900/95 p-1 text-sm shadow-xl backdrop-blur">
                  <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-white/45">
                    Speed
                  </p>
                  {SPEEDS.map((value) => (
                    <button
                      key={value}
                      onClick={() => changeSpeed(value)}
                      className={cx(
                        "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-white/85 hover:bg-white/10",
                        speed === value && "bg-white/15 text-white"
                      )}
                    >
                      {value === 1 ? "Normal" : `${value}×`}
                      {speed === value && <span aria-hidden="true">✓</span>}
                    </button>
                  ))}
                  <p className="mt-1 border-t border-white/10 px-2.5 pb-1 pt-2 text-[11px] text-white/45">
                    Quality: {quality ? quality.toUpperCase() : "as captured"}
                    <br />
                    {/* Honest, in the place a viewer would look for a quality switcher. */}
                    <span className="text-white/35">
                      Recordings are stored as a single file at the captured quality.
                    </span>
                  </p>
                </div>
              )}
            </div>

            <button className={btn} onClick={togglePip} aria-label="Picture in Picture"
              title="Picture in Picture">
              <FiExternalLink />
            </button>
            <button className={btn} onClick={toggleFullscreen}
              aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen"} title="Fullscreen (F)">
              {fullscreen ? <FiMinimize /> : <FiMaximize />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Chapter / bookmark list beside the player. Clicking a row seeks. */
export function ChapterList({ chapters = [], marks = [], onSeek, onDeleteMark }) {
  const rows = useMemo(() => {
    const merged = [
      ...chapters.map((c) => ({ ...c, kind: c.kind || "chapter", label: c.title })),
      ...marks.map((m) => ({ ...m, kind: m.note ? "note" : "bookmark", label: m.note || "Bookmark" })),
    ];
    return merged.sort((a, b) => a.at_ms - b.at_ms);
  }, [chapters, marks]);

  if (!rows.length) {
    return (
      <p className="px-1 py-6 text-center text-sm text-slate-400">
        No chapters or bookmarks yet. Press <kbd className="rounded border border-slate-300 px-1 text-xs dark:border-slate-600">B</kbd> while
        watching to mark a moment.
      </p>
    );
  }

  return (
    <ul className="max-h-80 space-y-0.5 overflow-y-auto pr-1">
      {rows.map((row, i) => (
        <li key={`${row.kind}-${row.at_ms}-${row.id || i}`} className="group/row flex items-start gap-2">
          <button
            onClick={() => onSeek?.(row.at_ms)}
            className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg px-2 py-1.5 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800"
          >
            <span className="mt-0.5 shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {fmtClock(row.at_ms)}
            </span>
            <span className="min-w-0 flex-1">
              <span className={cx(
                "block truncate text-sm",
                row.kind === "chapter" ? "text-slate-700 dark:text-slate-200"
                  : "text-amber-700 dark:text-amber-400"
              )}>
                {row.label}
              </span>
              {row.author && (
                <span className="text-[11px] text-slate-400">shared by {row.author}</span>
              )}
            </span>
          </button>
          {row.mine && onDeleteMark && (
            <button
              onClick={() => onDeleteMark(row.id)}
              aria-label="Remove this mark"
              className="mt-1.5 shrink-0 px-1 text-xs text-slate-300 opacity-0 transition hover:text-rose-500 group-hover/row:opacity-100"
            >
              ✕
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
