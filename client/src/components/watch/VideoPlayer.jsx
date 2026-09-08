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
  FiPlay,
  FiPause,
  FiVolume2,
  FiVolume1,
  FiVolumeX,
  FiMaximize,
  FiMinimize,
  FiSettings,
  FiRotateCcw,
  FiChevronRight,
  FiCheck,
  FiMonitor,
  FiMic,
  FiMicOff,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { initials } from "../../data/watch";
import useLiveKitViewer from "../../hooks/useLiveKitViewer";
import Logo from "../../ui/Logo";

const STAGE = {
  violet: "from-violet-900 via-slate-900 to-black",
  emerald: "from-emerald-900 via-slate-900 to-black",
  blue: "from-blue-900 via-slate-900 to-black",
  amber: "from-amber-900 via-slate-900 to-black",
  indigo: "from-indigo-900 via-slate-900 to-black",
  rose: "from-rose-900 via-slate-900 to-black",
};

// Overlay badge skin, shared by the LIVE/REPLAY chip and the viewer counter so they always
// match. Solid-dark in both themes on purpose — these sit on video, not on the page.
const BADGE = "rounded-lg bg-black/55 px-2.5 py-1 text-xs font-semibold text-white shadow-sm ring-1 ring-white/10 backdrop-blur-md";

// Control-bar icon button: 44px hit area for touch, quiet hover wash, brand tint on hover.
const CTRL =
  "grid h-11 w-11 place-items-center rounded-lg transition duration-150 hover:bg-white/15 hover:text-emerald-400 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100";

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

