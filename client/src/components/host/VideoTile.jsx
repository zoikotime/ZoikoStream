import { useEffect, useRef } from "react";
import { FiMic, FiMicOff, FiMonitor, FiVideoOff } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import { initials, accentFor, QUALITY } from "../../data/host";

// One participant tile. Attaches a REAL LiveKit track when there is one, and falls back to an
// avatar when there isn't — which is the honest state for someone connected with their camera
// off, not a placeholder standing in for video that exists.
//
// Attaching is imperative and belongs in an effect: track.attach(el) mutates the element, and
// detaching on unmount is what stops a decoder running for a tile nobody is looking at.
export default function VideoTile({
  participant,          // presence record (name/role/muted/quality/hand) — may be undefined
  videoTrack,           // LiveKit RemoteVideoTrack | LocalVideoTrack | null
  stream,               // a raw MediaStream, for the host's OWN monitor (see below)
  audioTrack,           // attached only for REMOTE participants
  label,
  isLocal = false,
  isScreenShare = false,
  speaking = false,
  pinned = false,
  onPin,
  className = "",
}) {
  const videoEl = useRef(null);
  const audioEl = useRef(null);

  // Two sources, because the host's own monitor is NOT a LiveKit track. It comes straight from
  // useMediaPreview's MediaStream, so the studio shows a camera preview before going live and
  // even when LiveKit is unreachable — the local monitor must never depend on the network.
  useEffect(() => {
    const el = videoEl.current;
    if (!el) return undefined;
    if (stream) {
      el.srcObject = stream;
      return () => { el.srcObject = null; };
    }
    if (!videoTrack) return undefined;
    videoTrack.attach(el);
    return () => {
      // detach(el) rather than detach(): the same track can legitimately be shown in two tiles
      // (a spotlight plus its filmstrip thumbnail), and the bare form would rip it out of both.
      try {
        videoTrack.detach(el);
      } catch {
        /* already detached */
      }
    };
  }, [videoTrack, stream]);

  useEffect(() => {
    const el = audioEl.current;
    // Never attach LOCAL audio: monitoring your own microphone through the speakers is a
    // feedback loop.
    if (isLocal || !audioTrack || !el) return undefined;
    audioTrack.attach(el);
    return () => {
      try {
        audioTrack.detach(el);
      } catch {
        /* already detached */
      }
    };
  }, [audioTrack, isLocal]);

  const name = label || participant?.name || "Participant";
  const muted = participant?.muted;
  const q = QUALITY[participant?.quality] || null;
  const hasVideo = !!videoTrack || !!stream;

  return (
    <div
      className={cx(
        "group relative overflow-hidden rounded-xl bg-slate-900 ring-1 transition",
        speaking ? "ring-2 ring-emerald-500" : pinned ? "ring-2 ring-violet-500" : "ring-slate-200 dark:ring-slate-800",
        className
      )}
    >
      <video
        ref={videoEl}
        autoPlay
        playsInline
        // The local monitor must be muted; remote audio rides the <audio> element below.
        muted={isLocal}
        className={cx(
          "h-full w-full",
          // A shared screen must never be cropped — object-contain keeps the whole surface,
          // where a face is better filling the tile.
          isScreenShare ? "object-contain" : "object-cover",
          hasVideo ? "opacity-100" : "opacity-0"
        )}
        aria-label={`${name}${isScreenShare ? " — shared screen" : ""}`}
      />
      {!isLocal && <audio ref={audioEl} autoPlay />}

      {!hasVideo && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex flex-col items-center gap-2">
            <span
              className={cx(
                "grid h-14 w-14 place-items-center rounded-full text-base font-semibold",
                ACCENT[accentFor(participant?.identity || name)].chip
              )}
              aria-hidden="true"
            >
              {initials(name)}
            </span>
            <span className="inline-flex items-center gap-1 text-[11px] text-slate-400">
              <FiVideoOff aria-hidden="true" /> camera off
            </span>
          </div>
        </div>
      )}

      {isScreenShare && (
        <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-md bg-emerald-600/90 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
          <FiMonitor aria-hidden="true" /> Screen
        </span>
      )}

      {q && (
        <span
          className={cx("absolute right-2 top-2 h-2 w-2 rounded-full", q.tone)}
          title={`Connection: ${q.label}`}
        />
      )}

      <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1.5 bg-gradient-to-t from-black/80 to-transparent px-2 py-1.5">
        <span className="min-w-0 truncate text-[11px] font-medium text-white">
          {name}
          {isLocal && <span className="ml-1 text-white/60">(you)</span>}
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          {participant?.hand && <span title="Hand raised" aria-label="Hand raised">✋</span>}
          {muted
            ? <FiMicOff className="text-rose-400" aria-label="Muted" />
            : <FiMic className="text-emerald-400" aria-label="Unmuted" />}
        </span>
      </div>

      {onPin && (
        <button
          type="button"
          onClick={() => onPin(pinned ? "" : participant?.identity || "")}
          // Visible on hover for pointers, always reachable by keyboard.
          className={cx(
            "absolute right-2 bottom-8 rounded-md bg-black/60 px-2 py-1 text-[10px] font-semibold text-white",
            "opacity-0 transition group-hover:opacity-100 focus-visible:opacity-100"
          )}
          aria-label={pinned ? `Unpin ${name}` : `Pin ${name} to the main stage`}
        >
          {pinned ? "Unpin" : "Pin"}
        </button>
      )}
    </div>
  );
}
