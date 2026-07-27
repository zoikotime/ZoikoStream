// client/src/components/watch/VideoPlayer.jsx
// Live broadcast surface: a main tile (the host) plus up to 6 on-stage speaker tiles,
// backed by a real LiveKit room. Also owns the "Raise Hand" self-service request and
// reacts once a moderator promotes this viewer to speaker.
import { useEffect, useRef, useState } from "react";
import {
  FiPlay, FiPause, FiVolume2, FiVolume1, FiVolumeX,
  FiMaximize, FiMinimize, FiSettings, FiRotateCcw, FiBell, FiMic, FiMicOff, FiVideo, FiVideoOff,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { initials } from "../../data/watch";
import { useAuth } from "../../auth/AuthContext";
import { connectEventChat, raiseHand, lowerHand } from "../../lib/chatSocket";
import { useLiveKitRoom, useRoomParticipants } from "../../lib/useLiveKitRoom";
import { notify } from "../../ui/Toast";
import SpeakerTile from "../common/SpeakerTile";

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

// The viewer's own "Raise Hand" control + post-promotion camera/mic prompt. Uses a
// small dedicated socket connection (join only needs stream_id/identity, not chat
// history) so it works independent of whether the chat tab has been opened.
function StageControls({ streamId, displayName, viewerEmail, localCanPublish, room }) {
  const { user } = useAuth();
  const [handRaised, setHandRaised] = useState(false);
  const [camera, setCamera] = useState(false);
  const [mic, setMic] = useState(false);
  const socketRef = useRef(null);
  const wasOnStage = useRef(localCanPublish);

  useEffect(() => {
    if (!displayName) return;
    const token = localStorage.getItem("token");
    const socket = connectEventChat(streamId, user ? { token } : { displayName, email: viewerEmail }, {});
    socketRef.current = socket;
    return () => socket.disconnect();
  }, [streamId, displayName, user, viewerEmail]);

  // The moment LiveKit grants publish rights (via useLiveKitRoom -> ParticipantPermissionsChanged),
  // prompt the viewer to actually go on camera; on demotion, drop their local preview state.
  useEffect(() => {
    if (localCanPublish && !wasOnStage.current) {
      setHandRaised(false);
      notify.success("You've been invited to speak! Enable your camera/mic below.");
    } else if (!localCanPublish && wasOnStage.current) {
      setCamera(false);
      setMic(false);
    }
    wasOnStage.current = localCanPublish;
  }, [localCanPublish]);

  const toggleHand = async () => {
    if (!socketRef.current) return;
    try {
      if (handRaised) {
        await lowerHand(socketRef.current);
        setHandRaised(false);
      } else {
        await raiseHand(socketRef.current);
        setHandRaised(true);
        notify.success("Hand raised — the host has been notified.");
      }
    } catch {
      notify.error("Couldn't reach the event — try again");
    }
  };

  const toggleCamera = async () => {
    if (!room) return;
    const next = !camera;
    try {
      await room.localParticipant.setCameraEnabled(next);
      setCamera(next);
    } catch {
      notify.error("Could not toggle camera");
    }
  };

  const toggleMic = async () => {
    if (!room) return;
    const next = !mic;
    try {
      await room.localParticipant.setMicrophoneEnabled(next);
      setMic(next);
    } catch {
      notify.error("Could not toggle microphone");
    }
  };

  if (!displayName) return null;

  if (localCanPublish) {
    return (
      <div className="flex items-center gap-2">
        <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-semibold text-emerald-400">You're on stage</span>
        <button onClick={toggleCamera} className={cx("grid h-8 w-8 place-items-center rounded-lg", camera ? "bg-white/15 text-white" : "bg-white/5 text-white/50")} aria-label="Toggle camera">
          {camera ? <FiVideo /> : <FiVideoOff />}
        </button>
        <button onClick={toggleMic} className={cx("grid h-8 w-8 place-items-center rounded-lg", mic ? "bg-white/15 text-white" : "bg-white/5 text-white/50")} aria-label="Toggle microphone">
          {mic ? <FiMic /> : <FiMicOff />}
        </button>
      </div>
    );
  }

  return (
    <button
      onClick={toggleHand}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold backdrop-blur transition",
        handRaised ? "bg-amber-500/20 text-amber-300" : "bg-white/10 text-white hover:bg-white/20"
      )}
    >
      <FiBell /> {handRaised ? "Hand raised" : "Raise Hand"}
    </button>
  );
}

export default function VideoPlayer({ event, viewers, liveToken, viewerEmail, guestName }) {
  const { user } = useAuth();
  const displayName = user?.full_name || guestName;

  const isLive = event.status === "Live";
  const isEnded = event.status === "Completed";

  const wrapRef = useRef(null);
  const mainVideoRef = useRef(null);
  const [playing, setPlaying] = useState(isLive);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(80);
  const [progress, setProgress] = useState(32); // replay scrubber (%)
  const [fs, setFs] = useState(false);

  const { room, connected } = useLiveKitRoom(isLive ? liveToken : null);
  const { participants, localCanPublish } = useRoomParticipants(room);

  const hostIdentity = event.host_id ? String(event.host_id) : null;
  const main = participants.find((p) => p.identity === hostIdentity) || participants.find((p) => !p.isLocal) || null;
  const filmstrip = participants.filter((p) => p !== main).slice(0, 6);

  useEffect(() => {
    const onFs = () => setFs(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  useEffect(() => {
    const track = main?.videoTrack;
    const el = mainVideoRef.current;
    if (track && el) track.attach(el);
    return () => track?.detach(el);
  }, [main?.videoTrack]);

  // Volume/mute/play-pause apply to the real element once connected.
  useEffect(() => {
    if (!mainVideoRef.current) return;
    mainVideoRef.current.muted = muted;
    mainVideoRef.current.volume = volume / 100;
  }, [muted, volume, connected]);

  useEffect(() => {
    if (!mainVideoRef.current) return;
    if (playing) mainVideoRef.current.play().catch(() => {});
    else mainVideoRef.current.pause();
  }, [playing, connected]);

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

      {/* Stage content. The <video> element is always mounted so its ref is stable —
          LiveKit can attach the host's track to it the instant it subscribes, regardless
          of whether that happens before or after `connected` state re-renders. */}
      <div className="absolute inset-0 grid place-items-center px-4 text-center">
        <video ref={mainVideoRef} autoPlay playsInline className="absolute inset-0 h-full w-full object-cover" />
        {!(isLive && connected && main?.videoTrack) &&
          (isEnded && !playing ? (
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
                {initials(main?.name || event.host)}
              </span>
              <div>
                <p className="text-sm font-semibold text-white">{main?.name || event.host}</p>
                <p className="text-xs text-white/70">{isLive ? "Connecting…" : "Host"}</p>
              </div>
            </div>
          ))}
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

      {/* Speaker filmstrip */}
      {isLive && filmstrip.length > 0 && (
        <div className="absolute bottom-16 left-3 right-3 z-10 grid grid-cols-3 gap-2 sm:grid-cols-6">
          {filmstrip.map((p) => (
            <SpeakerTile key={p.identity} participant={p} initials={initials} />
          ))}
        </div>
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

          {isLive && (
            <div className="ml-2">
              <StageControls
                streamId={event.id}
                displayName={displayName}
                viewerEmail={viewerEmail}
                localCanPublish={localCanPublish}
                room={room}
              />
            </div>
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
