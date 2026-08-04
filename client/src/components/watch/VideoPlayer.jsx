// client/src/components/watch/VideoPlayer.jsx
// The attendee's player. Real WebRTC playback from the event's LiveKit room — there is no
// dummy surface here any more.
//
// livekit-client is ~150KB and is loaded with a dynamic import() at the moment playback
// starts, not at module scope. An attendee who lands on a scheduled event and never presses
// play never downloads it, which is what keeps the landing page's first paint fast.
//
// The stage stays dark in BOTH themes. That is deliberate, not a missed dark:/light: pair:
// letterboxing around video reads as a bezel, and a white bezel wrecks perceived contrast
// on the picture. Every chrome element AROUND the stage is theme-paired.
import { useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  FiPlay, FiPause, FiVolume2, FiVolume1, FiVolumeX,
  FiMaximize, FiMinimize, FiSettings, FiRotateCcw, FiEye, FiAlertTriangle,
} from "react-icons/fi";
import { MdPictureInPicture } from "react-icons/md";
import api, { errMsg } from "../../api";
import { cx } from "../../ui/tokens";
import StageView from "./StageView";
import useRemoteFeeds from "../../hooks/useRemoteFeeds";
import { STAGE_LAYOUTS } from "../../data/attendee";

// Playback lifecycle. Kept as one value rather than four booleans so the overlay can never
// render two states at once (the bug the old boolean soup produced).
const IDLE = "idle";            // not connected; showing the poster + play affordance
const CONNECTING = "connecting";
const PLAYING = "playing";
const RECONNECTING = "reconnecting";
const FAILED = "failed";

// Simulcast layers, top-down. LiveKit's layers are RELATIVE (the publisher decides the
// actual resolutions), so labelling them "1080p" would be a guess printed as a fact.
const QUALITIES = [
  { id: "auto", label: "Auto" },
  { id: "high", label: "High" },
  { id: "medium", label: "Medium" },
  { id: "low", label: "Low" },
];

function VolumeIcon({ muted, volume }) {
  if (muted || volume === 0) return <FiVolumeX />;
  return volume < 50 ? <FiVolume1 /> : <FiVolume2 />;
}

const iconBtn =
  "grid h-8 w-8 place-items-center rounded-md text-white/80 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60";

