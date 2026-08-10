// client/src/components/watch/ReactionBar.jsx
// Viewer reactions under the player. FRONTEND ONLY — the live socket (server/app/routers/
// live.py) speaks chat / qa / poll and nothing else, so there is no reaction event to send
// and no count to read. Your taps are local UI state; the baseline each reaction starts from
// is derived from the event id so it's stable across renders instead of reshuffling.
// ponytail: local state. Replace `bump` with a `send("reaction", …)` the day live.py grows one.
import { useState } from "react";
import { cx } from "../../ui/tokens";

const REACTIONS = [
  { key: "like", emoji: "👍", label: "Like" },
  { key: "heart", emoji: "❤️", label: "Love" },
  { key: "clap", emoji: "👏", label: "Applaud" },
  { key: "fire", emoji: "🔥", label: "Fire" },
  { key: "party", emoji: "🎉", label: "Celebrate" },
];

// Stable per-event baseline, same trick as data/moderation.js accentFor.
const seed = (id = "", i) => {
  let h = 0;
  for (let c = 0; c < id.length; c += 1) h = (h * 31 + id.charCodeAt(c)) % 997;
  return 20 + ((h + i * 137) % 120);
};

export default function ReactionBar({ eventId, className = "" }) {
  const [counts, setCounts] = useState(() =>
    Object.fromEntries(REACTIONS.map((r, i) => [r.key, seed(eventId, i)]))
  );
  const [mine, setMine] = useState({});

  const bump = (key) => {
    setCounts((c) => ({ ...c, [key]: c[key] + (mine[key] ? -1 : 1) }));
    setMine((m) => ({ ...m, [key]: !m[key] }));
  };

  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm dark:border-slate-800 dark:bg-slate-900",
        className
      )}
    >
      {REACTIONS.map((r) => (
        <button
          key={r.key}
          onClick={() => bump(r.key)}
          aria-pressed={!!mine[r.key]}
          aria-label={`${r.label} (${counts[r.key]})`}
          title={r.label}
          className={cx(
            "inline-flex min-h-11 items-center gap-2 rounded-xl border px-3 py-2 text-sm font-semibold transition duration-150 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100",
            mine[r.key]
              ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-500/50 dark:bg-emerald-500/15 dark:text-emerald-300"
              : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-300 dark:hover:border-slate-700 dark:hover:bg-slate-800/60"
          )}
        >
          {/* key on the count so every tap restarts the pop animation */}
          <span key={counts[r.key]} className="zk-pop text-base leading-none" aria-hidden>{r.emoji}</span>
          <span className="zk-tnum text-xs">{counts[r.key]}</span>
        </button>
      ))}
    </div>
  );
}
