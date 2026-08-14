// client/src/components/watch/WatchPanel.jsx
// Viewer Portal right column — tabbed Chat / Q&A / Polls. All three are real, over the
// live socket EventWatch opens (see its `liveReducer`) — same backend the host/moderator
// consoles use. An unidentified visitor (no login, no self-serve registration) sees the
// IdentifyForm instead of dead controls — never a "sign in" prompt; a name+email is enough.
//
// Chat/QA/Polls are memoized: EventWatch's liveReducer keeps every live-panel slice
// (messages, typing, questions, polls, participants, reactions) in ONE state object, so a
// reaction tap or a presence update from ANY viewer produces a new top-level object and
// re-renders this whole tree for EVERY connected viewer, even though messages/questions/
// polls didn't change. With a couple dozen concurrent viewers tapping reactions, that
// cascades into the chat feeling laggy purely from unrelated re-renders, not real load.
// React.memo on each tab breaks the cascade at this boundary: since messages/typing/
// questions/polls only get NEW references from the reducer cases that actually touch
// them, a reaction-only or presence-only update leaves those references unchanged and
// these three skip re-rendering entirely.
import { memo, useEffect, useRef, useState } from "react";
import { FiSend, FiChevronUp, FiCheckCircle, FiMessageSquare } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import { initials } from "../../data/watch";
import { hhmm, accentFor } from "../../data/moderation";
import IdentifyForm from "./IdentifyForm";

function EmptyState({ children }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <FiMessageSquare className="text-2xl text-slate-300 dark:text-slate-700" aria-hidden />
      <p className="text-sm text-slate-400">{children}</p>
    </div>
  );
}

// One skin for the two composer inputs, so Chat and Q&A can't drift apart.
const FIELD =
  "min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-700 outline-none transition duration-150 placeholder:text-slate-400 focus:border-emerald-400 focus:bg-white focus:ring-2 focus:ring-emerald-500/20 motion-reduce:transition-none dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-200 dark:focus:bg-slate-900";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "qa", label: "Q&A" },
  { key: "polls", label: "Polls" },
];

// Real chat, wired to the same live socket the host/moderator consoles use. Reaching this
// component at all means the caller (WatchPanel) has already confirmed the visitor is
// identified — logged in or self-registered — so there's no gate to check here.
const Chat = memo(function Chat({ messages = [], typing = {}, send, connected }) {
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
      <div ref={scroller} className="zk-scroll-thin flex-1 space-y-1 overflow-y-auto pr-1">
        {messages.map((m) => (
          <div
            key={m.id}
            className={cx(
              "rounded-xl p-2 transition-colors duration-150 motion-reduce:transition-none",
              m.pinned
                ? "bg-emerald-50 ring-1 ring-emerald-500/20 dark:bg-emerald-500/10"
                : "hover:bg-slate-50 dark:hover:bg-slate-800/50"
            )}
          >
            <div className="flex items-center gap-2">
              <span className={cx("grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px] font-semibold", ACCENT[accentFor(m.name || "")].chip)}>
                {initials(m.name)}
              </span>
              <span className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{m.name}</span>
              {m.pinned && (
                <span className="shrink-0 rounded bg-emerald-600/10 px-1.5 text-[10px] font-semibold uppercase text-emerald-600 dark:text-emerald-400">Pinned</span>
              )}
              <span className="zk-tnum ml-auto shrink-0 text-[11px] text-slate-400">{hhmm(m.created_at)}</span>
            </div>
            <p className="ml-8 break-words text-sm text-slate-600 dark:text-slate-300">{m.text}</p>
          </div>
        ))}
        {messages.length === 0 && <EmptyState>No messages yet — say hello.</EmptyState>}
      </div>
      <p className="h-4 truncate text-[11px] text-slate-400" aria-live="polite">
        {typists.length === 1 ? `${typists[0]} is typing…` : typists.length > 1 ? `${typists.length} people are typing…` : ""}
      </p>
      <form onSubmit={submit} className="mt-2 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Say something…"
          aria-label="Chat message"
          className={FIELD}
        />
        <button
          type="submit"
          disabled={!connected}
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-emerald-600 text-white transition duration-150 hover:bg-emerald-500 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 motion-reduce:transition-none motion-reduce:active:scale-100"
          aria-label="Send message"
          title={connected ? "Send" : "Reconnecting…"}
        >
          <FiSend aria-hidden />
        </button>
      </form>
    </div>
  );
});

