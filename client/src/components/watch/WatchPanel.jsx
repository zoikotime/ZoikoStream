// client/src/components/watch/WatchPanel.jsx
// Viewer Portal right column — tabbed Chat / Q&A / Polls. All three are real, over the
// live socket EventWatch opens (see its `liveReducer`) — same backend the host/moderator
// consoles use. An unidentified visitor (no login, no self-serve registration) sees the
// IdentifyForm instead of dead controls — never a "sign in" prompt; a name+email is enough.
import { useEffect, useRef, useState } from "react";
import { FiSend, FiChevronUp, FiCheckCircle } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { initials } from "../../data/watch";
import { hhmm } from "../../data/moderation";
import IdentifyForm from "./IdentifyForm";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "qa", label: "Q&A" },
  { key: "polls", label: "Polls" },
];

// Real chat, wired to the same live socket the host/moderator consoles use. Reaching this
// component at all means the caller (WatchPanel) has already confirmed the visitor is
// identified — logged in or self-registered — so there's no gate to check here.
function Chat({ messages = [], typing = {}, send, connected }) {
  const [text, setText] = useState("");
  const scroller = useRef(null);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const submit = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    send("chat.send", { text: t });
    setText("");
  };

  const typists = Object.values(typing).map((t) => t.name);

  return (
    <div className="flex h-full flex-col">
      <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto pr-1">
        {messages.map((m) => (
          <div key={m.id} className={cx(m.pinned && "rounded-lg bg-emerald-50 p-2 dark:bg-emerald-500/10")}>
            <div className="flex items-center gap-2">
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-200">
                {initials(m.name)}
              </span>
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{m.name}</span>
              {m.pinned && <span className="rounded bg-emerald-600/10 px-1.5 text-[10px] font-semibold uppercase text-emerald-600 dark:text-emerald-400">Pinned</span>}
              <span className="ml-auto text-[11px] text-slate-400">{hhmm(m.created_at)}</span>
            </div>
            <p className="ml-8 text-sm text-slate-600 dark:text-slate-300">{m.text}</p>
          </div>
        ))}
        {messages.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-400">No messages yet — say hello.</p>
        )}
      </div>
      {typists.length > 0 && (
        <p className="h-4 truncate text-[11px] text-slate-400">
          {typists.length === 1 ? `${typists[0]} is typing…` : `${typists.length} people are typing…`}
        </p>
      )}
      <form onSubmit={submit} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Say something…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
        <button
          type="submit"
          disabled={!connected}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-600 text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
          aria-label="Send message"
        >
          <FiSend />
        </button>
      </form>
    </div>
  );
}

