// client/src/components/moderation/PollVote.jsx
// Poll PARTICIPATION: see what is running, vote, watch results land.
//
// Lives in moderation/* (the shared live-component library) because two consoles render it: the
// speaker console and the attendee watch page. Neither may create, launch, edit or close a poll,
// so this is the whole surface for both — offering the management controls would mean four
// buttons the server refuses.
//
// Not the moderator's PollManagement panel — a speaker cannot create, launch, edit or close a
// poll, and offering those controls would mean four buttons the server refuses. `poll.vote` is a
// VIEWER action, so voting needs nothing new.
//
// One local set remembers which polls this browser has voted in. The server has no per-user vote
// ledger (documented in services/moderation._poll_vote), so this is honest about what it is: it
// stops a double-click, not vote-stuffing.
import { useState } from "react";
import { FiBarChart2, FiCheck } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel from "./Panel";
import { hhmm, POLL_STATUS_TONE } from "../../data/moderation";

function Results({ options }) {
  const total = options.reduce((s, o) => s + (o.votes || 0), 0);
  return (
    <div className="mt-2 space-y-1.5">
      {options.map((o, i) => {
        const pct = total ? Math.round(((o.votes || 0) / total) * 100) : 0;
        return (
          <div key={i} className="relative overflow-hidden rounded-lg border border-slate-200 px-2.5 py-1.5 dark:border-slate-700">
            <div className="absolute inset-y-0 left-0 bg-emerald-500/15 transition-[width] duration-500 motion-reduce:transition-none"
                 style={{ width: `${pct}%` }} />
            <div className="relative flex items-center justify-between text-xs">
              <span className="font-medium text-slate-700 dark:text-slate-200">{o.label}</span>
              <span className="tabular-nums text-slate-500 dark:text-slate-400">
                {(o.votes || 0).toLocaleString()} · {pct}%
              </span>
            </div>
          </div>
        );
      })}
      <p className="text-[11px] text-slate-400">{total.toLocaleString()} votes</p>
    </div>
  );
}

export default function PollVote({ polls = [], send, disabled = false, className }) {
  const [voted, setVoted] = useState(() => new Set());

  const vote = (poll, index) => {
    if (disabled || voted.has(poll.id)) return;
    send("poll.vote", { id: poll.id, option: index });
    setVoted((prev) => new Set(prev).add(poll.id));
  };

  // Live first, then the most recent — a speaker cares about what's on screen right now.
  const shown = [...polls].sort(
    (a, b) => (b.status === "live") - (a.status === "live")
      || new Date(b.created_at) - new Date(a.created_at)
  );
  const live = polls.filter((p) => p.status === "live").length;

  return (
    <Panel
      title="Polls"
      count={polls.length}
      badge={live > 0 && <Badge tone="success" size="sm" dot>{live} live</Badge>}
      className={className}
    >
      <div className="space-y-3">
        {shown.map((p) => {
          const done = voted.has(p.id);
          const open = p.status === "live";
          return (
            <div key={p.id} className="rounded-xl border border-slate-100 p-3 dark:border-slate-800">
              <div className="flex items-start justify-between gap-2">
                <p className="min-w-0 break-words text-sm font-medium text-slate-800 dark:text-slate-100">
                  {p.question}
                </p>
                <Badge status={POLL_STATUS_TONE[p.status]} live={open}>{p.status}</Badge>
              </div>

              {open && !done && !disabled ? (
                <div className="mt-2 space-y-1.5">
                  {(p.options || []).map((o, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => vote(p, i)}
                      className={cx(
                        "flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs font-medium transition",
                        "border-slate-200 text-slate-700 hover:border-emerald-400 hover:bg-emerald-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-emerald-500/40 dark:hover:bg-emerald-500/10"
                      )}
                    >
                      {o.label}
                    </button>
                  ))}
                  <p className="text-[11px] text-slate-400">Results appear once you've voted.</p>
                </div>
              ) : (
                <>
                  {done && (
                    <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                      <FiCheck aria-hidden="true" /> You voted
                    </p>
                  )}
                  <Results options={p.options || []} />
                </>
              )}

              {p.status === "closed" && p.closed_at && (
                <p className="mt-1 text-[11px] text-slate-400">Closed at {hhmm(p.closed_at)}</p>
              )}
            </div>
          );
        })}

        {polls.length === 0 && (
          <EmptyState
            icon={FiBarChart2}
            title="No polls yet"
            description="A host or moderator launches polls. They appear here the moment they go live."
            className="py-10"
          />
        )}
      </div>
    </Panel>
  );
}
