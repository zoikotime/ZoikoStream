// client/src/components/moderation/ChatQAPanel.jsx
// Center panel — tabbed Chat Moderation (approve / delete / pin) and
// Q&A moderation (approve / mark answered / delete).
import { useState } from "react";
import { FiCheck, FiTrash2, FiBookmark, FiCheckCircle, FiChevronUp } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { ActionButton } from "./Panel";
import { initials } from "../../data/moderation";

export default function ChatQAPanel({
  className,
  messages,
  questions,
  onApproveMessage,
  onDeleteMessage,
  onPinMessage,
  onApproveQuestion,
  onAnswerQuestion,
  onDeleteQuestion,
}) {
  const [tab, setTab] = useState("chat");
  const chatPending = messages.filter((m) => m.status === "pending").length;
  const qaPending = questions.filter((q) => q.status === "pending").length;
  const sortedQuestions = [...questions].sort((a, b) => b.votes - a.votes);

  const tabs = [
    { key: "chat", label: "Chat Moderation", pending: chatPending },
    { key: "qa", label: "Q&A", pending: qaPending },
  ];

  return (
    <div className={cx("flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900", className)}>
      <div role="tablist" className="flex shrink-0 border-b border-slate-200 dark:border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              "flex flex-1 items-center justify-center gap-2 border-b-2 px-3 py-3 text-sm font-medium transition",
              tab === t.key
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
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

      <div className="flex-1 space-y-2 overflow-y-auto p-3">
        {/* Chat moderation */}
        {tab === "chat" &&
          messages.map((m) => (
            <div
              key={m.id}
              className={cx(
                "rounded-xl border p-3",
                m.pinned
                  ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-500/10"
                  : m.flagged
                    ? "border-rose-200 bg-rose-50/50 dark:border-rose-500/30 dark:bg-rose-500/5"
                    : "border-slate-100 dark:border-slate-800"
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-200">
                  {initials(m.name)}
                </span>
                <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{m.name}</span>
                {m.pinned && <Badge status="success">Pinned</Badge>}
                {m.flagged && <Badge status="error">Flagged</Badge>}
                {m.status === "pending" && <Badge status="warning">Pending</Badge>}
                <span className="ml-auto text-[11px] text-slate-400">{m.time}</span>
              </div>
              <p className="ml-8 mt-1 break-words text-sm text-slate-600 dark:text-slate-300">{m.text}</p>
              <div className="ml-8 mt-2 flex items-center gap-1">
                {m.status === "pending" && (
                  <ActionButton icon={FiCheck} label="Approve" tone="emerald" onClick={() => onApproveMessage(m.id)} />
                )}
                <ActionButton icon={FiBookmark} label={m.pinned ? "Unpin" : "Pin"} tone="amber" active={m.pinned} onClick={() => onPinMessage(m.id)} />
                <ActionButton icon={FiTrash2} label="Delete" tone="rose" onClick={() => onDeleteMessage(m.id)} />
              </div>
            </div>
          ))}
        {tab === "chat" && messages.length === 0 && <p className="py-8 text-center text-sm text-slate-400">No messages to moderate.</p>}

        {/* Q&A moderation */}
        {tab === "qa" &&
          sortedQuestions.map((q) => (
            <div key={q.id} className="flex gap-3 rounded-xl border border-slate-100 p-3 dark:border-slate-800">
              <div className="flex h-11 w-10 shrink-0 flex-col items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                <FiChevronUp className="text-base" />
                {q.votes}
              </div>
              <div className="min-w-0 flex-1">
                <p className="break-words text-sm text-slate-700 dark:text-slate-200">{q.text}</p>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-400">{q.name}</span>
                  {q.status === "pending" && <Badge status="warning">Pending</Badge>}
                  {q.status === "approved" && <Badge status="info">Approved</Badge>}
                  {q.status === "answered" && <Badge status="success">Answered</Badge>}
                </div>
                <div className="mt-2 flex items-center gap-1">
                  {q.status === "pending" && (
                    <ActionButton icon={FiCheck} label="Approve" tone="emerald" onClick={() => onApproveQuestion(q.id)} />
                  )}
                  {q.status !== "answered" && (
                    <ActionButton icon={FiCheckCircle} label="Answered" tone="blue" onClick={() => onAnswerQuestion(q.id)} />
                  )}
                  <ActionButton icon={FiTrash2} label="Delete" tone="rose" onClick={() => onDeleteQuestion(q.id)} />
                </div>
              </div>
            </div>
          ))}
        {tab === "qa" && questions.length === 0 && <p className="py-8 text-center text-sm text-slate-400">No questions yet.</p>}
      </div>
    </div>
  );
}
