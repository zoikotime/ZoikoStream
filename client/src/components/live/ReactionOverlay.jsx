// client/src/components/live/ReactionOverlay.jsx
// Google-Meet-style floating reactions, layered over a live video.
//
// ONE component for BOTH surfaces — the viewer's player (pages/watch/EventWatch.jsx) and
// the host's Producer Console monitor (components/host/StudioStage.jsx) — because they are
// the same thing seen from two seats, and the host's copy is the one that is mandatory:
// every viewer tap has to become a visible emoji on the console in real time.
//
// The two surfaces differ in ONE respect, and only visually: `lane`. The console keeps the
// full-width scatter (a producer monitors the whole frame and wants the reactions spread
// across it); the viewer's player uses the approved "reaction stream" — a narrow column
// rising out of the lower-left corner, clear of the speaker. Everything else — the
// payload, the channel, the lifetime, the concurrency cap — is identical on both.
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
import { useEffect, useRef, useState } from "react";
import useInterval from "../../hooks/useInterval";
import { REACTION_EMOJI } from "../../data/reactions";
import { cx } from "../../ui/tokens";

// Must match the `zk-reaction-float` / `zk-reaction-stream` animations in index.css: the
// CSS decides when the emoji has finished fading, this decides when the node leaves the
// DOM, and a mismatch either clips the fade or leaves an invisible node parked over the
// video. 3s sits in the middle of the 2.5-4s band a reaction should live for.
//
// On the producer's monitor this IS the duration of every float. On the viewer's lane it is
// the CENTRE of a per-item band (see STREAM_SPEED): each emoji is handed its own duration
// and its own expiry, because a flat 3s makes a burst climb in visible lockstep.
export const REACTION_DURATION_MS = 3000;

// How many can be on screen at once. Ten viewers reacting together is the point of the
// feature; two hundred is a wall of emoji over the picture the producer is monitoring, and
// the oldest are the ones already fading out, so they are what gets dropped.
const MAX_CONCURRENT = 28;

// How often finished items are swept. ONE interval for the whole overlay (only running
// while something is on screen) rather than a setTimeout per item: at a few reactions a
// second the per-item timers were the expensive part, not the animation.
const SWEEP_MS = 200;

// The largest start delay an item can be handed, and the slack added on top of it when
// computing expiry — so the last-starting emoji of a batch still finishes its fade before
// the sweep is allowed to take its node away.
const MAX_DELAY_MS = 220;
const EXPIRY_SLACK_MS = 260;

// Small random spread so simultaneous reactions read as a handful of separate people
// rather than one emoji rendered five times in the same place.
const rand = (min, max) => min + Math.random() * (max - min);
const round1 = (n) => Math.round(n * 10) / 10;
const round2 = (n) => Math.round(n * 100) / 100;

// ── the viewer's left-hand lane (lane="left") ──────────────────────────────────────────
// The approved viewer design is a controlled stream: every emoji is born in the lower-left
// corner and rises in a narrow column beside the picture, rather than anywhere across the
// frame. These are PERCENTAGES of the player, so the lane keeps its shape and its position
// in a phone-sized player, a desktop one and in fullscreen — as does the rise, which
// index.css sizes against the player itself (see `.zk-reaction-lane`).
// The lane is a share of the player's WIDTH while the glyph is sized off its HEIGHT, so on
// a 16:9 player the lane is worth about half as many emoji-widths at phone size as at
// desktop size — which is where a burst at the concurrency cap starts to read as a clump
// rather than a stream. Hence a lane wide enough to hold the cap's worth of emoji on a
// phone, and a glyph floor (index.css) low enough to fit inside it. Still comfortably
// inside the left fifth of the frame: the speaker sits in the middle third.
const LANE_SPAN_PCT = [3, 19];    // % from the left edge the lane occupies, end to end
const LANE_JITTER_PCT = 1.2;      // % of wander on top of the chosen spot, so it isn't a grid
const LANE_BOTTOM_PCT = [3, 12];  // % above the bottom edge where an emoji is born

// The lane spot for the next emoji advances by the golden-ratio conjugate and wraps. This
// replaced a `column % 4` rotation, which spaced a burst evenly but cycled through the same
// four spots forever — visible as a repeating pattern the moment more than four reactions
// land. An irrational step never repeats, still covers the lane evenly at any burst size,
// and (because the step is never near 0 or 1) guarantees CONSECUTIVE emoji land at least
// ~38% of the lane apart, which is the anti-stacking property the rotation was there for.
const GOLDEN_STEP = 0.6180339887;