// `ref` is a plain prop in React 19 — no forwardRef wrapper needed.
export default function VideoPlayer({ eventId, live, viewers, poster, title, ref }) {
  const wrapRef = useRef(null);
  const videoRef = useRef(null);
  const audioRef = useRef(null);
  // The Room and the subscribed publication are imperative handles, not render inputs —
  // holding them in state would re-render the player on every LiveKit event.
  const roomRef = useRef(null);
  const videoPubRef = useRef(null);
  const sdkRef = useRef(null);

  const [phase, setPhase] = useState(IDLE);
  const [error, setError] = useState(null);
  const [buffering, setBuffering] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [fs, setFs] = useState(false);
  const [pip, setPip] = useState(false);
  const [quality, setQuality] = useState("auto");
  const [qualityOpen, setQualityOpen] = useState(false);
  const [rtt, setRtt] = useState(null);          // real round-trip, ms — null until measured
  const [layout, setLayout] = useState("spotlight");
  const [pinned, setPinned] = useState("");

  // Every remote publisher, not just the last one subscribed. See StageView for why.
  const { feeds, syncFeeds } = useRemoteFeeds(roomRef, sdkRef, phase === PLAYING ? "playing" : "idle");
  const stageReady = phase === PLAYING && feeds.length > 0;

  // The feed Picture-in-Picture shows: the spotlight pick. `feeds` is already sorted screen-share
  // first, then speaking (see useRemoteFeeds), so index 0 IS the spotlight default, and an explicit
  // pin overrides it — PiP then shows what the attendee chose rather than an unrelated tile.
  const pipFeed = feeds.find((f) => f.key === pinned) || feeds[0] || null;
  useEffect(() => {
    const el = videoRef.current;
    const track = pipFeed?.videoTrack;
    if (!el || !track) return undefined;
    track.attach(el);
    return () => {
      try {
        track.detach(el);
      } catch {
        /* the publication went away first */
      }
    };
  }, [pipFeed]);

  // ── teardown ────────────────────────────────────────────────────────────────
  const disconnect = useCallback(() => {
    roomRef.current?.disconnect();
    roomRef.current = null;
    videoPubRef.current = null;
  }, []);

  // Disconnect on unmount AND when the event changes. Leaving the room open would keep the
  // attendee counted as present in an event they navigated away from.
  useEffect(() => disconnect, [disconnect, eventId]);

  // Leaving the live state (the broadcast ended) tears the room down rather than leaving a
  // dead <video> attached to a room nobody is publishing to.
  useEffect(() => {
    if (!live && roomRef.current) {
      disconnect();
      setPhase(IDLE);
    }
  }, [live, disconnect]);

  // ── connect ─────────────────────────────────────────────────────────────────
  const connect = useCallback(async () => {
    if (roomRef.current) return;
    setPhase(CONNECTING);
    setError(null);
    try {
      // Token first: a 409 here ("not live yet") is the common case and costs nothing,
      // whereas downloading the SDK before finding that out wastes the attendee's data.
      const { data } = await api.get(`/events/${eventId}/playback`);
      const sdk = await import("livekit-client");
      sdkRef.current = sdk;
      const { Room, RoomEvent, Track, VideoQuality } = sdk;

      const room = new Room({
        // Adaptive bitrate: LiveKit picks the simulcast layer from the element's real size
        // and the measured downlink, and pauses tracks scrolled out of view.
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      room
        .on(RoomEvent.TrackSubscribed, (track, publication) => {
          if (track.kind === Track.Kind.Video) {
            // Kept for the stats sampler and the quality picker, which both need A publication —
            // any of them measures the same connection. The PICTURE is rendered by StageView,
            // which attaches every video track it is given; attaching one here as well is what
            // used to make a panel event show a single arbitrary speaker.
            videoPubRef.current = publication;
            // Re-apply a pinned quality across a republish (a host switching cameras
            // produces a new publication that would otherwise revert to auto).
            if (quality !== "auto") publication.setVideoQuality(VideoQuality[quality.toUpperCase()]);
          } else if (track.kind === Track.Kind.Audio) {
            // Audio stays on ONE element. LiveKit mixes remote audio per track, so attaching each
            // to its own element would work, but volume and mute then need N elements kept in
            // step — and the attendee has one volume slider.
            track.attach(audioRef.current);
          }
          setPhase(PLAYING);
          syncFeeds();
        })
        .on(RoomEvent.TrackUnsubscribed, (track) => {
          track.detach();
          if (track.kind === Track.Kind.Video) videoPubRef.current = null;
          syncFeeds();
        })
        .on(RoomEvent.TrackMuted, syncFeeds)
        .on(RoomEvent.TrackUnmuted, syncFeeds)
        .on(RoomEvent.ParticipantConnected, syncFeeds)
        .on(RoomEvent.ParticipantDisconnected, syncFeeds)
        .on(RoomEvent.ActiveSpeakersChanged, syncFeeds)
        // LiveKit retries on its own; these just tell the attendee it's happening rather
        // than letting the picture freeze with no explanation.
        .on(RoomEvent.Reconnecting, () => setPhase(RECONNECTING))
        .on(RoomEvent.Reconnected, () => setPhase(PLAYING))
        .on(RoomEvent.Disconnected, () => {
          roomRef.current = null;
          videoPubRef.current = null;
          setPhase(IDLE);
        });

      await room.connect(data.livekit_url, data.token);
    } catch (e) {
      disconnect();
      setError(errMsg(e, "Playback could not start"));
      setPhase(FAILED);
    }
  }, [eventId, quality, disconnect, syncFeeds]);

  // The page's "Watch live" button and the player's own play affordance must take the SAME
  // path, or the two can disagree about whether playback started. Exposing start() as an
  // imperative handle (rather than a prop the page increments) keeps that single path
  // without an effect that fires setState during render — connect() is idempotent, so a
  // double click can't open two rooms.
  useImperativeHandle(ref, () => ({ start: connect, stop: disconnect }), [connect, disconnect]);

  // ── real latency ────────────────────────────────────────────────────────────
  // Sampled from the peer connection's nominated candidate pair — the actual network
  // round-trip, not the API socket's. Only runs while playing, so an idle page is silent.
  useEffect(() => {
    // A stale reading isn't cleared here (that would be a setState in the effect body) —
    // the control bar only renders `rtt` while the phase is PLAYING, so it can't be shown.
    if (phase !== PLAYING) return undefined;
    let alive = true;
    const sample = async () => {
      try {
        const report = await videoPubRef.current?.videoTrack?.getRTCStatsReport();
        if (!report || !alive) return;
        for (const s of report.values()) {
          if (s.type === "candidate-pair" && s.nominated && s.currentRoundTripTime != null) {
            setRtt(Math.round(s.currentRoundTripTime * 1000));
            return;
          }
        }
      } catch {
        // Stats are best-effort; a browser that won't report them just shows no number.
      }
    };
    sample();
    const id = setInterval(sample, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [phase]);

  // ── element wiring ──────────────────────────────────────────────────────────
  // Volume lives on the audio element (LiveKit delivers audio as its own track).
  useEffect(() => {
    const el = audioRef.current;
    if (el) { el.volume = volume / 100; el.muted = muted; }
  }, [volume, muted]);

  useEffect(() => {
    const onFs = () => setFs(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // Buffering is the video element's own stall signal — the only source that knows the
  // decoder actually ran dry, as opposed to the network merely looking slow.
  useEffect(() => {
    const el = videoRef.current;
    if (!el) return undefined;
    const on = () => setBuffering(true);
    const off = () => setBuffering(false);
    el.addEventListener("waiting", on);
    el.addEventListener("stalled", on);
    el.addEventListener("playing", off);
    el.addEventListener("canplay", off);
    const onPip = () => setPip(Boolean(document.pictureInPictureElement));
    el.addEventListener("enterpictureinpicture", onPip);
    el.addEventListener("leavepictureinpicture", onPip);
    return () => {
      el.removeEventListener("waiting", on);
      el.removeEventListener("stalled", on);
      el.removeEventListener("playing", off);
      el.removeEventListener("canplay", off);
      el.removeEventListener("enterpictureinpicture", onPip);
      el.removeEventListener("leavepictureinpicture", onPip);
    };
  }, []);

  // ── controls ────────────────────────────────────────────────────────────────
  const toggleFs = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrapRef.current?.requestFullscreen?.();
  };

  const togglePip = async () => {
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture();
      else await videoRef.current?.requestPictureInPicture();
    } catch {
      // Denied or unsupported — the button simply does nothing rather than throwing.
    }
  };

  const pickQuality = async (id) => {
    setQuality(id);
    setQualityOpen(false);
    const pub = videoPubRef.current;
    if (!pub) return;
    const { VideoQuality } = await import("livekit-client");
    // ponytail: "Auto" re-arms adaptiveStream by asking for the top layer and letting it
    // scale back down — livekit-client v2 has no explicit unpin. Swap for the real call if
    // one lands; the UI contract doesn't change.
    pub.setVideoQuality(VideoQuality[id === "auto" ? "HIGH" : id.toUpperCase()]);
  };

  const pipSupported = typeof document !== "undefined" && document.pictureInPictureEnabled;
  const showOverlay = phase === IDLE || phase === FAILED;

  return (
    <div
      ref={wrapRef}
      className={cx(
        "group relative aspect-video w-full overflow-hidden rounded-xl bg-black",
        "ring-1 ring-slate-200 dark:ring-white/10"
      )}
    >
      {/* Poster — the event banner until real frames arrive, so the stage is never an
          empty black rectangle while connecting. */}
      {poster && phase !== PLAYING && (
        <img
          src={poster}
          alt=""
          className="absolute inset-0 h-full w-full object-cover opacity-40"
        />
      )}

      {/* The stage. Every publisher, composed by the attendee's chosen layout — a screen share
          claims the large slot in spotlight, so when somebody presents everybody sees it. */}
      {stageReady && (
        <StageView
          feeds={feeds}
          layout={layout}
          pinned={pinned}
          onPin={setPinned}
          className={cx("h-full w-full", layout === "grid" && "p-2")}
        />
      )}

      {/* Always mounted, and HIDDEN once the stage renders.
          Two jobs: it is the surface before any track arrives (so the poster has something to sit
          behind), and it is the Picture-in-Picture source — the PiP API needs one element, and
          LiveKit allows the same track to be attached to a second one. Without this, turning the
          stage into a grid would have silently removed PiP. */}
      <video
        ref={videoRef}
        className={cx(
          "h-full w-full object-contain",
          (phase !== PLAYING || stageReady) && "pointer-events-none absolute inset-0 opacity-0"
        )}
        playsInline
        autoPlay
        muted
        aria-hidden={stageReady}
        aria-label={title ? `${title} — live video` : "Live video"}
      />
      {/* Audio is a separate LiveKit track. Kept off the <video> element so volume and mute
          stay independent of the muted-autoplay the video element needs. */}
      <audio ref={audioRef} autoPlay />

      {/* Live badge + viewer count. `viewers` is null until the socket reports it — and 0
          is a real answer, so the nullish check must not be a truthiness check. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 flex items-start justify-between p-4">
        <span
          className={cx(
            "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide",
            live ? "bg-rose-600 text-white" : "bg-white/15 text-white backdrop-blur"
          )}
        >
          {live && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white motion-reduce:animate-none" />}
          {live ? "Live" : "Offline"}
        </span>
        {viewers != null && (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-black/50 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur">
            <FiEye className="text-xs" />
            <span className="zk-tnum">{viewers.toLocaleString()}</span> watching
          </span>
        )}
      </div>

      {/* Buffering / reconnecting — one spinner, never two states at once. */}
      {(buffering || phase === CONNECTING || phase === RECONNECTING) && (
        <div className="absolute inset-0 z-20 grid place-items-center bg-black/40">
          <div className="flex flex-col items-center gap-3">
            <span className="h-10 w-10 animate-spin rounded-full border-2 border-white/30 border-t-white motion-reduce:animate-none" />
            <p className="text-xs font-medium text-white/80">
              {phase === RECONNECTING ? "Reconnecting…" : phase === CONNECTING ? "Connecting…" : "Buffering…"}
            </p>
          </div>
        </div>
      )}

      {/* Idle / failed overlay */}
      {showOverlay && (
        <div className="absolute inset-0 z-20 grid place-items-center bg-black/35 px-6 text-center">
          {phase === FAILED ? (
            <div className="flex flex-col items-center gap-3">
              <FiAlertTriangle className="text-3xl text-amber-400" />
              <p className="max-w-sm text-sm font-medium text-white">{error}</p>
              <button
                onClick={connect}
                className="inline-flex items-center gap-2 rounded-lg bg-white/15 px-4 py-2 text-sm font-semibold text-white backdrop-blur transition hover:bg-white/25"
              >
                <FiRotateCcw /> Try again
              </button>
            </div>
          ) : (
            <button
              onClick={connect}
              disabled={!live}
              aria-label={live ? "Play live stream" : "Stream is not live yet"}
              className="grid h-16 w-16 place-items-center rounded-full bg-white/15 text-white backdrop-blur transition hover:scale-105 hover:bg-white/25 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100 motion-reduce:hover:scale-100"
            >
              <FiPlay className="ml-1 text-2xl" />
            </button>
          )}
        </div>
      )}

      {/* Control bar — always visible on touch, hover-revealed on pointer devices. */}
      <div className="absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-black/85 via-black/45 to-transparent px-3 pb-2.5 pt-8 opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
        {/* Live progress line. Full and static because a live edge has no seekable range —
            a draggable scrubber here would be a control that cannot do anything. */}
        <div className="mb-2 h-1 w-full overflow-hidden rounded-full bg-white/25">
          <div className={cx("h-full rounded-full bg-rose-500", live ? "w-full" : "w-0")} />
        </div>

        <div className="flex items-center gap-1.5 text-white">
          <button
            onClick={phase === PLAYING ? disconnect : connect}
            disabled={!live}
            aria-label={phase === PLAYING ? "Stop playback" : "Play"}
            className={cx(iconBtn, "disabled:opacity-40")}
          >
            {phase === PLAYING ? <FiPause className="text-lg" /> : <FiPlay className="text-lg" />}
          </button>

          <div className="flex items-center gap-1">
            <button onClick={() => setMuted((m) => !m)} aria-label={muted ? "Unmute" : "Mute"} className={iconBtn}>
              <VolumeIcon muted={muted} volume={volume} />
            </button>
            <input
              type="range"
              min={0}
              max={100}
              value={muted ? 0 : volume}
              onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }}
              aria-label="Volume"
              className="hidden h-1 w-20 cursor-pointer accent-violet-500 sm:block"
            />
          </div>

          <div className="ml-auto flex items-center gap-1.5">
            {/* Layout — only offered when there is more than one feed to arrange. */}
            {stageReady && feeds.length > 1 && (
              <div className="flex items-center gap-0.5" role="group" aria-label="Stage layout">
                {STAGE_LAYOUTS.map((l) => (
                  <button
                    key={l.key}
                    onClick={() => setLayout(l.key)}
                    aria-pressed={layout === l.key}
                    title={l.hint}
                    aria-label={l.label}
                    className={cx(iconBtn, layout === l.key && "bg-white/20 text-white")}
                  >
                    <l.icon className="text-base" />
                  </button>
                ))}
              </div>
            )}

            {/* Latency: a real measurement or nothing. An invented number would be worse
                than the blank it replaces. */}
            <span className="inline-flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide">
              <span className={cx("h-1.5 w-1.5 rounded-full", live ? "animate-pulse bg-rose-500 motion-reduce:animate-none" : "bg-white/40")} />
              Live
              {phase === PLAYING && rtt != null && (
                <span className="font-normal normal-case text-white/60 zk-tnum">{rtt}ms</span>
              )}
            </span>

            <div className="relative">
              <button
                onClick={() => setQualityOpen((o) => !o)}
                aria-label="Stream quality"
                aria-expanded={qualityOpen}
                className={iconBtn}
              >
                <FiSettings className="text-lg" />
              </button>
              {qualityOpen && (
                <div className="absolute bottom-10 right-0 w-36 overflow-hidden rounded-lg border border-white/10 bg-neutral-950/95 py-1 shadow-lg backdrop-blur">
                  {QUALITIES.map((q) => (
                    <button
                      key={q.id}
                      onClick={() => pickQuality(q.id)}
                      className={cx(
                        "flex w-full items-center justify-between px-3 py-1.5 text-left text-xs transition hover:bg-white/10",
                        quality === q.id ? "font-semibold text-white" : "text-white/70"
                      )}
                    >
                      {q.label}
                      {quality === q.id && <span className="h-1.5 w-1.5 rounded-full bg-violet-400" />}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {pipSupported && (
              <button onClick={togglePip} aria-label={pip ? "Exit picture in picture" : "Picture in picture"} className={iconBtn}>
                <MdPictureInPicture className="text-lg" />
              </button>
            )}

            <button onClick={toggleFs} aria-label={fs ? "Exit fullscreen" : "Fullscreen"} className={iconBtn}>
              {fs ? <FiMinimize className="text-lg" /> : <FiMaximize className="text-lg" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
