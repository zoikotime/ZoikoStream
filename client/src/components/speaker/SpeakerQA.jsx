// client/src/components/speaker/SpeakerQA.jsx
// The speaker's own Q&A worklist: the questions a moderator routed to THEM.
//
// Deliberately not the moderator's QATab. That panel is a triage queue — approve, pin, dismiss,
// merge, assign — and a speaker has none of those powers. This one answers a different question:
// "what do I still have to answer, and what did I say?"
//
// Authorization is per-ROW server-side (services/moderation._qa_respond): can_speak gets you into
// the handler, but the question has to be assigned to you. So this panel showing only your own
// questions is a convenience, not the security boundary.
import { useMemo, useState } from "react";
import {
  FiCheckCircle, FiFlag, FiSearch, FiHelpCircle, FiSend, FiChevronDown, FiChevronUp,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { Input, Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "../moderation/Panel";
import { hhmm, QA_STATUS_TONE } from "../../data/moderation";

const FILTERS = [
  { key: "open", label: "To answer" },
  { key: "answered", label: "Answered" },
  { key: "all", label: "All" },
];

const MATCHES = {
  open: (q) => q.status !== "answered" && q.status !== "dismissed",
  answered: (q) => q.status === "answered",
  all: () => true,
};

function QuestionCard({ q, send }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(q.answer_text || "");
  const answered = q.status === "answered";

  const submit = (e, completed) => {
    e.preventDefault();
    const answer = draft.trim();
    // An empty answer with completed=true is still meaningful — "I answered this out loud".
    send("qa.respond", { id: q.id, answer, completed });
    setOpen(false);
  };

  return (
    <div
      className={cx(
        "rounded-xl border p-3 transition motion-safe:animate-[zk-fade-in_.25s]",
        answered
          ? "border-slate-100 dark:border-slate-800"
          : "border-emerald-200 bg-emerald-50/40 dark:border-emerald-500/30 dark:bg-emerald-500/5"
      )}
    >
      <p className="break-words text-sm text-slate-800 dark:text-slate-100">{q.text}</p>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <span className="text-xs text-slate-400">{q.name} · {hhmm(q.created_at)}</span>
        <Badge tone={QA_STATUS_TONE[q.status]} size="sm">{q.status}</Badge>
        {q.votes > 0 && <Badge tone="neutral" size="sm">{q.votes} vote{q.votes === 1 ? "" : "s"}</Badge>}
        {(q.flags || []).includes("escalated") && (
          <Badge tone="warning" size="sm">Sent back to a moderator</Badge>
        )}
      </div>

      {q.answer_text && (
        <p className="mt-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          <span className="font-semibold">Your answer: </span>{q.answer_text}
        </p>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-1">
        <ActionButton
          icon={open ? FiChevronUp : FiChevronDown}
          label={q.answer_text ? "Edit answer" : "Write an answer"}
          tone="blue"
          onClick={() => setOpen((v) => !v)}
        />
        {!answered && (
          <ActionButton
            icon={FiCheckCircle}
            label="Answered live"
            tone="emerald"
            title="Mark this answered without typing anything — you answered it out loud"
            onClick={() => send("qa.respond", { id: q.id, answer: draft.trim(), completed: true })}
          />
        )}
        <ActionButton
          icon={FiFlag}
          label="Back to moderator"
          tone="rose"
          title="Hand this question back — you can't or shouldn't answer it"
          onClick={() => send("qa.escalate", { id: q.id })}
        />
      </div>

      {open && (
        <form onSubmit={(e) => submit(e, true)} className="mt-2 space-y-1.5">
          <Input
            variant="console"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type your answer…"
            aria-label={`Answer to ${q.name}'s question`}
            autoFocus
          />
          <div className="flex items-center gap-1.5">
            <button type="submit" className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white transition hover:bg-emerald-500">
              <FiSend aria-hidden="true" /> Save &amp; mark answered
            </button>
            <button
              type="button"
              onClick={(e) => submit(e, false)}
              className="rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              Save without closing
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function SpeakerQA({ questions = [], send, className }) {
  const [filter, setFilter] = useState("open");
  const [query, setQuery] = useState("");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return questions
      .filter(MATCHES[filter])
      .filter((x) => !q || `${x.name} ${x.text}`.toLowerCase().includes(q))
      // Most-voted first inside the open list: that is the running order a moderator hands over.
      .sort((a, b) => b.votes - a.votes || new Date(a.created_at) - new Date(b.created_at));
  }, [questions, filter, query]);

  const open = questions.filter(MATCHES.open).length;

  return (
    <Panel
      title="Your questions"
      count={questions.length}
      badge={open > 0 && <Badge tone="warning" size="sm" dot>{open} to answer</Badge>}
      className={className}
      toolbar={
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <Input
              variant="console"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your questions…"
              aria-label="Search your questions"
              className="pl-9"
            />
          </div>
          <Select variant="console" className="w-32" value={filter}
                  onChange={(e) => setFilter(e.target.value)} aria-label="Filter questions">
            {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </Select>
        </div>
      }
    >
      <div className="space-y-2">
        {shown.map((q) => <QuestionCard key={q.id} q={q} send={send} />)}
        {shown.length === 0 && (
          <EmptyState
            icon={FiHelpCircle}
            title={questions.length === 0 ? "Nothing routed to you yet" : "Nothing here"}
            description={
              questions.length === 0
                ? "A moderator assigns audience questions to a speaker. Yours will appear here as they arrive."
                : "Try a different filter or search."
            }
            className="py-10"
          />
        )}
      </div>
    </Panel>
  );
}