// Per-item speed, as a multiple of the overlay's nominal duration. This is the main thing
// that stops the stream reading as one animation played N times: at a flat duration every
// emoji climbs in lockstep, and a burst looks like a formation. Centred on 1 and bounded so
// the band lands at ~2.55-3.8s in production — inside the 2.5-4s a reaction should live for.
const STREAM_SPEED = [0.85, 1.27];

// Per-item travel, as a multiple of the rise index.css computes from the player's height:
// they must not all stop at the same altitude. Bounded well under 1.2 so the top of the
// climb stays inside the frame in a short player.
const STREAM_TRAVEL = [0.82, 1.16];

// Per-item tilt, in degrees. Small on purpose — enough to stop the glyphs looking stamped,
// far short of tumbling. The keyframes oscillate between +rot and -rot and land back at 0.
const STREAM_ROTATE_DEG = 9;

// The longest an item can occupy the DOM: the slowest float, started at the largest delay,
// plus the sweep's slack. Exported so a test can settle the overlay without hard-coding the
// arithmetic — and so it is obvious that varying duration per item moved this number.
export const REACTION_MAX_LIFETIME_MS =
  Math.round(REACTION_DURATION_MS * STREAM_SPEED[1]) + MAX_DELAY_MS + EXPIRY_SLACK_MS;

/**
 * One of three sideways personalities, picked per emoji. The reference stream is not a
 * uniform spray: some reactions go almost straight up, some lean left, some lean right, and
 * that mix is what reads as a crowd rather than a particle emitter. A single symmetric
 * random range (what this used to be) averages every item towards the same gentle wobble.
 *
 * @returns {{drift: number, sway: number}} px — `drift` is net displacement at the top,
 *   `sway` the amplitude of the S-shaped wander on the way (its sign sets which way it
 *   leans first, so two emoji sharing a spot never trace the same line).
 */
function driftProfile() {
  const roll = Math.random();
  const sign = Math.random() < 0.5 ? -1 : 1;
  // Straight up: barely any net displacement, and a narrow wander around the vertical.
  if (roll < 0.34) return { drift: Math.round(rand(-4, 4)), sway: Math.round(rand(4, 8)) * sign };
  // Leans left / leans right. Bounded so that even at full drift an emoji finishes inside
  // the left-hand zone it was born in — it must never end up over the speaker.
  const lean = roll < 0.67 ? -1 : 1;
  return { drift: Math.round(rand(9, 22)) * lean, sway: Math.round(rand(6, 14)) * sign };
}

/**
 * Where one emoji starts, how far and how fast it travels, and how far it is allowed to
 * wander on the way up. Purely presentational — the payload, the channel and the transport
 * never reach this function.
 *
 * @param {"spread"|"left"} lane
 * @param {number} spot - 0..1, the lane position for this emoji (see GOLDEN_STEP). Ignored
 *   by the producer's full-width scatter.
 * @param {number} durationMs - the overlay's nominal duration; the left lane varies each
 *   item around it rather than replacing it, so an override still shortens every float.
 */
function spawnGeometry(lane, spot, durationMs) {
  if (lane !== "left") {
    // The producer's monitor, unchanged: the whole width of the frame is fair game, on one
    // shared clock. The console is a monitoring surface, and the reference this enhancement
    // follows is a viewer frame — so nothing here moves.
    return {
      left: round1(rand(8, 84)), bottom: 10, drift: Math.round(rand(-36, 36)),
      sway: 0, scale: 1, rotate: 0, travel: 1, duration: durationMs,
    };
  }
  const [from, to] = LANE_SPAN_PCT;
  return {
    left: round1(from + spot * (to - from) + rand(-LANE_JITTER_PCT, LANE_JITTER_PCT)),
    bottom: round1(rand(LANE_BOTTOM_PCT[0], LANE_BOTTOM_PCT[1])),
    ...driftProfile(),
    // Slight size variation, so a burst is a handful of people rather than one emoji
    // rendered five times.
    scale: round2(rand(0.85, 1.15)),
    rotate: Math.round(rand(-STREAM_ROTATE_DEG, STREAM_ROTATE_DEG)),
    travel: round2(rand(STREAM_TRAVEL[0], STREAM_TRAVEL[1])),
    duration: Math.round(durationMs * rand(STREAM_SPEED[0], STREAM_SPEED[1])),
  };
}

