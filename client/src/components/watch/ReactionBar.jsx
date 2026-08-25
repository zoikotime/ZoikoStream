// client/src/components/watch/ReactionBar.jsx
// Viewer reactions under the player. REAL DATA — counts are the backend's authoritative
// state (server/app/services/bus.py reaction_incr/reaction_all), delivered on the same
// live socket as chat/Q&A/polls (see server/app/routers/live.py, moderation.py
// `reaction.add` / `reaction.update`). A tap sends the action and waits for the
// broadcast to come back before the number changes — the same pattern chat.send and
// poll.vote already use in WatchPanel.jsx, so there is nothing reaction-specific to
// reconcile and no risk of double-counting a tap.
import { useEffect, useState } from "react";
import { cx } from "../../ui/tokens";
import { REACTIONS } from "../../data/reactions";

// How long a tapped emoji stays highlighted. This is purely a "you just did that" flash,
// not a persistent "your reaction" toggle — the backend has no per-viewer reaction ledger
// (every tap is a +1, there's nothing to un-react), so nothing here implies otherwise.
const PULSE_MS = 900;

/**
 * @param {object} props
 * @param {Record<string, number>} props.reactions - authoritative counts keyed by
 *   reaction key (like/heart/clap/fire/party), from the live socket snapshot/update.
 * @param {(key: string) => void} props.onReact - sends `reaction.add` for this key.
 * @param {boolean} [props.disabled] - true while disconnected or while the host has
 *   turned reactions off (settings.reactions_enabled === false).
 * @param {boolean} [props.handRaised] - this viewer's own `hand` presence flag (see
 *   EventWatch.jsx, derived from panel.you/panel.participants).
 * @param {() => void} [props.onToggleHand] - sends `participant.hand` for this viewer.
 * @param {boolean} [props.raiseHandVisible] - gated on watch.raise_hand_enabled.
 */
export default function ReactionBar({
  reactions, onReact, disabled = false, className = "",
  handRaised = false, onToggleHand, raiseHandVisible = false,
}) {
  const [pulsing, setPulsing] = useState({});

  // Clear a pulse automatically so a tap's highlight is always transient, without
  // leaking one setTimeout per click into an ever-growing set of pending timers.
  useEffect(() => {
    const keys = Object.keys(pulsing);
    if (!keys.length) return undefined;
    const timers = keys.map((key) =>
      setTimeout(() => {
        setPulsing((p) => {
          const { [key]: _drop, ...rest } = p;
          return rest;
        });
      }, PULSE_MS)
    );
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulsing]);

  const tap = (key) => {
    if (disabled) return;
    setPulsing((p) => ({ ...p, [key]: true }));
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
        const count = reactions?.[r.key] ?? 0;
        return (
          <button
            key={r.key}
            type="button"
            onClick={() => tap(r.key)}
            disabled={disabled}
            aria-pressed={!!pulsing[r.key]}
            aria-label={`${r.label} (${count})`}
            title={r.label}
            className={cx(
              "inline-flex min-h-11 items-center gap-2 rounded-xl border px-3 py-2 text-sm font-semibold transition duration-150 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100 disabled:cursor-not-allowed disabled:opacity-50",
              pulsing[r.key]
                ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-500/50 dark:bg-emerald-500/15 dark:text-emerald-300"
                : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-300 dark:hover:border-slate-700 dark:hover:bg-slate-800/60"
            )}
          >
            {/* key on the count so every authoritative change restarts the pop animation */}
            <span key={count} className="zk-pop text-base leading-none" aria-hidden>{r.emoji}</span>
            <span className="zk-tnum text-xs">{count}</span>
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
