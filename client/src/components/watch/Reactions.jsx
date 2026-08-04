// client/src/components/watch/Reactions.jsx
// Floating reactions: the six-emoji bar an attendee taps, and the animation everybody sees.
//
// Two things that look like one feature and are engineered differently:
//
//   * The BAR sends `reaction.send`, which the server validates against an allow-list, throttles
//     per identity and broadcasts. Never persisted — see services/attendee.py for why one audit row
//     per clap is a self-inflicted write storm at 10,000 attendees. The running totals ARE kept,
//     in the bus, and the analytics sampler persists the aggregate.
//
//   * The ANIMATION subscribes to raw envelopes through a callback (useViewerEvent.onReaction), not
//     to reducer state. In a full room this fires many times a second, and routing each one through
//     a reducer would re-render the whole page per tap. Here it touches one ref and one short list.
import { useCallback, useEffect, useRef, useState } from "react";
import { cx, focusRing } from "../../ui/tokens";
import { REACTIONS } from "../../data/attendee";

// How long one emoji stays on screen, and the ceiling on how many float at once. The cap matters:
// a 10,000-person room can produce hundreds per second, and every one would otherwise be a DOM node.
const FLIGHT_MS = 2600;
const MAX_IN_FLIGHT = 24;

let seq = 0;

export default function Reactions({ onReaction, send, enabled = true, totals = {}, className }) {
  const [flying, setFlying] = useState([]);
  const timers = useRef(new Set());

  useEffect(() => () => {
    timers.current.forEach(clearTimeout);
    timers.current.clear();
  }, []);

  const launch = useCallback((emoji) => {
    seq += 1;
    const id = seq;
    // Horizontal jitter so simultaneous reactions don't stack into one column. Deterministic per
    // id rather than random, which keeps this pure enough for the render path.
    const left = 8 + ((id * 37) % 78);
    setFlying((prev) => [...prev.slice(-(MAX_IN_FLIGHT - 1)), { id, emoji, left }]);
    const t = setTimeout(() => {
      setFlying((prev) => prev.filter((f) => f.id !== id));
      timers.current.delete(t);
    }, FLIGHT_MS);
    timers.current.add(t);
  }, []);

  // Everybody else's reactions.
  useEffect(() => {
    if (!onReaction) return undefined;
    return onReaction((data) => launch(data.emoji));
  }, [onReaction, launch]);

  const tap = (emoji) => {
    if (!enabled) return;
    // Optimistic: the animation starts on the tap, not on the round trip. The server echo also
    // arrives and animates — which is correct, because it confirms it reached the room.
    launch(emoji);
    send("reaction.send", { emoji });
  };

  const total = Object.values(totals).reduce((s, n) => s + n, 0);

  return (
    <div className={cx("relative", className)}>
      {/* The flight path. Sits above the player, ignores pointer events, and is hidden from
          assistive tech — it is decoration, and the counts below carry the same information. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-full h-56 overflow-hidden"
      >
        {flying.map((f) => (
          <span
            key={f.id}
            className="absolute bottom-0 text-2xl motion-safe:animate-[zk-rise_2.6s_ease-out_forwards] motion-reduce:opacity-0"
            style={{ left: `${f.left}%`, "--zk-rise-tilt": `${(f.id % 5) * 6 - 12}deg` }}
          >
            {f.emoji}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {REACTIONS.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={() => tap(emoji)}
            disabled={!enabled}
            aria-label={`React with ${emoji}${totals[emoji] ? ` (${totals[emoji]} so far)` : ""}`}
            title={enabled ? `React ${emoji}` : "Reactions are turned off for this event"}
            className={cx(
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-base transition",
              "border-slate-200 hover:scale-110 hover:border-violet-300 dark:border-white/10 dark:hover:border-violet-500/40",
              "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100",
              "motion-reduce:hover:scale-100",
              focusRing
            )}
          >
            {emoji}
            {totals[emoji] > 0 && (
              <span className="text-[11px] font-semibold tabular-nums text-slate-500 dark:text-neutral-400">
                {totals[emoji] > 999 ? `${Math.floor(totals[emoji] / 1000)}k` : totals[emoji]}
              </span>
            )}
          </button>
        ))}
        {total > 0 && (
          <span className="ml-1 text-xs text-slate-400" aria-live="polite">
            {total.toLocaleString()} reaction{total === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </div>
  );
}