/**
 * @param {object} props
 * @param {{subscribe: (fn: (reaction: object) => void) => () => void}} [props.channel]
 *   The page's reaction channel (hooks/useReactionChannel.js). Omitted/null renders an
 *   inert layer — that is the "reactions turned off for this event" case, and it must not
 *   throw.
 * @param {"spread"|"left"} [props.lane="spread"] - where the emoji are allowed to travel.
 *   "spread" is the Producer Console's full-width scatter; "left" is the viewer player's
 *   lower-left stream. Visual only: it changes nothing about what arrives or when.
 * @param {number} [props.durationMs] - overridable only so a test can shorten the life of
 *   an item; production always uses REACTION_DURATION_MS to stay in step with the CSS.
 */
export default function ReactionOverlay({
  channel, durationMs = REACTION_DURATION_MS, lane = "spread", className = "",
}) {
  const [items, setItems] = useState([]);
  // Where in the lane the next left-lane emoji is born, as 0..1 advanced by GOLDEN_STEP. A
  // ref, not state: it moves on every single reaction and must never be a reason to
  // re-render. Seeded at random so two viewers watching the same event don't get identical
  // streams, and so a remount doesn't always restart from the same edge of the lane.
  const spot = useRef(Math.random());

  useEffect(() => {
    if (!channel?.subscribe) return undefined;
    return channel.subscribe((payload) => {
      // An unknown key (a newer server, a malformed frame) is dropped rather than
      // rendered — `undefined` painted over the video would be worse than nothing.
      const emoji = REACTION_EMOJI[payload?.reaction];
      if (!emoji) return;
      spot.current = (spot.current + GOLDEN_STEP) % 1;
      const geometry = spawnGeometry(lane, spot.current, durationMs);
      const delay = Math.round(rand(0, MAX_DELAY_MS));   // so a simultaneous batch staggers
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
          ...geometry,
          delay,
          // This item's OWN clock, because the left lane hands every emoji its own duration:
          // a single shared expiry would sweep the slow ones away mid-fade and leave the
          // fast ones parked invisible over the video.
          expiresAt: Date.now() + delay + geometry.duration + EXPIRY_SLACK_MS,
        },
      ].slice(-MAX_CONCURRENT));
    });
  }, [channel, durationMs, lane]);

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

  const streaming = lane === "left";

  return (
    <div
      // pointer-events-none is load-bearing, not cosmetic: this layer covers the whole
      // monitor, including the host's fullscreen/expand button and the publish-retry
      // control underneath it. z-index is passed in by the caller so each surface can slot
      // the layer between its own video and its own chrome.
      className={cx(
        "pointer-events-none absolute inset-0 overflow-hidden",
        // Makes the layer a CSS size container, so index.css can size the rise against the
        // PLAYER (cqh) instead of the viewport. That is what keeps the stream reading the
        // same when the player is resized, the window is resized, or it goes fullscreen —
        // the layer is a descendant of the element that fullscreens on both surfaces.
        streaming && "zk-reaction-lane",
        className,
      )}
      data-testid="reaction-overlay"
      data-lane={lane}
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
          className={cx(
            streaming ? "zk-reaction-stream" : "zk-reaction-float",
            "pointer-events-none absolute select-none text-3xl leading-none drop-shadow-[0_2px_6px_rgba(0,0,0,0.45)]",
          )}
          style={{
            // Position is set once, at birth, and never animated: only transform and
            // opacity move, so a screenful of these stays on the compositor and never
            // triggers layout while it is on screen.
            left: `${item.left}%`,
            bottom: `${item.bottom}%`,
            animationDelay: `${item.delay}ms`,
            // Per item on the viewer's lane, so the stream is a crowd climbing at its own
            // speeds rather than one animation played N times; the same value times the
            // node's removal above.
            animationDuration: `${item.duration}ms`,
            // Read by the keyframes (index.css) so every emoji takes its own path up.
            "--zk-reaction-drift": `${item.drift}px`,
            "--zk-reaction-sway": `${item.sway}px`,
            "--zk-reaction-scale": item.scale,
            "--zk-reaction-rot": `${item.rotate}deg`,
            "--zk-reaction-travel": item.travel,
          }}
        >
          {item.emoji}
        </span>
      ))}
    </div>
  );
}
