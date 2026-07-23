// client/src/components/watch/WatchPanel.jsx
// Viewer Portal right column — tabbed Chat / Q&A / Polls. Fully interactive on
// local state (send a message, upvote/ask a question, vote in a poll).
import { useEffect, useRef, useState } from "react";
import { FiSend, FiChevronUp, FiCheckCircle } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { notify } from "../../ui/Toast";
import { useAuth } from "../../auth/AuthContext";
import { connectEventChat, sendChatMessage } from "../../lib/chatSocket";
import { qaSeed, pollsSeed, initials } from "../../data/watch";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "qa", label: "Q&A" },
  { key: "polls", label: "Polls" },
];

// Viewers usually aren't logged in — ask for a display name once and remember it
// for next time, rather than gating chat behind an account.
function useGuestName() {
  const [name, setName] = useState(() => localStorage.getItem("chat_guest_name") || "");
  const save = (n) => {
    localStorage.setItem("chat_guest_name", n);
    setName(n);
  };
  return [name, save];
}

function JoinChatPrompt({ onJoin }) {
  const [value, setValue] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onJoin(value.trim());
      }}
      className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center"
    >
      <p className="text-sm text-slate-500 dark:text-slate-400">Enter your name to join the chat</p>
      <input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Your name"
        className="w-full max-w-[220px] rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-center text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
      />
      <button type="submit" className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500" disabled={!value.trim()}>
        Join Chat
      </button>
    </form>
  );
}

function Chat({ streamId, viewerEmail }) {
  const { user } = useAuth();
  const [guestName, setGuestName] = useGuestName();
  const displayName = user?.full_name || guestName;

  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const socketRef = useRef(null);

  useEffect(() => {
    if (!streamId || !displayName) return;
    const token = localStorage.getItem("token");
    const socket = connectEventChat(
      streamId,
      user ? { token } : { displayName, email: viewerEmail },
      {
        onHistory: setMsgs,
        onNew: (msg) => setMsgs((m) => [...m, msg]),
        onUpdated: (msg) => setMsgs((m) => m.map((x) => (x.id === msg.id ? msg : x))),
        onDeleted: (id) => setMsgs((m) => m.filter((x) => x.id !== id)),
        onError: (err) => notify.error(err),
      }
    );
    socketRef.current = socket;
    return () => socket.disconnect();
  }, [streamId, displayName, user, viewerEmail]);

  const send = async (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || !socketRef.current) return;
    try {
      await sendChatMessage(socketRef.current, t);
      setText("");
    } catch {
      notify.error("Failed to send message");
    }
  };

  if (!displayName) return <JoinChatPrompt onJoin={setGuestName} />;

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto pr-1">
        {msgs.map((m) => (
          <div key={m.id} className={cx(m.pinned && "rounded-lg bg-emerald-50 p-2 dark:bg-emerald-500/10")}>
            <div className="flex items-center gap-2">
              <span
                className={cx(
                  "grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px] font-semibold",
                  m.display_name === displayName ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-200"
                )}
              >
                {initials(m.display_name)}
              </span>
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{m.display_name}</span>
              {m.pinned && <span className="rounded bg-emerald-600/10 px-1.5 text-[10px] font-semibold uppercase text-emerald-600 dark:text-emerald-400">Pinned</span>}
              <span className="ml-auto text-[11px] text-slate-400">
                {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
            <p className="ml-8 text-sm text-slate-600 dark:text-slate-300">{m.text}</p>
          </div>
        ))}
        {msgs.length === 0 && <p className="text-center text-sm text-slate-400">No messages yet — say hello!</p>}
      </div>
      <form onSubmit={send} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Say something…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
        <button type="submit" className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-600 text-white hover:bg-emerald-500" aria-label="Send message">
          <FiSend />
        </button>
      </form>
    </div>
  );
}

function QA() {
  const [items, setItems] = useState(qaSeed);
  const [voted, setVoted] = useState({});
  const [text, setText] = useState("");

  const toggleVote = (id) => {
    const on = !voted[id];
    setVoted((v) => ({ ...v, [id]: on }));
    setItems((list) => list.map((q) => (q.id === id ? { ...q, votes: q.votes + (on ? 1 : -1) } : q)));
  };

  const ask = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    setItems((list) => [...list, { id: `local-${list.length}`, name: "You", text: t, votes: 0, answered: false }]);
    setText("");
  };

  const sorted = [...items].sort((a, b) => b.votes - a.votes);

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
                {q.answered && (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
                    <FiCheckCircle className="text-sm" /> Answered
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={ask} className="mt-3 flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask a question…"
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        />
        <button type="submit" className="rounded-xl bg-emerald-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-emerald-500">
          Ask
        </button>
      </form>
    </div>
  );
}

function Poll({ poll }) {
  const [choice, setChoice] = useState(null);
  const voted = choice !== null;
  const opts = voted
    ? poll.options.map((o) => (o.id === choice ? { ...o, votes: o.votes + 1 } : o))
    : poll.options;
  const total = opts.reduce((s, o) => s + o.votes, 0);

  return (
    <div className="rounded-xl border border-slate-100 p-4 dark:border-slate-800">
      <p className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-100">{poll.question}</p>
      <div className="space-y-2">
        {opts.map((o) => {
          const pct = total ? Math.round((o.votes / total) * 100) : 0;
          if (!voted)
            return (
              <button
                key={o.id}
                onClick={() => setChoice(o.id)}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm font-medium text-slate-700 transition hover:border-emerald-400 hover:bg-emerald-50 dark:border-slate-700 dark:text-slate-200 dark:hover:border-emerald-500/50 dark:hover:bg-emerald-500/10"
              >
                {o.label}
              </button>
            );
          return (
            <div key={o.id} className="relative overflow-hidden rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
              <div className={cx("absolute inset-y-0 left-0", o.id === choice ? "bg-emerald-500/20" : "bg-slate-100 dark:bg-slate-800")} style={{ width: `${pct}%` }} />
              <div className="relative flex items-center justify-between text-sm">
                <span className={cx("font-medium", o.id === choice ? "text-emerald-700 dark:text-emerald-300" : "text-slate-700 dark:text-slate-200")}>
                  {o.id === choice && "✓ "}{o.label}
                </span>
                <span className="tabular-nums text-slate-500 dark:text-slate-400">{pct}%</span>
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-slate-400">{total.toLocaleString()} votes{voted ? " · thanks for voting" : ""}</p>
    </div>
  );
}

function Polls() {
  return (
    <div className="h-full space-y-4 overflow-y-auto">
      {pollsSeed.map((p) => (
        <Poll key={p.id} poll={p} />
      ))}
    </div>
  );
}

export default function WatchPanel({ streamId, viewerEmail, className = "" }) {
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
        {tab === "chat" && <Chat streamId={streamId} viewerEmail={viewerEmail} />}
        {tab === "qa" && <QA />}
        {tab === "polls" && <Polls />}
      </div>
    </div>
  );
}
