// client/src/components/watch/StageView.jsx
// The attendee's stage: every publisher LiveKit is delivering, in spotlight, grid or pinned.
//
// This exists because the player attached ONE video element. With a host and two panellists
// publishing, whichever track subscribed last won and the attendee saw a single, arbitrary
// speaker — the other feeds arrived and were thrown away. A panel event was unwatchable.
//
// The layout is the ATTENDEE'S choice, held locally. The host's own composition (`layout` and
// `pinned_identity`) is a broadcast setting that is deliberately NOT in the attendee projection —
// an audience member picking grid over spotlight is a preference, not a change to the show. What
// the room IS doing arrives implicitly: a screen share claims the large slot in spotlight, so when
// somebody presents, everybody sees the presentation.
import { useEffect, useMemo, useRef } from "react";
import { FiUser, FiMicOff, FiVideoOff } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";

/** Columns for N equal tiles, chosen so the last row is never a single stranded tile. */
const cols = (n) => (n <= 1 ? "grid-cols-1" : n <= 4 ? "grid-cols-2" : n <= 9 ? "grid-cols-3" : "grid-cols-4");

const initials = (name = "") =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";

/**
 * One feed. Attaching a LiveKit track to a DOM node is imperative, so it happens in an effect
 * keyed on the track — re-attaching on every render would restart the decoder and flash the tile.
 */
function Feed({ feed, large, onPin, pinned }) {
  const videoRef = useRef(null);

  useEffect(() => {
    const el = videoRef.current;
    const track = feed.videoTrack;
    if (!el || !track) return undefined;
    track.attach(el);
    return () => {
      // detach(el), not detach(): the same track may be attached to a second element (the
      // spotlight and its filmstrip thumbnail), and a bare detach would tear both down.
      try {
        track.detach(el);
      } catch {
        /* already gone with the publication */
      }
    };
  }, [feed.videoTrack]);

  const hasVideo = !!feed.videoTrack && !feed.videoMuted;

  return (
    <div
      className={cx(
        "group relative overflow-hidden rounded-xl bg-neutral-900 ring-1 ring-white/10",
        large ? "aspect-video w-full" : "aspect-video"
      )}
    >
      {/* The element stays mounted even when the camera is off, so turning it back on does not
          have to rebuild the decoder. */}
      <video
        ref={videoRef}
        className={cx("h-full w-full", feed.isScreenShare ? "object-contain" : "object-cover",
          !hasVideo && "opacity-0")}
        playsInline
        autoPlay
        muted
        aria-label={`${feed.name}${feed.isScreenShare ? " — shared screen" : ""}`}
      />

      {!hasVideo && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="text-center">
            <span className={cx(
              "grid place-items-center rounded-full bg-white/10 font-semibold text-white",
              large ? "h-20 w-20 text-2xl" : "h-12 w-12 text-sm"
            )}>
              {initials(feed.name)}
            </span>
            {large && <p className="mt-2 text-sm text-white/70">Camera off</p>}
          </div>
        </div>
      )}

      {/* Speaking ring — the fastest read on a busy stage. */}
      {feed.speaking && (
        <span className="pointer-events-none absolute inset-0 rounded-xl ring-2 ring-emerald-400" aria-hidden="true" />
      )}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-black/70 to-transparent p-2">
        <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-medium text-white">
          <span className="truncate">{feed.name}</span>
          {feed.isScreenShare && (
            <span className="shrink-0 rounded bg-white/20 px-1 text-[10px] uppercase">screen</span>
          )}
          {feed.audioMuted && <FiMicOff className="shrink-0 text-white/70" aria-label="Muted" />}
          {!hasVideo && !large && <FiVideoOff className="shrink-0 text-white/50" aria-hidden="true" />}
        </span>
      </div>

      {onPin && (
        <button
          type="button"
          onClick={() => onPin(pinned ? "" : feed.key)}
          aria-pressed={pinned}
          title={pinned ? `Unpin ${feed.name}` : `Pin ${feed.name}`}
          className={cx(
            "absolute right-2 top-2 grid h-7 w-7 place-items-center rounded-lg text-white transition",
            pinned ? "bg-violet-600" : "bg-black/50 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100",
            focusRing
          )}
        >
          <FiUser className="text-sm" />
        </button>
      )}
    </div>
  );
}

export default function StageView({ feeds = [], layout = "spotlight", pinned = "", onPin, className }) {
  const { primary, rest } = useMemo(() => {
    if (!feeds.length) return { primary: null, rest: [] };
    if (layout === "grid") return { primary: null, rest: feeds };
    // Spotlight priority: an explicit pin, then whoever is presenting, then whoever is speaking,
    // then the first feed. Each step falls through deliberately — a pin whose publisher left must
    // not blank the stage.
    const lead =
      feeds.find((f) => f.key === pinned)
      || feeds.find((f) => f.isScreenShare)
      || feeds.find((f) => f.speaking)
      || feeds[0];
    return { primary: lead, rest: feeds.filter((f) => f !== lead) };
  }, [feeds, layout, pinned]);

  if (!feeds.length) return null;

  if (primary) {
    return (
      <div className={cx("space-y-2", className)}>
        <Feed feed={primary} large onPin={onPin} pinned={primary.key === pinned} />
        {rest.length > 0 && (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-5">
            {rest.map((f) => (
              <Feed key={f.key} feed={f} onPin={onPin} pinned={f.key === pinned} />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={cx("grid gap-2", cols(feeds.length), className)}>
      {feeds.map((f) => (
        <Feed key={f.key} feed={f} onPin={onPin} pinned={f.key === pinned} />
      ))}
    </div>
  );
}
