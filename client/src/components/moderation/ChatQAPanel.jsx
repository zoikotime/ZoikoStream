// client/src/components/moderation/ChatQAPanel.jsx
// Center panel — tabbed Chat Moderation and Q&A. Both are live: messages, questions,
// votes, reactions and typing all arrive over the socket, and every action goes back
// out on it. Kept as ONE component with two tab bodies (the shell it already had)
// rather than split into parallel panels.
import { useEffect, useMemo, useRef, useState } from "react";
import {
  FiCheck, FiTrash2, FiBookmark, FiCheckCircle, FiChevronUp, FiChevronDown,
  FiCornerUpLeft, FiMicOff, FiClock, FiEdit3, FiX, FiArrowDown,
  FiBarChart2, FiSend, FiMessageSquare, FiHelpCircle, FiSlash, FiDownload,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import { Input, Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import { ActionButton } from "./Panel";
import { PANEL, PANEL_PRIMARY } from "./panelTokens";
import SearchField from "./SearchField";
import { downloadCsv } from "../../utils/export";
import {
  initials, hhmm, FLAG_LABELS, CHAT_FILTERS, QUICK_REACTIONS,
  QA_FILTERS, QA_STATUS_TONE,
} from "../../data/moderation";

// Treat "within 40px of the bottom" as pinned to the bottom — an exact check makes
// auto-scroll flicker off on sub-pixel scroll positions.
const BOTTOM_SLACK = 40;
const TYPING_IDLE_MS = 2500;

const CHAT_MATCHES = {
  all: () => true,
  pending: (m) => m.status === "pending",
  flagged: (m) => (m.flags || []).length > 0,
  pinned: (m) => m.pinned,
};

function FlagChips({ flags }) {
  if (!flags?.length) return null;
  return flags.map((f) => (
    <Badge key={f} tone={f === "profanity" ? "danger" : f === "spam" ? "warning" : "neutral"} size="sm">
      {FLAG_LABELS[f] || f}
    </Badge>
  ));
}

// ── chat ─────────────────────────────────────────────────────────────────────

export function ChatTab({ messages, typing, canModerate, send }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [picked, setPicked] = useState(() => new Set());   // bulk selection
  const [replyTo, setReplyTo] = useState(null);
  const [noteFor, setNoteFor] = useState(null);
  const [draft, setDraft] = useState("");
  const [showStats, setShowStats] = useState(false);
  const [unread, setUnread] = useState(0);

  const scroller = useRef(null);
  const atBottom = useRef(true);
  const typingTimer = useRef(null);
  const searchBox = useRef(null);
  const composer = useRef(null);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return messages
      .filter(CHAT_MATCHES[filter])
      .filter((m) => !q || `${m.name} ${m.text}`.toLowerCase().includes(q));
  }, [messages, query, filter]);

  const pinned = messages.find((m) => m.pinned);
  const pendingCount = messages.filter((m) => m.status === "pending").length;

  // Auto-scroll only when the moderator is already at the bottom; otherwise count
  // unreads, so reading back through history isn't yanked away by a new message.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (atBottom.current) el.scrollTop = el.scrollHeight;
    else setUnread((n) => n + 1);
  }, [messages.length]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_SLACK;
    if (atBottom.current) setUnread(0);
  };

  const jumpToLatest = () => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
    atBottom.current = true;
    setUnread(0);
  };

  // Keyboard shortcuts. Deliberately few: the ones a moderator hits constantly during a
  // live event. Every handler bails when focus is already in a field, so typing a message
  // never triggers a command.
  useEffect(() => {
    const onKey = (e) => {
      const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
      if (e.key === "Escape" && inField) {
        e.target.blur();
        return;
      }
      if (inField || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "/") {
        e.preventDefault();          // otherwise "/" lands in the box we just focused
        searchBox.current?.focus();
      } else if (e.key === "j") {
        jumpToLatest();
      } else if (e.key === "m") {
        e.preventDefault();
        composer.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const toggle = (id) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const bulk = (op) => {
    send("chat.bulk", { ids: [...picked], op });
    setPicked(new Set());
  };

  // Typing is throttled to one "started" per idle period, then auto-cleared — otherwise
  // it's one socket frame per keystroke.
  const onDraft = (value) => {
    setDraft(value);
    if (!typingTimer.current) send("chat.typing", { typing: true });
    clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => {
      send("chat.typing", { typing: false });
      typingTimer.current = null;
    }, TYPING_IDLE_MS);
  };

  const submit = (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    send("chat.send", { text, reply_to: replyTo?.id });
    setDraft("");
    setReplyTo(null);
    clearTimeout(typingTimer.current);
    typingTimer.current = null;
    send("chat.typing", { typing: false });
    jumpToLatest();
  };

  const saveNote = (e) => {
    e.preventDefault();
    send("chat.note", { id: noteFor.id, note: new FormData(e.target).get("note") });
    setNoteFor(null);
  };

  const stats = useMemo(() => {
    const flagged = messages.filter((m) => (m.flags || []).length).length;
    const authors = new Map();
    messages.forEach((m) => authors.set(m.name, (authors.get(m.name) || 0) + 1));
    const top = [...authors.entries()].sort((a, b) => b[1] - a[1])[0];
    const stamps = messages.map((m) => m.created_at && new Date(m.created_at).getTime()).filter(Boolean);
    const spanMin = stamps.length > 1 ? (Math.max(...stamps) - Math.min(...stamps)) / 60000 : 0;
    return {
      total: messages.length,
      flagged,
      authors: authors.size,
      top: top ? `${top[0]} (${top[1]})` : "—",
      perMin: spanMin > 0.5 ? (messages.length / spanMin).toFixed(1) : "—",
    };
  }, [messages]);

  const typists = Object.values(typing).map((t) => t.name);

  return (
    <>
      <div className="shrink-0 space-y-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <SearchField
            ref={searchBox}
            className="flex-1"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chat…  (/)"
            label="Search chat"
            title="Press / to search, j to jump to latest, m to write a message"
          />
          <Select variant="console" className="h-8 w-28 py-0 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter chat">
            {CHAT_FILTERS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}{f.key === "pending" && pendingCount ? ` (${pendingCount})` : ""}
              </option>
            ))}
          </Select>
          <ActionButton
            icon={FiBarChart2}
            title="Chat analytics"
            active={showStats}
            onClick={() => setShowStats((v) => !v)}
          />
          <ActionButton
            icon={FiDownload}
            title="Export chat log as CSV"
            onClick={() => downloadCsv("chat-log.csv", messages, [
              ["Time", (m) => hhmm(m.created_at)], ["Author", (m) => m.name],
              ["Message", (m) => m.text], ["Status", (m) => m.status],
              ["Flags", (m) => (m.flags || []).join(" ")], ["Note", (m) => m.note || ""],
            ])}
          />
        </div>

        {showStats && (
          <dl className="grid grid-cols-2 gap-2 rounded-xl bg-slate-50 p-2.5 text-xs sm:grid-cols-5 dark:bg-slate-800/60">
            {[["Messages", stats.total], ["Per minute", stats.perMin], ["Flagged", stats.flagged],
              ["Posters", stats.authors], ["Most active", stats.top]].map(([label, value]) => (
              <div key={label} className="min-w-0">
                <dt className="truncate text-slate-500 dark:text-slate-400">{label}</dt>
                <dd className="truncate font-semibold tabular-nums text-slate-800 dark:text-slate-100">{value}</dd>
              </div>
            ))}
          </dl>
        )}

        {pinned && (
          <div className="flex items-start gap-2 rounded-lg border border-violet-200 bg-violet-50 px-2.5 py-2 dark:border-violet-500/30 dark:bg-violet-500/10">
            <FiBookmark className="mt-0.5 shrink-0 text-violet-600 dark:text-violet-400" aria-hidden="true" />
            <p className="min-w-0 flex-1 break-words text-xs text-slate-700 dark:text-slate-200">
              <span className="font-semibold">{pinned.name}: </span>{pinned.text}
            </p>
            {canModerate && <ActionButton icon={FiX} title="Unpin" onClick={() => send("chat.pin", { id: pinned.id })} />}
          </div>
        )}

        {canModerate && picked.size > 0 && (
          <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-800">
            <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{picked.size} selected</span>
            <div className="ml-auto flex items-center gap-1">
              <ActionButton icon={FiCheck} label="Approve" tone="emerald" onClick={() => bulk("approve")} />
              <ActionButton icon={FiTrash2} label="Delete" tone="rose" onClick={() => bulk("delete")} />
              <ActionButton icon={FiX} title="Clear selection" onClick={() => setPicked(new Set())} />
            </div>
          </div>
        )}
      </div>

      <div ref={scroller} onScroll={onScroll} className="relative flex-1 space-y-2 overflow-y-auto p-3">
        {shown.map((m) => (
          <div
            key={m.id}
            className={cx(
              "rounded-lg border p-2.5 motion-safe:animate-[zk-fade-in_.25s]",
              PANEL.t150,
              picked.has(m.id)
                ? "border-violet-300 bg-violet-50 dark:border-violet-500/40 dark:bg-violet-500/10"
                : m.pinned
                  ? "border-violet-200 bg-violet-50/60 dark:border-violet-500/30 dark:bg-violet-500/[0.07]"
                  : (m.flags || []).length
                    ? "border-rose-200 bg-rose-50/50 dark:border-rose-500/30 dark:bg-rose-500/5"
                    : cx("border-slate-200 dark:border-slate-800", PANEL.cardHover)
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              {canModerate && (
                <input
                  type="checkbox"
                  checked={picked.has(m.id)}
                  onChange={() => toggle(m.id)}
                  aria-label={`Select message from ${m.name}`}
                  className="h-3.5 w-3.5 shrink-0 accent-violet-600"
                />
              )}
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-200">
                {initials(m.name)}
              </span>
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{m.name}</span>
              {m.pinned && <Badge status="success">Pinned</Badge>}
              {m.status === "pending" && <Badge status="warning">Held for review</Badge>}
              <FlagChips flags={m.flags} />
              <span className="ml-auto text-[11px] text-slate-400">{hhmm(m.created_at)}</span>
            </div>

            {m.reply_to && (
              <p className="ml-8 mt-1 flex items-center gap-1 text-[11px] text-slate-400">
                <FiCornerUpLeft aria-hidden="true" />
                replying to {messages.find((x) => x.id === m.reply_to)?.name || "a message"}
              </p>
            )}

            <p className="ml-8 mt-1 break-words text-sm text-slate-600 dark:text-slate-300">{m.text}</p>

            {!!Object.keys(m.reactions || {}).length && (
              <div className="ml-8 mt-1.5 flex flex-wrap gap-1">
                {Object.entries(m.reactions).map(([emoji, count]) => (
                  <span key={emoji} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs tabular-nums dark:bg-slate-800">
                    {emoji} {count}
                  </span>
                ))}
              </div>
            )}

            {m.note && (
              <p className="ml-8 mt-1.5 rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                <span className="font-semibold">Note: </span>{m.note}
              </p>
            )}

            <div className="ml-8 mt-2 flex flex-wrap items-center gap-1">
              {QUICK_REACTIONS.map((emoji) => (
                <button
                  key={emoji}
                  type="button"
                  onClick={() => send("chat.react", { id: m.id, emoji })}
                  className={cx("rounded-md px-1 py-0.5 text-xs hover:bg-slate-100 dark:hover:bg-slate-800", PANEL.t150, focusRing)}
                  aria-label={`React ${emoji}`}
                  title={`React ${emoji}`}
                >
                  {emoji}
                </button>
              ))}
              <ActionButton icon={FiCornerUpLeft} title={`Reply to ${m.name}`} onClick={() => setReplyTo(m)} />
              {canModerate && (
                <>
                  {m.status === "pending" && (
                    <ActionButton icon={FiCheck} label="Approve" tone="emerald" onClick={() => send("chat.approve", { id: m.id })} />
                  )}
                  <ActionButton icon={FiBookmark} label={m.pinned ? "Unpin" : "Pin"} tone="amber" active={m.pinned} onClick={() => send("chat.pin", { id: m.id })} />
                  <ActionButton icon={FiEdit3} title="Add staff note" tone="amber" onClick={() => setNoteFor(m)} />
                  {m.user_id && (
                    <>
                      <ActionButton icon={FiMicOff} title={`Mute ${m.name}`} tone="amber" onClick={() => send("participant.mute", { identity: m.user_id, muted: true })} />
                      <ActionButton icon={FiClock} title={`Time out ${m.name} for 5 min`} tone="amber" onClick={() => send("participant.timeout", { identity: m.user_id, minutes: 5 })} />
                    </>
                  )}
                  <ActionButton icon={FiTrash2} label="Delete" tone="rose" onClick={() => send("chat.delete", { id: m.id })} />
                </>
              )}
            </div>

            {noteFor?.id === m.id && (
              <form onSubmit={saveNote} className="ml-8 mt-2 flex items-center gap-2">
                <Input variant="console" name="note" defaultValue={m.note || ""} placeholder="Staff-only note…" aria-label="Staff note" autoFocus />
                <button type="submit" className={cx(PANEL_PRIMARY, "h-8")}>Save</button>
                <button type="button" onClick={() => setNoteFor(null)} className="rounded-lg px-2 py-1.5 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">Cancel</button>
              </form>
            )}
          </div>
        ))}

        {shown.length === 0 && (
          <EmptyState
            icon={FiMessageSquare}
            title={messages.length === 0 ? "Chat is quiet" : "No matching messages"}
            description={messages.length === 0 ? "New messages appear here instantly." : "Try a different search or filter."}
            className="py-12"
          />
        )}
      </div>

      {/* Unread pill — the "you're scrolled up and missing messages" affordance. */}
      {unread > 0 && (
        <button
          type="button"
          onClick={jumpToLatest}
          className="mx-auto -mt-10 mb-2 inline-flex items-center gap-1.5 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white shadow-lg transition hover:bg-slate-800 dark:bg-white dark:text-slate-900"
        >
          <FiArrowDown aria-hidden="true" /> {unread} new message{unread > 1 ? "s" : ""}
        </button>
      )}

      <div className="shrink-0 border-t border-slate-100 p-3 dark:border-slate-800">
        <p className="mb-1 h-4 truncate text-[11px] text-slate-400" aria-live="polite">
          {typists.length === 1 && `${typists[0]} is typing…`}
          {typists.length === 2 && `${typists[0]} and ${typists[1]} are typing…`}
          {typists.length > 2 && `${typists.length} people are typing…`}
        </p>
        {replyTo && (
          <div className="mb-1.5 flex items-center gap-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs dark:bg-slate-800">
            <FiCornerUpLeft className="shrink-0 text-slate-400" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">
              Replying to <span className="font-semibold">{replyTo.name}</span>
            </span>
            <button type="button" onClick={() => setReplyTo(null)} aria-label="Cancel reply" className="text-slate-400 hover:text-slate-600">
              <FiX />
            </button>
          </div>
        )}
        <form onSubmit={submit} className="flex items-center gap-2">
          <Input
            ref={composer}
            variant="console"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            placeholder="Message the room…  (m)"
            aria-label="Send a chat message"
          />
          {/* Disabled until there is something to send, so the primary action can never
              fire a no-op frame at the socket. `submit` already guards the same condition. */}
          <button
            type="submit"
            disabled={!draft.trim()}
            className={cx(PANEL_PRIMARY, "h-9 w-9 justify-center px-0")}
            aria-label="Send message"
            title="Send message"
          >
            <FiSend className="text-sm" aria-hidden="true" />
          </button>
        </form>
      </div>
    </>
  );
}

// ── Q&A ──────────────────────────────────────────────────────────────────────

export function QATab({ questions, speakers, canModerate, send }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return questions
      .filter((x) => filter === "all" || x.status === filter)
      .filter((x) => !q || `${x.name} ${x.text}`.toLowerCase().includes(q))
      // Pinned first, then most-voted: the running order a moderator reads from.
      .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.votes - a.votes);
  }, [questions, query, filter]);

  return (
    <>
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800">
        <SearchField
          className="flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search questions…"
          label="Search questions"
        />
        <Select variant="console" className="h-8 w-28 py-0 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter questions">
          {QA_FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </Select>
        <ActionButton
          icon={FiDownload}
          title="Export questions as CSV"
          onClick={() => downloadCsv("questions.csv", questions, [
            ["Votes", (q) => q.votes], ["Question", (q) => q.text], ["Asked by", (q) => q.name],
            ["Status", (q) => q.status], ["Assigned to", (q) => q.assigned_name || ""],
          ])}
        />
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {shown.map((q, i) => (
          <div
            key={q.id}
            className={cx(
              "flex gap-2.5 rounded-lg border p-2.5 motion-safe:animate-[zk-fade-in_.25s]",
              PANEL.t150,
              q.pinned
                ? "border-green-300 bg-green-50/70 dark:border-green-500/30 dark:bg-green-500/10"
                : cx("border-slate-200 dark:border-slate-800", PANEL.cardHover)
            )}
          >
            {/* Vote control — an operator can also up/down-weight the running order. */}
            <div className="flex shrink-0 flex-col items-center gap-0.5">
              {/* Position in the running order (pinned first, then most-voted), so a
                  host reading questions aloud can say "number three". Derived from
                  render order — it is not a field on the question. */}
              <span className={cx("text-[10px] font-semibold tabular-nums", PANEL.faint)} aria-hidden="true">
                #{String(i + 1).padStart(2, "0")}
              </span>
              <button
                type="button"
                onClick={() => send("qa.vote", { id: q.id })}
                className={cx("rounded text-slate-400 hover:text-violet-600 dark:hover:text-violet-400", PANEL.t150, focusRing)}
                aria-label={`Upvote question from ${q.name}`}
              >
                <FiChevronUp className="text-base" />
              </button>
              <span className="text-xs font-semibold tabular-nums text-slate-600 dark:text-slate-300">{q.votes}</span>
              {canModerate && (
                <button
                  type="button"
                  onClick={() => send("qa.vote", { id: q.id, down: true })}
                  className={cx("rounded text-slate-400 hover:text-rose-600 dark:hover:text-rose-400", PANEL.t150, focusRing)}
                  aria-label={`Downvote question from ${q.name}`}
                >
                  <FiChevronDown className="text-base" />
                </button>
              )}
            </div>

            <div className="min-w-0 flex-1">
              <p className="break-words text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="text-xs text-slate-400">{q.name} · {hhmm(q.created_at)}</span>
                <Badge tone={QA_STATUS_TONE[q.status]} size="sm">{q.status}</Badge>
                {q.pinned && <Badge tone="success" size="sm">Live on air</Badge>}
                {q.assigned_name && <Badge tone="info" size="sm">→ {q.assigned_name}</Badge>}
                <FlagChips flags={q.flags} />
              </div>

              {canModerate && (
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  {q.status === "pending" && (
                    <ActionButton icon={FiCheck} label="Approve" tone="emerald" onClick={() => send("qa.approve", { id: q.id })} />
                  )}
                  <ActionButton
                    icon={FiBookmark}
                    label={q.pinned ? "Off air" : "On air"}
                    tone="amber"
                    active={q.pinned}
                    onClick={() => send("qa.pin", { id: q.id })}
                  />
                  {q.status !== "answered" && (
                    <ActionButton icon={FiCheckCircle} label="Answered" tone="blue" onClick={() => send("qa.answer", { id: q.id })} />
                  )}
                  {speakers.length > 0 && (
                    <Select
                      variant="console"
                      className="h-7 min-w-0 flex-1 py-0 text-[11px] @lg:flex-none @lg:w-40"
                      value={q.assigned_name ? speakers.find((s) => s.name === q.assigned_name)?.id || "" : ""}
                      onChange={(e) => send("qa.assign", { id: q.id, speaker_id: e.target.value || null })}
                      aria-label="Assign to speaker"
                    >
                      <option value="">Assign speaker…</option>
                      {speakers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </Select>
                  )}
                  <ActionButton icon={FiSlash} label="Dismiss" onClick={() => send("qa.dismiss", { id: q.id })} />
                  <ActionButton icon={FiTrash2} label="Delete" tone="rose" onClick={() => send("qa.delete", { id: q.id })} />
                </div>
              )}
            </div>
          </div>
        ))}

        {shown.length === 0 && (
          <EmptyState
            icon={FiHelpCircle}
            title={questions.length === 0 ? "No questions yet" : "No matching questions"}
            description={questions.length === 0 ? "Questions from the audience land here in real time." : "Try a different search or filter."}
            className="py-12"
          />
        )}
      </div>
    </>
  );
}

export default function ChatQAPanel({
  className, messages, questions, speakers = [], typing = {}, canModerate, loading, send,
}) {
  const [tab, setTab] = useState("chat");

  const tabs = [
    { key: "chat", label: "Chat Moderation", pending: messages.filter((m) => m.status === "pending").length },
    { key: "qa", label: "Q&A", pending: questions.filter((q) => q.status === "pending").length },
  ];

  return (
    <div className={cx("@container flex min-h-0 flex-col overflow-hidden", PANEL.surface, className)}>
      <div role="tablist" className="flex shrink-0 border-b border-slate-200 dark:border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              "flex flex-1 items-center justify-center gap-2 border-b-2 px-3 py-2.5 text-[13px] font-medium",
              PANEL.t150,
              focusRing,
              tab === t.key
                ? "border-violet-600 text-violet-700 dark:border-violet-400 dark:text-violet-300"
                : "border-transparent text-slate-500 hover:bg-violet-50/60 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-violet-500/[0.07] dark:hover:text-white"
            )}
          >
            {t.label}
            {t.pending > 0 && (
              <span className="rounded-full bg-amber-100 px-1.5 text-xs font-semibold text-amber-700 dark:bg-amber-500/15 dark:text-amber-400">
                {t.pending}
              </span>
            )}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex-1 space-y-2 p-3" aria-hidden="true">
          {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
        </div>
      ) : tab === "chat" ? (
        <ChatTab messages={messages} typing={typing} canModerate={canModerate} send={send} />
      ) : (
        <QATab questions={questions} speakers={speakers} canModerate={canModerate} send={send} />
      )}
    </div>
  );
}
