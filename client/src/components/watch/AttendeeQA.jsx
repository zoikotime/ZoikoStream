// client/src/components/watch/AttendeeQA.jsx
// Q&A from the audience side: ask, vote, save, search, and read the answer when it comes.
//
// Deliberately not the moderator's QATab (a triage queue: approve, pin, dismiss, merge, assign) nor
// the speaker's SpeakerQA ("questions routed to me"). An attendee's question is different again:
// "did mine get through, and has anyone answered it?"
//
// `qa.ask` and `qa.vote` are existing VIEWER_ACTIONS — nothing new was needed on the server for
// either. Saving a question is REST, because it outlives the session (it lands on the attendee's
// own registration row).
import { useMemo, useState } from "react";
import {
  FiChevronUp, FiSearch, FiSend, FiHelpCircle, FiBookmark, FiCheckCircle, FiClock,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { Input, Select } from "../../ui/forms";
import { hhmm } from "../../data/moderation";
import { QA_FILTERS } from "../../data/attendee";

const MATCHES = {
  popular: () => true,
  pending: (q) => q.status !== "answered" && q.status !== "dismissed",
  answered: (q) => q.status === "answered",
  mine: (q, me) => q.user_id === me,
  saved: (q, _me, saved) => saved.includes(q.id),
};

const SORTERS = {
  popular: (a, b) => b.votes - a.votes || new Date(a.created_at) - new Date(b.created_at),
  pending: (a, b) => b.votes - a.votes,
  answered: (a, b) => new Date(b.answered_at || b.created_at) - new Date(a.answered_at || a.created_at),
  mine: (a, b) => new Date(b.created_at) - new Date(a.created_at),
  saved: (a, b) => b.votes - a.votes,
};

export default function AttendeeQA({
  questions = [], identity, saved = [], enabled = true, send, onSave, className,
}) {
  const [filter, setFilter] = useState("popular");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [voted, setVoted] = useState(() => new Set());

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return questions
      // A `dismissed` question is one a moderator took out of the running order. The AUTHOR still
      // sees theirs — otherwise their question silently vanishes and they ask it again.
      .filter((x) => x.status !== "dismissed" || x.user_id === identity)
      .filter((x) => MATCHES[filter](x, identity, saved))
      .filter((x) => !q || `${x.name} ${x.text}`.toLowerCase().includes(q))
      .sort(SORTERS[filter]);
  }, [questions, filter, query, identity, saved]);

  const submit = (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    send("qa.ask", { text });
    setDraft("");
  };

  const vote = (q) => {
    // One vote per browser per question. The server keeps no per-user vote ledger (documented in
    // services/moderation._poll_vote and the same for questions), so this stops a double-tap — it
    // is not a claim to prevent determined vote-stuffing.
    if (voted.has(q.id)) return;
    setVoted((prev) => new Set(prev).add(q.id));
    send("qa.vote", { id: q.id });
  };

  const mine = questions.filter((q) => q.user_id === identity).length;

  return (
    <div className={cx("flex min-h-0 flex-col", className)}>
      {enabled ? (
        <form onSubmit={submit} className="flex shrink-0 items-center gap-2 border-b border-slate-200 p-3 dark:border-white/10">
          <Input
            variant="console"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask the speakers a question…"
            aria-label="Ask a question"
            maxLength={1000}
          />
          <button
            type="submit"
            disabled={!draft.trim()}
            className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-violet-600 text-white transition hover:bg-violet-500 disabled:opacity-40", focusRing)}
            aria-label="Submit question"
          >
            <FiSend className="text-sm" />
          </button>
        </form>
      ) : (
        <p className="shrink-0 border-b border-slate-200 p-3 text-xs text-slate-500 dark:border-white/10 dark:text-neutral-400">
          Q&amp;A is turned off for this event.
        </p>
      )}

      <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 p-3 dark:border-white/10">
        <div className="relative flex-1">
          <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <Input
            variant="console"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search questions…"
            aria-label="Search questions"
            className="pl-9"
          />
        </div>
        <Select variant="console" className="w-40" value={filter}
                onChange={(e) => setFilter(e.target.value)} aria-label="Filter questions">
          {QA_FILTERS.map((f) => (
            <option key={f.key} value={f.key}>
              {f.label}{f.key === "mine" && mine ? ` (${mine})` : ""}
            </option>
          ))}
        </Select>
      </div>

      <ol className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {shown.map((q) => {
          const isMine = q.user_id === identity;
          const isSaved = saved.includes(q.id);
          const answered = q.status === "answered";
          return (
            <li
              key={q.id}
              className={cx(
                "flex gap-3 rounded-xl border p-3 transition motion-safe:animate-[zk-fade-in_.25s]",
                answered
                  ? "border-emerald-200 bg-emerald-50/50 dark:border-emerald-500/30 dark:bg-emerald-500/5"
                  : "border-slate-200 dark:border-white/10"
              )}
            >
              <div className="flex shrink-0 flex-col items-center gap-0.5">
                <button
                  type="button"
                  onClick={() => vote(q)}
                  disabled={voted.has(q.id)}
                  aria-label={`Upvote: ${q.text.slice(0, 60)}`}
                  aria-pressed={voted.has(q.id)}
                  className={cx(
                    "rounded transition",
                    voted.has(q.id)
                      ? "text-violet-600 dark:text-violet-400"
                      : "text-slate-400 hover:text-violet-600 dark:hover:text-violet-400",
                    focusRing
                  )}
                >
                  <FiChevronUp className="text-lg" />
                </button>
                <span className="text-xs font-semibold tabular-nums text-slate-600 dark:text-neutral-300">
                  {q.votes}
                </span>
              </div>

              <div className="min-w-0 flex-1">
                <p className="break-words text-sm text-slate-800 dark:text-neutral-100">{q.text}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-400">
                    {isMine ? "You" : q.name} · {hhmm(q.created_at)}
                  </span>
                  {answered && (
                    <Badge tone="success" size="sm"><FiCheckCircle aria-hidden="true" /> Answered</Badge>
                  )}
                  {!answered && q.status === "pending" && (
                    <Badge tone="neutral" size="sm"><FiClock aria-hidden="true" /> Awaiting review</Badge>
                  )}
                  {q.assigned_name && <Badge tone="info" size="sm">→ {q.assigned_name}</Badge>}
                </div>

                {/* The speaker's written answer, when there is one. */}
                {q.answer_text && (
                  <p className="mt-2 rounded-lg bg-white px-2.5 py-1.5 text-xs text-slate-700 ring-1 ring-slate-200 dark:bg-white/[0.04] dark:text-neutral-200 dark:ring-white/10">
                    <span className="font-semibold">Answer: </span>{q.answer_text}
                  </p>
                )}
              </div>

              <button
                type="button"
                onClick={() => onSave?.(q.id)}
                aria-label={isSaved ? "Remove from saved" : "Save this question"}
                aria-pressed={isSaved}
                title={isSaved ? "Saved — tap to remove" : "Save to come back to"}
                className={cx(
                  "shrink-0 self-start rounded p-1 transition",
                  isSaved ? "text-violet-600 dark:text-violet-400" : "text-slate-300 hover:text-slate-500 dark:text-neutral-600",
                  focusRing
                )}
              >
                <FiBookmark className="text-sm" />
              </button>
            </li>
          );
        })}

        {shown.length === 0 && (
          <li className="grid place-items-center py-12 text-center">
            <FiHelpCircle className="text-2xl text-slate-300 dark:text-neutral-700" aria-hidden="true" />
            <p className="mt-2 text-sm text-slate-500 dark:text-neutral-400">
              {questions.length === 0
                ? enabled ? "No questions yet — ask the first one." : "No questions yet."
                : "Nothing matches that filter."}
            </p>
          </li>
        )}
      </ol>
    </div>
  );
}