export default function VideoPlayer({ event, viewers, watch, onStage = false, children }) {
  const isLive = event.status === "Live";
  const isEnded = event.status === "Completed";
  // The TOKEN is the gate, not a status string. GET /events/:id/watch only issues
  // livekit_token when the backend has decided this visitor may stream right now
  // (routers/events.py::watch_event's can_stream — which deliberately includes
  // status "degraded", so a viewer can sit connected and recover automatically once the
  // producer's media comes back). This used to also require watch.status === "live", which
  // threw that away: the moment services/broadcast.py's sampler marked an event "degraded"
  // (its normal reaction to a producer that isn't publishing), canStream went false, the
  // <video> element below was never mounted, useLiveKitViewer was never enabled, and the
  // page sat on the host placeholder with a "PREVIEW" badge — for an event the backend was
  // still streaming and still handing out tokens for. Recovery needed a manual reload,
  // because nothing re-armed when the host came back. Honest liveness messaging comes from
  // media_status (see the placeholder below), never from refusing to connect.
  const canStream = Boolean(watch?.livekit_token && watch?.livekit_url && !isEnded);
  const canReplay = isEnded && Boolean(watch?.recording_url);

  const {
    mediaRef, connected, reconnecting, hasVideo, hasAudio, error: streamError,
    micOn, micError, toggleMic,
  } = useLiveKitViewer({
    enabled: canStream,
    url: watch?.livekit_url,
    token: watch?.livekit_token,
    // The host promoted this viewer to speaker — see EventWatch.jsx, which derives this
    // from the viewer's own presence record (participants[you.identity]). The hook does
    // the actual mic capture + publish; this component only needs to show the control.
    canPublish: canStream && onStage,
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
  const [showSettings, setShowSettings] = useState(false);
  const [settingsPage, setSettingsPage] = useState("main");
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  const progress =
    canReplay && replayDuration
      ? (replayTime / replayDuration) * 100
      : 0;

  // `playing` only seeds from `isLive` at mount (see useState(isLive) above) — it never
  // notices a LIVE EVENT ending under it without a remount. Left alone, the live stream's
  // playing=true survives straight into the ended state: showPlaceholder's `isEnded &&
  // !playing` check then never passes, so the placeholder falls through to the stale
  // "Host"/streaming caption instead of "This event has ended". A page refresh used to
  // paper over this by remounting with isLive already false. React's own pattern for
  // "adjusting state when a prop changes" (react.dev/learn/you-might-not-need-an-effect) —
  // same one EventWatch's loadedEventId reset uses — rather than a setState-in-effect: this
  // only fires on the actual live->ended transition, so it never stomps on a viewer who
  // already clicked "Watch the replay".
  const [wasEnded, setWasEnded] = useState(isEnded);
  if (isEnded !== wasEnded) {
    setWasEnded(isEnded);
    if (isEnded) setPlaying(false);
  }

  useEffect(() => {
    const onFs = () => setFs(Boolean(document.fullscreenElement));

    document.addEventListener("fullscreenchange", onFs);

    return () => {
      document.removeEventListener("fullscreenchange", onFs);
    };
  }, []);

  useEffect(() => {
    const video = canStream ? mediaRef.current : replayRef.current;

    if (!video) return undefined;

    const onWebkitBeginFullscreen = () => setFs(true);
    const onWebkitEndFullscreen = () => setFs(false);

    video.addEventListener(
      "webkitbeginfullscreen",
      onWebkitBeginFullscreen
    );

    video.addEventListener(
      "webkitendfullscreen",
      onWebkitEndFullscreen
    );

    return () => {
      video.removeEventListener(
        "webkitbeginfullscreen",
        onWebkitBeginFullscreen
      );

      video.removeEventListener(
        "webkitendfullscreen",
        onWebkitEndFullscreen
      );
    };
  }, [canStream, mediaRef, replayStarted]);

  // Real playback: mirror play/pause/volume state onto the actual <video> element —
  // whichever one is live right now (the LiveKit stream, or the recorded replay).
  useEffect(() => {
    if (!canStream || !mediaRef.current) return;
    if (playing) {
      // Rejection isn't always the expected "blocked pre-interaction" case this is muted
      // for — logging it (not just swallowing it) means a genuinely different autoplay
      // failure leaves a trace instead of nothing. Reverting `playing` to false keeps the
      // visible play/pause button honest: without this, the button showed "playing" while
      // the element sat paused, with no way to tell from the UI.
      mediaRef.current.play().catch((e) => {
        console.warn("Live video play() rejected:", e?.name || e);
        setPlaying(false);
      });
    } else {
      mediaRef.current.pause();
    }
  }, [canStream, playing, hasVideo, mediaRef]);

  // livekit-client's own attachToElement() (called on every track.attach(), including a
  // reconnect's resubscribe) sets `element.muted = mediaStream.getAudioTracks().length ===
  // 0` as an autoplay-compliance side effect — the moment the audio track attaches, it
  // force-UNMUTES the element outright, silently overriding whatever the viewer had chosen.
  // Depending on hasVideo/hasAudio (flipped by useLiveKitViewer's TrackSubscribed/
  // Unsubscribed handlers, i.e. exactly when attach()/detach() run) re-asserts our own
  // muted/volume state right after LiveKit's reset instead of only reacting to the viewer's
  // own clicks — otherwise a track (re)attaching after the user muted quietly undoes it.
  useEffect(() => {
    if (!canStream || !mediaRef.current) return;
    mediaRef.current.muted = muted;
    mediaRef.current.volume = Math.min(1, Math.max(0, volume / 100));
  }, [canStream, muted, volume, mediaRef, hasVideo, hasAudio]);

  useEffect(() => {
    if (!canReplay || !replayRef.current) return;
    if (playing) {
      replayRef.current.play().catch((e) => {
        console.warn("Replay video play() rejected:", e?.name || e);
        setPlaying(false);
      });
    } else {
      replayRef.current.pause();
    }
  }, [canReplay, playing]);

  useEffect(() => {
    if (!canReplay || !replayRef.current) return;
    replayRef.current.muted = muted;
    replayRef.current.volume = Math.min(1, Math.max(0, volume / 100));
  }, [canReplay, muted, volume]);
  useEffect(() => {
    if (!canReplay || !replayRef.current) return;

    replayRef.current.playbackRate = playbackSpeed;
  }, [canReplay, playbackSpeed]);

  const togglePictureInPicture = async () => {
    const video = canStream ? mediaRef.current : replayRef.current;

    if (!video) return;

    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
        return;
      }

      if (document.pictureInPictureEnabled && video.requestPictureInPicture) {
        await video.requestPictureInPicture();
      }
    } catch (error) {
      console.error("Picture-in-Picture failed:", error);
    }
  };
  const toggleFs = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
      return;
    }

    const video = canStream ? mediaRef.current : replayRef.current;

    // iPhone / iPad Safari native fullscreen fallback
    if (video?.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
      return;
    }

    // Standard fullscreen for desktop/Android browsers
    if (wrapRef.current?.requestFullscreen) {
      wrapRef.current.requestFullscreen().catch(() => {});
    }
  };

  const onSeek = (pct) => {
    if (!canReplay || !replayRef.current || !replayDuration) return;
    replayRef.current.currentTime = (pct / 100) * replayDuration;
  };

  const showPlayOverlay = !playing || (!isLive && !isEnded);
  // !hasVideo && !hasAudio, not just !hasVideo: an audio-only stream (host's camera off, or
  // video simply hasn't attached yet while audio already has) used to stay stuck behind
  // "waiting for the host's camera" with no unmute prompt below ever appearing — the viewer
  // heard nothing and had no way to know why. Either track arriving is enough to drop the
  // placeholder.
  const showPlaceholder = canReplay ? !replayStarted : !canStream || (!hasVideo && !hasAudio);
  // Actually watchable right now, but silent because it started muted (autoplay policy) —
  // worth a visible nudge, since a silently-muted stream with no indicator reads as broken.
  // hasAudio alongside hasVideo for the same reason as showPlaceholder above.
  const showUnmutePrompt = muted && !showPlaceholder
    && ((canStream && (hasVideo || hasAudio)) || (canReplay && replayStarted));

  return (
    <div
      ref={wrapRef}
      className={cx(
        "group relative aspect-video w-full overflow-hidden rounded-2xl bg-gradient-to-br shadow-xl shadow-slate-900/10 ring-1 ring-slate-200 dark:shadow-black/40 dark:ring-white/10",
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
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between bg-gradient-to-b from-black/45 to-transparent p-3 sm:p-4">
        {isLive ? (
          <span className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-2.5 py-1 text-xs font-bold tracking-wide text-white shadow-lg shadow-rose-900/40 ring-1 ring-white/20">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> LIVE
          </span>
        ) : (
          <span className={BADGE}>{isEnded ? "REPLAY" : "PREVIEW"}</span>
        )}
        {isLive && viewers != null && (
          <span className={cx(BADGE, "zk-tnum")}>{viewers.toLocaleString()} watching</span>
        )}
      </div>

      {/* On-stage indicator — only shown to the promoted viewer themselves (onStage is
          this viewer's OWN status, from EventWatch.jsx), never to the rest of the
          audience. Sits under the LIVE/viewer-count row so it never collides with it. */}
      {canStream && onStage && (
        <div className="pointer-events-none absolute inset-x-0 top-11 z-10 flex justify-center px-3 sm:top-12">
          <span className="pointer-events-auto inline-flex items-center gap-1.5 rounded-lg bg-emerald-600/90 px-2.5 py-1 text-xs font-semibold text-white shadow-lg ring-1 ring-white/20 backdrop-blur-md">
            <FiMic aria-hidden="true" />
            You&apos;re on stage — the host and audience can hear you
          </span>
        </div>
      )}
      {canStream && onStage && micError && (
        <div className="pointer-events-none absolute inset-x-0 top-[4.5rem] z-10 flex justify-center px-3">
          <span className="pointer-events-auto rounded-lg bg-rose-600/90 px-2.5 py-1 text-xs font-medium text-white shadow-lg ring-1 ring-white/20 backdrop-blur-md">
            {micError}
          </span>
        </div>
      )}

      {/* Stage content — the placeholder. Shown until the host's video track actually
          arrives, so "live but nobody's camera is on yet" reads honestly instead of a
          blank black rectangle. */}
      {showPlaceholder && (
        <div className="absolute inset-0 grid place-items-center px-4 text-center">
          {/* Decorative brand watermark + wave lines — sits behind the host-avatar
              placeholder content below, never replaces it. Purely cosmetic, so it's
              skipped entirely once real video is attached (showPlaceholder is false). */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="absolute inset-x-0 top-[18%] flex flex-col items-center gap-2 opacity-40">
              <Logo height="h-5" />
              <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/70">
                Secure · Scalable · Reliable
              </p>
            </div>
            <svg viewBox="0 0 400 220" preserveAspectRatio="none" className="absolute inset-x-0 bottom-0 h-2/5 w-full" aria-hidden>
              <path d="M0 150 C70 120 110 170 190 145 C270 120 310 165 400 140 L400 220 L0 220 Z" fill="#8b5cf6" opacity="0.18" />
              <path d="M0 180 C90 160 150 195 230 175 C310 155 350 190 400 175 L400 220 L0 220 Z" fill="#6d28d9" opacity="0.22" />
            </svg>
          </div>
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
                    ? streamError
                    : canStream && reconnecting
                      ? "Reconnecting…"
                      // Backend-confirmed (services/broadcast.py mark_degraded, via
                      // GET /events/:id/watch's media_status) — distinct from the transient,
                      // client-only `reconnecting` above: this means the SERVER has verified
                      // the producer's media actually dropped, not just this viewer's own
                      // socket. Worth its own honest wording rather than folding into the
                      // generic "waiting for camera" case below.
                      : canStream && watch?.media_status === "reconnecting"
                        ? "The host's connection dropped — waiting for them to reconnect…"
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
          user gesture, so the unmute it triggers is guaranteed to succeed. Dead-center and
          pulsing rather than a small bottom pill: a muted stream with no visible sound
          control reads as "the audio is broken" to a viewer who never notices a quiet
          corner button — this has to be impossible to miss. */}
      {showUnmutePrompt && (
        <button
          onClick={() => setMuted(false)}
          className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 bg-black/45 text-white transition hover:bg-black/55"
        >
          <span className="grid h-16 w-16 place-items-center rounded-full bg-white/15 ring-4 ring-white/30 backdrop-blur motion-safe:animate-pulse motion-reduce:animate-none">
            <FiVolumeX className="text-3xl" />
          </span>
          <span className="rounded-full bg-black/70 px-4 py-1.5 text-sm font-semibold backdrop-blur">
            Click anywhere to unmute
          </span>
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
      {/* focus-within keeps the bar visible for keyboard users, who never trigger :hover. */}
      <div className="absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black/85 via-black/45 to-transparent p-2 opacity-100 transition duration-200 motion-reduce:transition-none sm:p-3 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
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

        <div className="flex items-center gap-2 text-white sm:gap-3">
          <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"} className={CTRL}>
            {playing ? <FiPause className="text-xl" /> : <FiPlay className="text-xl" />}
          </button>

          {/* Volume */}
          <div className="flex items-center gap-1 sm:gap-2">
            <button onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"} className={CTRL}>
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

          {/* Own-mic control — only present while this viewer is actually on stage. A
              separate control from the volume button above: that one controls what THIS
              viewer hears, this one controls what everyone else hears FROM them. */}
          {canStream && onStage && (
            <button
              onClick={toggleMic}
              aria-label={micOn ? "Mute your mic" : "Unmute your mic"}
              title={micOn ? "Mute your mic" : "Unmute your mic"}
              className={cx(CTRL, micOn ? "text-emerald-400" : "text-rose-400")}
            >
              {micOn ? <FiMic className="text-lg" /> : <FiMicOff className="text-lg" />}
            </button>
          )}

          {isLive ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-rose-500" /> LIVE
            </span>
          ) : (
            <span className="text-xs font-medium tabular-nums text-white/80">
              {canReplay ? `${fmtTime(replayTime)} / ${fmtTime(replayDuration)}` : "—"}
            </span>
          )}

          <div className="ml-auto flex items-center gap-1 sm:gap-2">
            <div className="relative">
              <button
                onClick={() => {
                  setShowSettings((v) => !v);
                  setSettingsPage("main");
                }}
                aria-label="Settings"
                title="Settings"
                className={CTRL}
              >
                <FiSettings className="text-lg" />
              </button>

              {showSettings && (
                <div className="absolute bottom-12 right-0 z-50 w-56 overflow-hidden rounded-xl bg-slate-900 p-2 text-sm text-white shadow-2xl ring-1 ring-white/10">
                  {settingsPage === "main" ? (
                    <>
                      {/* Settings header */}
                      <div className="border-b border-white/10 px-3 py-2">
                        <p className="font-semibold">Settings</p>
                      </div>

                      {/* Quality */}
                      <button
                        type="button"
                        onClick={() => setSettingsPage("quality")}
                        className="flex w-full items-center justify-between rounded-lg px-3 py-3 text-left hover:bg-white/10"
                      >
                        <div className="flex items-center gap-3">
                          <FiMonitor className="text-base text-white/80" />
                          <div>
                            <p className="font-medium">Quality</p>
                            <p className="text-xs text-white/50">Auto</p>
                          </div>
                        </div>

                        <FiChevronRight className="text-white/50" />
                      </button>

                      {/* Playback speed */}
                      <button
                        type="button"
                        onClick={() => setSettingsPage("speed")}
                        className="flex w-full items-center justify-between rounded-lg px-3 py-3 text-left hover:bg-white/10"
                      >
                        <div>
                          <p className="font-medium">Playback speed</p>
                          <p className="text-xs text-white/50">
                            {playbackSpeed === 1 ? "Normal" : `${playbackSpeed}x`}
                          </p>
                        </div>

                        <FiChevronRight className="text-white/50" />
                      </button>

                      {/* Picture in Picture */}
                      <button
                        type="button"
                        onClick={togglePictureInPicture}
                        // Same condition that mounts <video ref={mediaRef}>/<video
                        // ref={replayRef}> below — refs can't be read during render (React
                        // doesn't know to re-render when a ref's .current changes, so a
                        // disabled-state derived from it can go stale), and this is the
                        // render-safe equivalent: the ref is populated exactly when one of
                        // these is true. togglePictureInPicture itself still no-ops safely
                        // on a null ref for the brief window before the element mounts.
                        disabled={!(canStream || canReplay)}
                        className="flex w-full items-center justify-between rounded-lg px-3 py-3 text-left hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <div>
                          <p className="font-medium">Picture-in-Picture</p>
                          <p className="text-xs text-white/50">
                            Watch while using other apps
                          </p>
                        </div>
                      </button>
                    </>
                  ) : settingsPage === "quality" ? (
                    <>
                      {/* Quality page */}
                      <div className="flex items-center gap-2 border-b border-white/10 px-2 py-2">
                        <button
                          type="button"
                          onClick={() => setSettingsPage("main")}
                          className="rounded-md px-2 py-1 text-white/70 hover:bg-white/10 hover:text-white"
                          aria-label="Back to settings"
                        >
                          ←
                        </button>

                        <p className="font-semibold">Quality</p>
                      </div>

                      {["Auto", "1080p", "720p", "480p", "360p"].map((quality) => (
                        <button
                          key={quality}
                          type="button"
                          disabled={quality !== "Auto"}
                          onClick={() => {
                            if (quality === "Auto") {
                              setSettingsPage("main");
                            }
                          }}
                          className="flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <span>{quality}</span>

                          {quality === "Auto" && (
                            <FiCheck className="text-emerald-400" />
                          )}
                        </button>
                      ))}

                      <p className="px-3 pb-2 pt-2 text-[11px] leading-4 text-white/40">
                        Manual quality selection will be available when multiple
                        video renditions are provided.
                      </p>
                    </>
                  ) : (
                    <>
                      {/* Playback speed page */}
                      <div className="flex items-center gap-2 border-b border-white/10 px-2 py-2">
                        <button
                          type="button"
                          onClick={() => setSettingsPage("main")}
                          className="rounded-md px-2 py-1 text-white/70 hover:bg-white/10 hover:text-white"
                          aria-label="Back to settings"
                        >
                          ←
                        </button>

                        <p className="font-semibold">Playback speed</p>
                      </div>

                      {[0.5, 0.75, 1, 1.25, 1.5, 2].map((speed) => (
                        <button
                          key={speed}
                          type="button"
                          onClick={() => {
                            setPlaybackSpeed(speed);
                            setSettingsPage("main");
                          }}
                          className="flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left hover:bg-white/10"
                        >
                          <span>{speed === 1 ? "Normal" : `${speed}x`}</span>

                          {playbackSpeed === speed && (
                            <FiCheck className="text-emerald-400" />
                          )}
                        </button>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
            <button onClick={toggleFs} aria-label={fs ? "Exit fullscreen" : "Fullscreen"} title={fs ? "Exit fullscreen" : "Fullscreen"} className={CTRL}>
              {fs ? <FiMinimize className="text-lg" /> : <FiMaximize className="text-lg" />}
            </button>
          </div>
        </div>
      </div>

      {/* Floating reaction bursts (EventWatch.jsx) sit above every other layer,
          including the control bar, so a tap never gets hidden behind it. */}
      {children}
    </div>
  );
}