// Real Q&A. No per-user vote ledger on the server (see moderation._qa_vote), so the "voted"
// highlight is purely local — it survives this tab session, not a reload, same as the mock
// it replaced.
const QA = memo(function QA({ questions = [], send, connected }) {
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
      <div className="zk-scroll-thin flex-1 space-y-2 overflow-y-auto pr-1">
        {sorted.map((q) => (
          <div
            key={q.id}
            className="flex gap-3 rounded-xl border border-slate-200 p-3 transition-colors duration-150 hover:border-slate-300 motion-reduce:transition-none dark:border-slate-800 dark:hover:border-slate-700"
          >
            <button
              onClick={() => toggleVote(q.id)}
              className={cx(
                "flex h-12 w-11 shrink-0 flex-col items-center justify-center rounded-lg border text-xs font-semibold transition duration-150 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100",
                voted[q.id]
                  ? "border-emerald-500 bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
                  : "border-slate-200 text-slate-500 hover:border-emerald-300 hover:text-emerald-600 dark:border-slate-700 dark:text-slate-400 dark:hover:border-emerald-500/40"
              )}
              aria-pressed={!!voted[q.id]}
              aria-label={`Upvote: ${q.text}`}
            >
              <FiChevronUp className="text-base" aria-hidden />
              <span className="zk-tnum">{q.votes}</span>
            </button>
            <div className="min-w-0 flex-1">
              <p className="break-words text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-400">{q.name}</span>
                {q.status === "answered" && (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    <FiCheckCircle className="text-sm" aria-hidden /> Answered
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
        {sorted.length === 0 && <EmptyState>No questions yet — ask the first one.</EmptyState>}
      </div>
      <form onSubmit={ask} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question…"
          aria-label="Your question"
          className={FIELD}
        />
        <button
          type="submit"
          disabled={!connected}
          className="min-h-11 shrink-0 rounded-xl bg-emerald-600 px-4 text-sm font-semibold text-white transition duration-150 hover:bg-emerald-500 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 motion-reduce:transition-none motion-reduce:active:scale-100"
        >
          Ask
        </button>
      </form>
    </div>
  );
});

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
    <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">{poll.question}</p>
        {poll.status === "live" && (
          <span className="shrink-0 rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-600 dark:text-rose-400">Live</span>
        )}
      </div>
      <div className="space-y-2">
        {opts.map((o, i) => {
          const pct = total ? Math.round((o.votes / total) * 100) : 0;
          if (!voted && poll.status === "live")
            return (
              <button
                key={i}
                onClick={() => vote(i)}
                className="min-h-11 w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm font-medium text-slate-700 transition duration-150 hover:border-emerald-400 hover:bg-emerald-50 active:scale-[0.99] motion-reduce:transition-none motion-reduce:active:scale-100 dark:border-slate-700 dark:text-slate-200 dark:hover:border-emerald-500/50 dark:hover:bg-emerald-500/10"
              >
                {o.label}
              </button>
            );
          return (
            <div key={i} className="relative overflow-hidden rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <div
                className={cx(
                  "absolute inset-y-0 left-0 transition-[width] duration-500 ease-out motion-reduce:transition-none",
                  i === choice ? "bg-emerald-500/20" : "bg-slate-100 dark:bg-slate-800"
                )}
                style={{ width: `${pct}%` }}
              />
              <div className="relative flex items-center justify-between text-sm">
                <span className={cx("font-medium", i === choice ? "text-emerald-700 dark:text-emerald-300" : "text-slate-700 dark:text-slate-200")}>
                  {i === choice && "✓ "}{o.label}
                </span>
                <span className="zk-tnum text-slate-500 dark:text-slate-400">{pct}%</span>
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

const Polls = memo(function Polls({ polls = [], send }) {
  const visible = polls.filter((p) => p.status === "live" || p.status === "closed");

  if (!visible.length) return <EmptyState>No polls yet.</EmptyState>;

  return (
    <div className="zk-scroll-thin h-full space-y-4 overflow-y-auto pr-1">
      {visible.map((p) => (
        <Poll key={p.id} poll={p} send={send} />
      ))}
    </div>
  );
});

const IDENTIFY_LABEL = {
  chat: "Enter your name and email to join the chat.",
  qa: "Enter your name and email to ask a question.",
  polls: "Enter your name and email to vote in polls.",
};

// Memoized too: without it, EventWatch re-rendering for an unrelated panel change (a
// reaction tap, a 15s ping/pong) still re-runs this wrapper's own badge/tab-bar JSX even
// though Chat/QA/Polls below correctly bail out — cheap on its own, but free to skip.
const WatchPanel = memo(function WatchPanel({
  className = "", messages, typing, questions, polls, send, connected,
  identified, eventId, onIdentified,
}) {
  const [tab, setTab] = useState("chat");

  // Real counts, straight off the socket state — so a viewer sitting on Chat can still see
  // that questions or polls are waiting.
  const badges = {
    qa: questions?.length || 0,
    polls: (polls || []).filter((p) => p.status === "live").length,
  };

  return (
    <div
      className={cx(
        "flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900",
        className
      )}
    >
      <div className="flex shrink-0 items-center border-b border-slate-200 dark:border-slate-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? "true" : undefined}
            className={cx(
              "relative flex flex-1 items-center justify-center gap-1.5 border-b-2 px-3 py-3.5 text-sm font-semibold transition duration-150 motion-reduce:transition-none",
              tab === t.key
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:bg-slate-50 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800/50 dark:hover:text-slate-100"
            )}
          >
            {t.label}
            {badges[t.key] > 0 && (
              <span className="zk-tnum rounded-full bg-slate-100 px-1.5 text-[10px] font-bold text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                {badges[t.key]}
              </span>
            )}
          </button>
        ))}
        <span
          className={cx(
            "mr-3 h-2 w-2 shrink-0 rounded-full",
            connected ? "bg-green-500" : "bg-slate-300 dark:bg-slate-700"
          )}
          title={connected ? "Connected" : "Reconnecting…"}
          aria-label={connected ? "Connected" : "Reconnecting"}
          role="status"
        />
      </div>
      <div key={tab} className="zk-fade-in flex min-h-0 flex-1 flex-col p-3">
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
});

export default WatchPanel;
