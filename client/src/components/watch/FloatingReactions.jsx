// client/src/components/watch/FloatingReactions.jsx
// Floating reaction burst over the video — purely decorative. ReactionBar's counts stay
// the single source of truth for "how many"; this is just "something happened, and
// everyone watching sees it float up," the way Twitch/Instagram Live reactions read as
// alive rather than a number that occasionally ticks up. Bursts are spawned by
// EventWatch.jsx from real socket deltas (reactions/reaction.update), not from this
// viewer's own taps alone, so a reaction from anyone in the audience shows here.
import { useEffect } from "react";

const DURATION_MS = 2200;

export default function FloatingReactions({ bursts, onExpire, className = "" }) {
  useEffect(() => {
    if (!bursts.length) return undefined;
    const timers = bursts.map((b) => setTimeout(() => onExpire(b.id), DURATION_MS));
    return () => timers.forEach(clearTimeout);
  }, [bursts, onExpire]);

  if (!bursts.length) return null;

  return (
    <div className={`pointer-events-none absolute inset-0 z-30 overflow-hidden ${className}`} aria-hidden>
      {bursts.map((b) => (
        <span
          key={b.id}
          className="zk-rise-fade absolute bottom-14 text-2xl drop-shadow-md"
          style={{ left: `${b.left}%`, animationDelay: `${b.delayMs}ms` }}
        >
          {b.emoji}
        </span>
      ))}
    </div>
  );
}
