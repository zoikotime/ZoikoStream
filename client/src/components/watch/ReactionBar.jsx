// client/src/components/watch/ReactionBar.jsx
// Viewer reactions under the player. Tap targets only — NO counts, by design.
//
// A tap sends `reaction.add {key}` on the same live socket that carries chat/Q&A/polls
// (server/app/routers/live.py -> services/moderation.py::_reaction_add). The server turns
// it into one ephemeral `reactions`/`reaction.burst` envelope, which every socket on the
// event — crucially including the host's Producer Console — animates once over the video
// and forgets. So the visible result of a tap is a floating emoji
// (components/live/ReactionOverlay.jsx), not a number.
//
// This replaced a per-emoji counter (👍 11 / ❤️ 0 / …). A total is the wrong reading of
// what a reaction is here: it turns a moment into a scoreboard, it can only ever grow, and
// it told the host almost nothing about what the audience was doing right now. The Google
// Meet model — the reaction appears, floats and is gone — is what this sends instead, and
// nothing in this component or its data now carries a count at all.
import { useEffect, useState } from "react";
import { cx } from "../../ui/tokens";
import { REACTIONS } from "../../data/reactions";

// How long a tapped emoji stays highlighted. Purely a local "you just did that" flash: it
// confirms the tap landed without waiting for the round trip, and it is NOT a "your
// reaction" toggle — every tap is an independent event, so there is nothing to un-react.
const PULSE_MS = 700;

/**
 * @param {object} props
 * @param {(key: string) => void} props.onReact - sends `reaction.add` for this key.
 * @param {boolean} [props.disabled] - true while disconnected, or while the host has turned
 *   reactions off (settings.reactions_enabled === false / a memorial event).
 * @param {boolean} [props.handRaised] - this viewer's own `hand` presence flag (see
 *   EventWatch.jsx, derived from panel.you/panel.participants).
 * @param {() => void} [props.onToggleHand] - sends `participant.hand` for this viewer.
 * @param {boolean} [props.raiseHandVisible] - gated on watch.raise_hand_enabled.
 */
export default function ReactionBar({
  onReact, disabled = false, className = "",
  handRaised = false, onToggleHand, raiseHandVisible = false,
}) {
  // The most recent tap: which emoji, and a monotonic counter so a REPEAT tap on the same
  // emoji is still a new value. One piece of state and one timer for the whole bar — the
  // per-key map this replaced needed a timer per key, and a viewer can only tap one thing
  // at a time anyway.
  const [lastTap, setLastTap] = useState(null);

  // Clears itself, so the highlight is always transient and no timer outlives the bar.
  // Re-armed by each tap (the effect re-runs on a new `lastTap` object), which is what
  // makes the flash follow the latest tap rather than expiring on the first one's clock.
  useEffect(() => {
    if (!lastTap) return undefined;
    const timer = setTimeout(() => setLastTap(null), PULSE_MS);
    return () => clearTimeout(timer);
  }, [lastTap]);

  const tap = (key) => {
    if (disabled) return;
    setLastTap((prev) => ({ key, n: (prev?.n || 0) + 1 }));
    onReact(key);
  };

  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm dark:border-slate-800 dark:bg-slate-900",
        className
      )}
    >
      {REACTIONS.map((r) => {
        const flashing = lastTap?.key === r.key;
        return (
        <button
          key={r.key}
          type="button"
          onClick={() => tap(r.key)}
          disabled={disabled}
          // The label is the reaction, nothing more — there is no count to read out and
          // no pressed state to report, because a reaction is not a toggle.
          aria-label={r.label}
          title={r.label}
          className={cx(
            "inline-flex h-11 w-11 items-center justify-center rounded-xl border text-xl transition duration-150 active:scale-90 motion-reduce:transition-none motion-reduce:active:scale-100 disabled:cursor-not-allowed disabled:opacity-50",
            flashing
              ? "border-emerald-400 bg-emerald-50 dark:border-emerald-500/50 dark:bg-emerald-500/15"
              : "border-slate-200 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:hover:border-slate-700 dark:hover:bg-slate-800/60"
          )}
        >
          {/* Keyed on the tap counter so React remounts the span and the pop animation
              replays — including when the SAME emoji is tapped twice in a row. */}
          <span key={flashing ? lastTap.n : 0} className="zk-pop leading-none" aria-hidden>{r.emoji}</span>
        </button>
        );
      })}

      {raiseHandVisible && (
        <button
          type="button"
          onClick={onToggleHand}
          disabled={disabled}
          aria-pressed={handRaised}
          aria-label={handRaised ? "Lower your hand" : "Raise your hand"}
          title={handRaised ? "Lower your hand" : "Raise your hand"}
          className={cx(
            "ml-auto inline-flex min-h-11 items-center gap-2 rounded-xl border px-4 py-2 text-sm font-semibold transition duration-150 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100 disabled:cursor-not-allowed disabled:opacity-50",
            handRaised
              ? "border-transparent bg-violet-600 text-white hover:bg-violet-500"
              : "border-violet-300 text-violet-600 hover:bg-violet-50 dark:border-violet-500/40 dark:text-violet-400 dark:hover:bg-violet-500/10"
          )}
        >
          <span aria-hidden>✋</span> Raise Hand
        </button>
      )}
    </div>
  );
}
