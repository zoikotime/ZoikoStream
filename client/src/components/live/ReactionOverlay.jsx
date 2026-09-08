// client/src/components/live/ReactionOverlay.jsx
// Google-Meet-style floating reactions, layered over a live video.
//
// ONE component for BOTH surfaces — the viewer's player (pages/watch/EventWatch.jsx) and
// the host's Producer Console monitor (components/host/StudioStage.jsx) — because they are
// the same thing seen from two seats, and the host's copy is the one that is mandatory:
// every viewer tap has to become a visible emoji on the console in real time.
//
// Each item arrives as ONE server envelope (`reactions`/`reaction.burst`, published by
// server/app/services/moderation.py::_reaction_add), floats up, fades, and is dropped from
// the DOM. There is no count, no aggregation and nothing persisted: the payload carries an
// emoji key, the event id and a unique instance id, and deliberately no viewer identity —
// the host sees "somebody sent ❤️", never who.
//
// Nothing here reads or writes React state on the page that hosts it. Bursts live in this
// component, fed through hooks/useReactionChannel.js, so a busy audience re-renders this
// overlay and nothing else — see that file for why that matters on the host console.
import { useEffect, useState } from "react";
import useInterval from "../../hooks/useInterval";
import { REACTION_EMOJI } from "../../data/reactions";
import { cx } from "../../ui/tokens";

// Must match the `zk-reaction-float` animation in index.css: the CSS decides when the
// emoji has finished fading, this decides when the node leaves the DOM, and a mismatch
// either clips the fade or leaves an invisible node parked over the video.
// 3s sits in the middle of the 2.5-4s band a reaction should live for.
export const REACTION_DURATION_MS = 3000;

// How many can be on screen at once. Ten viewers reacting together is the point of the
// feature; two hundred is a wall of emoji over the picture the producer is monitoring, and
// the oldest are the ones already fading out, so they are what gets dropped.
const MAX_CONCURRENT = 28;

// How often finished items are swept. ONE interval for the whole overlay (only running
// while something is on screen) rather than a setTimeout per item: at a few reactions a
// second the per-item timers were the expensive part, not the animation.
const SWEEP_MS = 200;

// Small random spread so simultaneous reactions read as a handful of separate people
// rather than one emoji rendered five times in the same place.
const rand = (min, max) => min + Math.random() * (max - min);

/**
 * @param {object} props
 * @param {{subscribe: (fn: (reaction: object) => void) => () => void}} [props.channel]
 *   The page's reaction channel (hooks/useReactionChannel.js). Omitted/null renders an
 *   inert layer — that is the "reactions turned off for this event" case, and it must not
 *   throw.
 * @param {number} [props.durationMs] - overridable only so a test can shorten the life of
 *   an item; production always uses REACTION_DURATION_MS to stay in step with the CSS.
 */
export default function ReactionOverlay({ channel, durationMs = REACTION_DURATION_MS, className = "" }) {
  const [items, setItems] = useState([]);

  useEffect(() => {
    if (!channel?.subscribe) return undefined;
    return channel.subscribe((payload) => {
      // An unknown key (a newer server, a malformed frame) is dropped rather than
      // rendered — `undefined` painted over the video would be worse than nothing.
      const emoji = REACTION_EMOJI[payload?.reaction];
      if (!emoji) return;
      setItems((current) => [
        ...current,
        {
          // Server-minted per reaction INSTANCE, so two taps of the same emoji in the same
          // millisecond are two independent floats. The suffix keeps React's key unique
          // even if a server ever repeated an id, and the fallback covers a frame that
          // arrived without one.
          id: `${payload.id || "r"}-${Math.random().toString(36).slice(2, 8)}`,
          emoji,
          reaction: payload.reaction,
          left: rand(8, 84),          // % across the video, inset so wide emoji don't clip
          drift: Math.round(rand(-36, 36)),   // px of horizontal wander on the way up
          delay: Math.round(rand(0, 220)),    // ms, so a simultaneous batch staggers
          expiresAt: Date.now() + durationMs + 260,   // + the largest possible delay
        },
      ].slice(-MAX_CONCURRENT));
    });
  }, [channel, durationMs]);

  // Removal is by wall-clock expiry, not by animation event: an `animationend` listener
  // never fires for a tab that was backgrounded mid-float, which would leave the node
  // parked over the video until the next reaction happened to re-render the list.
  useInterval(() => {
    const now = Date.now();
    setItems((current) => {
      const live = current.filter((item) => item.expiresAt > now);
      return live.length === current.length ? current : live;   // same array = no re-render
    });
  }, SWEEP_MS, items.length > 0);

  return (
    <div
      // pointer-events-none is load-bearing, not cosmetic: this layer covers the whole
      // monitor, including the host's fullscreen/expand button and the publish-retry
      // control underneath it. z-index is passed in by the caller so each surface can slot
      // the layer between its own video and its own chrome.
      className={cx("pointer-events-none absolute inset-0 overflow-hidden", className)}
      data-testid="reaction-overlay"
      // Decorative by design. A screen reader announcing every reaction in the audience
      // would bury the chat and Q&A updates that actually carry content, and the emoji
      // itself is the entire message.
      aria-hidden="true"
    >
      {items.map((item) => (
        <span
          key={item.id}
          id={`zk-reaction-${item.id}`}
          data-reaction={item.reaction}
          className="zk-reaction-float pointer-events-none absolute bottom-[10%] select-none text-3xl leading-none drop-shadow-[0_2px_6px_rgba(0,0,0,0.45)]"
          style={{
            left: `${item.left}%`,
            animationDelay: `${item.delay}ms`,
            animationDuration: `${durationMs}ms`,
            // Read by the keyframes (index.css) so every emoji takes its own path up.
            "--zk-reaction-drift": `${item.drift}px`,
          }}
        >
          {item.emoji}
        </span>
      ))}
    </div>
  );
}
