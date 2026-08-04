// client/src/components/watch/ParticipationRail.jsx
// The attendee's participation surface: chat, Q&A, polls, announcements and the shared downloads,
// as one tabbed rail beside the player.
//
// Almost everything here is a REUSED component, and that is the point — the data was already
// flowing to attendees on the socket (services/moderation.VIEWER_CHANNELS carries chat, qa, poll,
// announcement and reaction unfiltered), the page simply had no interface for it:
//
//   chat   -> components/moderation/ChatTab. Permission-aware already: every moderation control it
//             renders is behind `canModerate`, which is false here, so an attendee gets exactly the
//             composer, reactions, reply and report. Forking it would guarantee drift.
//   polls  -> components/moderation/PollVote. Vote + live results, no management — which IS the
//             attendee surface, and the speaker console's too.
//   Q&A    -> AttendeeQA, genuinely new: an attendee's question needs "did mine get through and has
//             anyone answered it", which neither the moderator's triage queue nor the speaker's
//             worklist expresses.
import { useState } from "react";
import {
  FiMessageSquare, FiHelpCircle, FiBarChart2, FiVolume2, FiDownload, FiFileText, FiLock,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { ChatTab } from "../moderation/ChatQAPanel";
import PollVote from "../moderation/PollVote";
import AttendeeQA from "./AttendeeQA";
import { hhmm } from "../../data/moderation";
import { fmtBytes } from "../../data/attendee";

const TABS = [
  { key: "chat", label: "Chat", icon: FiMessageSquare },
  { key: "qa", label: "Q&A", icon: FiHelpCircle },
  { key: "polls", label: "Polls", icon: FiBarChart2 },
  { key: "news", label: "Updates", icon: FiVolume2 },
  { key: "files", label: "Files", icon: FiDownload },
];

function Announcements({ items }) {
  if (!items.length) {
    return (
      <p className="grid place-items-center py-12 text-center text-sm text-slate-500 dark:text-neutral-400">
        Nothing from the host yet. Announcements appear here the moment they're sent.
      </p>
    );
  }
  return (
    <ol className="space-y-2 p-3">
      {items.map((a) => (
        <li
          key={a.id}
          className={cx(
            "rounded-xl border p-3 motion-safe:animate-[zk-fade-in_.25s]",
            a.priority === "urgent"
              ? "border-rose-300 bg-rose-50 dark:border-rose-500/40 dark:bg-rose-500/10"
              : a.priority === "important"
                ? "border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10"
                : "border-slate-200 dark:border-white/10"
          )}
        >
          <p className="break-words text-sm text-slate-800 dark:text-neutral-100">{a.text}</p>
          <p className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
            {a.priority !== "normal" && (
              <Badge tone={a.priority === "urgent" ? "danger" : "warning"} size="sm">
                {a.priority}
              </Badge>
            )}
            {hhmm(a.sent_at || a.created_at)}
          </p>
        </li>
      ))}
    </ol>
  );
}

function Resources({ items, onDownload, downloading }) {
  if (!items.length) {
    return (
      <div className="grid place-items-center py-12 px-6 text-center">
        <FiFileText className="text-2xl text-slate-300 dark:text-neutral-700" aria-hidden="true" />
        <p className="mt-2 text-sm text-slate-500 dark:text-neutral-400">
          No files shared yet.
        </p>
        <p className="mt-1 text-xs text-slate-400">
          Slides and handouts appear here when the organizers release them.
        </p>
      </div>
    );
  }
  return (
    <ul className="space-y-2 p-3">
      {items.map((r) => (
        <li key={r.id} className="flex items-center gap-3 rounded-xl border border-slate-200 p-3 dark:border-white/10">
          <FiFileText className="shrink-0 text-slate-400" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-slate-800 dark:text-neutral-100">{r.filename}</p>
            <p className="truncate text-xs text-slate-400">
              {fmtBytes(r.size_bytes)}
              {r.pages ? ` · ${r.pages} page${r.pages === 1 ? "" : "s"}` : ""}
              {r.shared_by ? ` · ${r.shared_by}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={() => onDownload(r)}
            disabled={downloading === r.id}
            className={cx(
              "inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium transition",
              "text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-white/10 dark:text-neutral-200 dark:hover:bg-white/[0.06]",
              focusRing
            )}
          >
            <FiDownload aria-hidden="true" />
            {downloading === r.id ? "Downloading…" : "Download"}
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function ParticipationRail({
  live, identity, savedQuestions, send, onSaveQuestion, onDownload, downloading, className,
}) {
  const [tab, setTab] = useState("chat");
  const features = live.features || {};

  const pending = {
    qa: live.questions.filter((q) => q.status !== "answered" && q.status !== "dismissed").length,
    polls: live.polls.filter((p) => p.status === "live").length,
    news: live.announcements.length,
    files: live.resources.length,
  };

  return (
    <section
      className={cx(
        "flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white",
        "dark:border-white/10 dark:bg-white/[0.02]",
        className
      )}
      aria-label="Join the conversation"
    >
      <div role="tablist" className="flex shrink-0 overflow-x-auto border-b border-slate-200 dark:border-white/10">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            title={t.label}
            className={cx(
              "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-3 text-xs font-medium transition",
              tab === t.key
                ? "border-violet-500 text-violet-600 dark:text-violet-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-neutral-400 dark:hover:text-white",
              focusRing
            )}
          >
            <t.icon className="text-base" aria-hidden="true" />
            <span className="hidden sm:inline">{t.label}</span>
            {pending[t.key] > 0 && (
              <span className="rounded-full bg-violet-100 px-1.5 text-[10px] font-semibold text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                {pending[t.key] > 99 ? "99+" : pending[t.key]}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {tab === "chat" && (
          features.chat === false ? (
            <p className="grid flex-1 place-items-center px-6 text-center text-sm text-slate-500 dark:text-neutral-400">
              <span>
                <FiLock className="mx-auto mb-2 text-2xl text-slate-300 dark:text-neutral-700" aria-hidden="true" />
                Chat is turned off for this event.
              </span>
            </p>
          ) : (
            // canModerate={false} is what makes this the ATTENDEE's chat: every moderation control
            // inside ChatTab is gated on it, and the report button appears in its place.
            <ChatTab
              messages={live.messages}
              typing={{}}
              canModerate={false}
              send={send}
            />
          )
        )}

        {tab === "qa" && (
          <AttendeeQA
            className="min-h-0 flex-1"
            questions={live.questions}
            identity={identity}
            saved={savedQuestions}
            enabled={features.qa !== false}
            send={send}
            onSave={onSaveQuestion}
          />
        )}

        {tab === "polls" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <PollVote
              className="!rounded-none !border-0 !shadow-none"
              polls={live.polls}
              send={send}
              disabled={features.polls === false}
            />
          </div>
        )}

        {tab === "news" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Announcements items={live.announcements} />
          </div>
        )}

        {tab === "files" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Resources items={live.resources} onDownload={onDownload} downloading={downloading} />
          </div>
        )}
      </div>
    </section>
  );
}