// Real Q&A. No per-user vote ledger on the server (see moderation._qa_vote), so the "voted"
// highlight is purely local — it survives this tab session, not a reload, same as the mock
// it replaced.
function QA({ questions = [], send, connected }) {
  const [voted, setVoted] = useState({});
  const [text, setText] = useState("");

  const toggleVote = (id) => {
    const on = !voted[id];
    setVoted((v) => ({ ...v, [id]: on }));
    send("qa.vote", on ? { id } : { id, down: true });
  };

  const ask = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    send("qa.ask", { text: t });
    setText("");
  };

  const sorted = [...questions].sort((a, b) => b.votes - a.votes);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-2 overflow-y-auto pr-1">
        {sorted.map((q) => (
          <div key={q.id} className="flex gap-3 rounded-xl border border-slate-100 p-3 dark:border-slate-800">
            <button
              onClick={() => toggleVote(q.id)}
              className={cx(
                "flex h-12 w-11 shrink-0 flex-col items-center justify-center rounded-lg border text-xs font-semibold transition",
                voted[q.id]
                  ? "border-emerald-500 bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
                  : "border-slate-200 text-slate-500 hover:border-slate-300 dark:border-slate-700 dark:text-slate-400"
              )}
              aria-pressed={!!voted[q.id]}
            >
              <FiChevronUp className="text-base" />
              {q.votes}
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-xs text-slate-400">{q.name}</span>
                {q.status === "answered" && (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    <FiCheckCircle className="text-sm" /> Answered
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
        {sorted.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-400">No questions yet — ask the first one.</p>
        )}
      </div>
      <form onSubmit={ask} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
        <button type="submit" disabled={!connected} className="rounded-xl bg-emerald-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50">
          Ask
        </button>
      </form>
    </div>
  );
}

// Options are index-addressed on the server (moderation._poll_vote takes `option` as an
// array index, not an id — see poll_out), so voting sends the option's position, not a key.
function Poll({ poll, send }) {
  const [choice, setChoice] = useState(null);
  const voted = choice !== null;
  const opts = poll.options || [];
  const total = opts.reduce((s, o) => s + (o.votes || 0), 0);

  const vote = (index) => {
    setChoice(index);
    send("poll.vote", { id: poll.id, option: index });
  };

  return (
    <div className="rounded-xl border border-slate-100 p-4 dark:border-slate-800">
      <p className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-100">{poll.question}</p>
      <div className="space-y-2">
        {opts.map((o, i) => {
          const pct = total ? Math.round((o.votes / total) * 100) : 0;
          if (!voted && poll.status === "live")
            return (
              <button
                key={i}
                onClick={() => vote(i)}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm font-medium text-slate-700 transition hover:border-emerald-400 hover:bg-emerald-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-emerald-500/50 dark:hover:bg-emerald-500/10"
              >
                {o.label}
              </button>
            );
          return (
            <div key={i} className="relative overflow-hidden rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <div className={cx("absolute inset-y-0 left-0", i === choice ? "bg-emerald-500/20" : "bg-slate-100 dark:bg-slate-800")} style={{ width: `${pct}%` }} />
              <div className="relative flex items-center justify-between text-sm">
                <span className={cx("font-medium", i === choice ? "text-emerald-700 dark:text-emerald-300" : "text-slate-700 dark:text-slate-200")}>
                  {i === choice && "✓ "}{o.label}
                </span>
                <span className="tabular-nums text-slate-500 dark:text-slate-400">{pct}%</span>
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {total.toLocaleString()} votes{voted ? " · thanks for voting" : poll.status !== "live" ? " · closed" : ""}
      </p>
    </div>
  );
}

function Polls({ polls = [], send }) {
  const visible = polls.filter((p) => p.status === "live" || p.status === "closed");

  if (!visible.length) {
    return <p className="py-8 text-center text-sm text-slate-400">No polls yet.</p>;
  }

  return (
    <div className="h-full space-y-4 overflow-y-auto">
      {visible.map((p) => (
        <Poll key={p.id} poll={p} send={send} />
      ))}
    </div>
  );
}

const IDENTIFY_LABEL = {
  chat: "Enter your name and email to join the chat.",
  qa: "Enter your name and email to ask a question.",
  polls: "Enter your name and email to vote in polls.",
};

export default function WatchPanel({
  className = "", messages, typing, questions, polls, send, connected,
  identified, eventId, onIdentified,
}) {
  const [tab, setTab] = useState("chat");

  return (
    <div className={cx("flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900", className)}>
      <div className="flex shrink-0 border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              "flex-1 border-b-2 px-3 py-3 text-sm font-medium transition",
              tab === t.key
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-3">
        {!identified ? (
          <IdentifyForm eventId={eventId} label={IDENTIFY_LABEL[tab]} onIdentified={onIdentified} />
        ) : (
          <>
            {tab === "chat" && <Chat messages={messages} typing={typing} send={send} connected={connected} />}
            {tab === "qa" && <QA questions={questions} send={send} connected={connected} />}
            {tab === "polls" && <Polls polls={polls} send={send} />}
          </>
        )}
      </div>
    </div>
  );
}
