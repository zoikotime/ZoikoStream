// client/src/components/watch/WatchPanel.jsx
// Viewer Portal right column — tabbed Chat / Q&A / Polls. All three are real, over the
// live socket EventWatch opens (see its `liveReducer`) — same backend the host
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
import { FiSend, FiChevronUp, FiCheckCircle, FiMessageSquare, FiMapPin, FiX, FiInfo, FiSmile } from "react-icons/fi";
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

// A fixed, common-emoji picker — not a full emoji-mart/library dependency, just a small
// popover that inserts a glyph into the composer. Matches the emoji vocabulary the
// reaction bar already uses elsewhere on this same page.
const QUICK_EMOJI = ["👍", "❤️", "😂", "🎉", "🔥", "👏", "😮", "🙌"];

function EmojiPicker({ onPick }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Insert an emoji"
        aria-expanded={open}
        className="grid h-11 w-11 shrink-0 place-items-center rounded-xl text-slate-400 transition duration-150 hover:bg-slate-100 hover:text-slate-600 motion-reduce:transition-none dark:hover:bg-slate-800 dark:hover:text-slate-200"
      >
        <FiSmile aria-hidden />
      </button>
      {open && (
        <div className="absolute bottom-12 right-0 z-20 grid w-40 grid-cols-4 gap-1 rounded-xl border border-slate-200 bg-white p-2 shadow-lg dark:border-slate-800 dark:bg-slate-900">
          {QUICK_EMOJI.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => { onPick(e); setOpen(false); }}
              className="grid h-9 w-9 place-items-center rounded-lg text-lg hover:bg-slate-100 dark:hover:bg-slate-800"
            >
              {e}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// "Pinned by host" banner above the message list — additive to, not a replacement for,
// the inline highlighted row already rendered for a pinned message below. Purely a local
// dismiss: there's no viewer-facing "unpin" action (chat.pin is host-only), so closing
// this just hides it for this session. Comparing against the pinned message's OWN id
// (rather than a plain dismissed=true flag) means a new pin from the host — a different
// id — reappears even if a previous pin was dismissed.
function PinnedBanner({ pinned, dismissedId, onDismiss }) {
  if (!pinned || pinned.id === dismissedId) return null;
  return (
    <div className="mb-2 flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 p-3 dark:border-violet-500/20 dark:bg-violet-500/10">
      <FiMapPin className="mt-0.5 shrink-0 text-violet-600 dark:text-violet-400" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-violet-600 dark:text-violet-400">Pinned by host</p>
        <p className="mt-0.5 break-words text-sm text-slate-700 dark:text-slate-200">{pinned.text}</p>
      </div>
      <button
        type="button"
        onClick={() => onDismiss(pinned.id)}
        aria-label="Dismiss pinned message"
        className="shrink-0 rounded-lg p-1 text-violet-400 transition duration-150 hover:bg-violet-100 hover:text-violet-600 motion-reduce:transition-none dark:hover:bg-violet-500/15"
      >
        <FiX aria-hidden />
      </button>
    </div>
  );
}

// Read-only — slow mode is a host moderation setting (server/app/services/broadcast.py
// DEFAULT_SETTINGS.slow_mode_seconds), not something a random viewer's own socket can
// flip. This mirrors the reference design's toggle shape without pretending it's
// interactive: no onClick, disabled-look track, a tooltip explaining what it means.
function SlowModeIndicator({ seconds }) {
  if (!seconds) return null;
  return (
    <div className="mt-2 flex items-center justify-end gap-2 text-xs text-slate-500 dark:text-slate-400">
      <span>Slow mode</span>
      <FiInfo
        aria-hidden
        title={`Messages are limited to one every ${seconds}s — set by the host`}
      />
      <span
        role="status"
        aria-label={`Slow mode is on, set by the host (${seconds}s)`}
        title={`Set by the host — one message every ${seconds}s`}
        className="inline-flex h-5 w-9 shrink-0 items-center rounded-full bg-violet-600 p-0.5"
      >
        <span className="h-4 w-4 translate-x-4 rounded-full bg-white shadow-sm" />
      </span>
    </div>
  );
}

// Real chat, wired to the same live socket the host console uses. Reaching this
// component at all means the caller (WatchPanel) has already confirmed the visitor is
// identified — logged in or self-registered — so there's no gate to check here.
const Chat = memo(function Chat({ messages = [], typing = {}, send, connected, slowModeSeconds = null }) {
  const [text, setText] = useState("");
  const [dismissedPinId, setDismissedPinId] = useState(null);
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
  const pinned = messages.find((m) => m.pinned) || null;

  return (
    <div className="flex h-full flex-col">
      <PinnedBanner pinned={pinned} dismissedId={dismissedPinId} onDismiss={setDismissedPinId} />
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
              {m.actor_role === "host" && (
                <span className="shrink-0 rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-violet-600 dark:bg-violet-500/15 dark:text-violet-400">Host</span>
              )}
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
        <EmojiPicker onPick={(e) => setText((t) => t + e)} />
        <button
          type="submit"
          disabled={!connected}
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-violet-600 text-white transition duration-150 hover:bg-violet-500 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 motion-reduce:transition-none motion-reduce:active:scale-100"
          aria-label="Send message"
          title={connected ? "Send" : "Reconnecting…"}
        >
          <FiSend aria-hidden />
        </button>
      </form>
      <SlowModeIndicator seconds={slowModeSeconds} />
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
//
// `poll.your_vote` is the ONE record of this ballot, and this component holds no copy of it.
// The per-socket snapshot fills it (server/app/services/moderation.py poll_out, from the
// per-viewer vote lookup), which is what makes a refreshed or reconnected page open already
// showing "you voted for X" instead of the vote buttons; and a vote cast in this session is
// written into the same field immediately by EventWatch's `local/poll.vote` action, so the UI
// still updates without waiting on a round trip.
//
// It used to be mirrored into local `choice` state, which then needed an effect to reconcile
// itself against the prop on every reconnect. Two owners for one fact also meant a viewer who
// had reconnected (so your_vote was set) and then changed their vote saw the buttons snap back
// to the old option until the server caught up. One field, one owner, no effect.
//
// A vote can still be CHANGED while the poll is live — the ledger row moves to the new
// option server-side (see moderation._poll_vote) instead of being rejected as a second
// vote, so re-picking here is just another `poll.vote` send.
function Poll({ poll, send }) {
  const choice = poll.your_vote ?? null;
  const voted = choice !== null;
  const canChange = poll.status === "live";
  const opts = poll.options || [];
  const total = opts.reduce((s, o) => s + (o.votes || 0), 0);

  const vote = (index) => {
    if (index === choice) return; // already this option — nothing to send
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
          // Already voted: still clickable while the poll is live, so a viewer can
          // change their mind — locked to a static bar once the poll closes.
          const Row = canChange ? "button" : "div";
          return (
            <Row
              key={i}
              type={canChange ? "button" : undefined}
              onClick={canChange ? () => vote(i) : undefined}
              className={cx(
                "relative w-full overflow-hidden rounded-lg border border-slate-200 px-3 py-2 text-left dark:border-slate-700",
                canChange && "transition duration-150 hover:border-emerald-400 active:scale-[0.99] motion-reduce:transition-none motion-reduce:active:scale-100 dark:hover:border-emerald-500/50"
              )}
            >
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
            </Row>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-slate-400">
        {total.toLocaleString()} votes
        {voted && canChange ? " · thanks for voting — tap another option to change it" : voted ? " · thanks for voting" : poll.status !== "live" ? " · closed" : ""}
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
  identified, eventId, onIdentified, alerts = {}, onTabView,
  // Per-event enablement (GET /events/{id}/watch — chat_enabled/qa_enabled/polls_enabled).
  // A memorial-category event has all three False (crud.event.is_memorial_category,
  // doc Sec. 11.3/19, non-waivable LE-AC-16) — EventWatch.jsx doesn't render this
  // component at all in that case, but the individual flags are still honored here so a
  // non-memorial event that only disabled e.g. polls shows just Chat/Q&A, not a dead tab.
  enabledTabs = { chat: true, qa: true, polls: true },
  // Current slow_mode_seconds off the moderator/snapshot envelope — display-only, see
  // SlowModeIndicator above.
  slowModeSeconds = null,
}) {
  const visibleTabs = TABS.filter((t) => enabledTabs[t.key]);
  const [tab, setTab] = useState(visibleTabs[0]?.key || "chat");

  // Real counts, straight off the socket state — so a viewer sitting on Chat can still see
  // that questions or polls are waiting.
  const badges = {
    qa: questions?.length || 0,
    polls: (polls || []).filter((p) => p.status === "live").length,
  };

  const openTab = (key) => {
    setTab(key);
    onTabView?.(key); // clears that tab's "new activity" alert dot — see EventWatch.jsx
  };

  // enabledTabs can only narrow between renders (the /watch fetch that supplies it is
  // static per event, not a live toggle) — this keeps `tab` from pointing at a now-hidden
  // tab. Diffed during render rather than an effect, same pattern this codebase already
  // uses for prop-driven resets (e.g. Credentials.jsx's CreateKeyModal `wasOpen` check).
  if (visibleTabs.length && !visibleTabs.some((t) => t.key === tab)) {
    setTab(visibleTabs[0].key);
  }

  if (visibleTabs.length === 0) return null;

  return (
    <div
      className={cx(
        "flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900",
        className
      )}
    >
      <div className="flex shrink-0 items-center border-b border-slate-200 dark:border-slate-800">
        {visibleTabs.map((t) => (
          <button
            key={t.key}
            onClick={() => openTab(t.key)}
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
            {/* New-activity alert symbol — a viewer-facing counterpart to the host
                console's pending-count badge. Lights up the moment a host action lands
                for a tab that isn't the active one (see EventWatch.jsx's `alerts`/
                `markAlert`) and disappears the instant the viewer opens that tab, so it
                only ever means "something happened since you last looked". Pulses for
                the same reason the host console's own badge does: a static dot on a busy
                page gets missed. */}
            {alerts[t.key] && tab !== t.key && (
              <span
                className="absolute right-2 top-2 h-2 w-2 animate-pulse rounded-full bg-rose-500 shadow-sm shadow-rose-500/50 motion-reduce:animate-none"
                aria-label={`New activity in ${t.label}`}
                role="status"
              />
            )}
          </button>
        ))}
        <span
          className={cx(
            "mr-3 h-2 w-2 shrink-0 rounded-full",
            connected ? "bg-green-500" : "bg-slate-300 dark:bg-slate-700"
          )}
          // Explicitly "chat" here: this is the chat/Q&A/poll socket (hooks/useEventStream),
          // a separate connection from the video/audio player above — it reflects only
          // whether messages can send/receive right now, never whether the stream itself is
          // live (see VideoPlayer.jsx's own hasVideo/hasAudio/connected for that).
          title={connected ? "Chat connected" : "Chat reconnecting…"}
          aria-label={connected ? "Chat connected" : "Chat reconnecting"}
          role="status"
        />
      </div>
      <div key={tab} className="zk-fade-in flex min-h-0 flex-1 flex-col p-3">
        {!identified ? (
          <IdentifyForm eventId={eventId} label={IDENTIFY_LABEL[tab]} onIdentified={onIdentified} />
        ) : (
          <>
            {tab === "chat" && (
              <Chat messages={messages} typing={typing} send={send} connected={connected} slowModeSeconds={slowModeSeconds} />
            )}
            {tab === "qa" && <QA questions={questions} send={send} connected={connected} />}
            {tab === "polls" && <Polls polls={polls} send={send} />}
          </>
        )}
      </div>
    </div>
  );
});

export default WatchPanel;
