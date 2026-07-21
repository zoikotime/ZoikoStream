// client/src/components/moderation/ModeratorSidebar.jsx
// Right panel — Poll Management, Announcements, and the Live Activity Feed.
import { useState } from "react";
import {
  FiPlay, FiXCircle, FiPlus, FiSend, FiVolume2,
  FiUserPlus, FiMessageSquare, FiHelpCircle, FiBarChart2, FiShield, FiActivity,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Panel, { ActionButton } from "./Panel";

const field =
  "w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 shadow-sm outline-none placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200";

const POLL_STATUS = { live: "success", draft: "neutral", closed: "info" };
const cap = (s) => s[0].toUpperCase() + s.slice(1);

function PollResults({ options }) {
  const total = options.reduce((s, o) => s + o.votes, 0);
  return (
    <div className="mt-2 space-y-1.5">
      {options.map((o, i) => {
        const pct = total ? Math.round((o.votes / total) * 100) : 0;
        return (
          <div key={i} className="relative overflow-hidden rounded-lg border border-slate-200 px-2.5 py-1.5 dark:border-slate-700">
            <div className="absolute inset-y-0 left-0 bg-emerald-500/15" style={{ width: `${pct}%` }} />
            <div className="relative flex items-center justify-between text-xs">
              <span className="font-medium text-slate-700 dark:text-slate-200">{o.label}</span>
              <span className="tabular-nums text-slate-500 dark:text-slate-400">{pct}%</span>
            </div>
          </div>
        );
      })}
      <p className="text-[11px] text-slate-400">{total.toLocaleString()} votes</p>
    </div>
  );
}

function PollManagement({ polls, onLaunch, onClose, onCreate }) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [opts, setOpts] = useState(["", ""]);
  const setOpt = (i, v) => setOpts((p) => p.map((o, j) => (j === i ? v : o)));

  const submit = (e) => {
    e.preventDefault();
    const clean = opts.map((o) => o.trim()).filter(Boolean);
    if (!question.trim() || clean.length < 2) return;
    onCreate(question.trim(), clean);
    setQuestion("");
    setOpts(["", ""]);
    setOpen(false);
  };

  return (
    <Panel
      title="Poll Management"
      count={polls.length}
      scroll={false}
      action={
        <button
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <FiPlus /> New
        </button>
      }
    >
      {open && (
        <form onSubmit={submit} className="mb-3 space-y-2 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Poll question" aria-label="Poll question" className={field} />
          {opts.map((o, i) => (
            <input key={i} value={o} onChange={(e) => setOpt(i, e.target.value)} placeholder={`Option ${i + 1}`} aria-label={`Option ${i + 1}`} className={field} />
          ))}
          <div className="flex items-center justify-between">
            <button type="button" onClick={() => setOpts((o) => [...o, ""])} className="text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400">
              + Add option
            </button>
            <div className="flex gap-2">
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg px-2.5 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800">
                Cancel
              </button>
              <button type="submit" className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-emerald-500">
                Launch
              </button>
            </div>
          </div>
        </form>
      )}

      <div className="space-y-3">
        {polls.map((p) => (
          <div key={p.id} className="rounded-xl border border-slate-100 p-3 dark:border-slate-800">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{p.question}</p>
              <Badge status={POLL_STATUS[p.status]} live={p.status === "live"}>{cap(p.status)}</Badge>
            </div>
            {(p.status === "live" || p.status === "closed") && <PollResults options={p.options} />}
            <div className="mt-2 flex gap-1">
              {p.status === "draft" && <ActionButton icon={FiPlay} label="Launch" tone="emerald" onClick={() => onLaunch(p.id)} />}
              {p.status === "live" && <ActionButton icon={FiXCircle} label="Close poll" tone="rose" onClick={() => onClose(p.id)} />}
            </div>
          </div>
        ))}
        {polls.length === 0 && <p className="py-6 text-center text-sm text-slate-400">No polls yet.</p>}
      </div>
    </Panel>
  );
}

function Announcements({ announcements, onPost }) {
  const [text, setText] = useState("");
  const post = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    onPost(t);
    setText("");
  };
  return (
    <Panel title="Announcements" scroll={false}>
      <form onSubmit={post} className="mb-3 flex items-center gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Broadcast to all viewers…" aria-label="Announcement message" className={cx(field, "flex-1")} />
        <button type="submit" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-emerald-600 text-white hover:bg-emerald-500" aria-label="Post announcement">
          <FiSend className="text-sm" />
        </button>
      </form>
      <div className="space-y-2">
        {announcements.map((a) => (
          <div key={a.id} className="flex items-start gap-2 rounded-xl border border-slate-100 px-3 py-2 dark:border-slate-800">
            <FiVolume2 className="mt-0.5 shrink-0 text-emerald-500" />
            <div className="min-w-0 flex-1">
              <p className="break-words text-sm text-slate-700 dark:text-slate-200">{a.text}</p>
              <p className="text-[11px] text-slate-400">{a.time}</p>
            </div>
          </div>
        ))}
        {announcements.length === 0 && <p className="py-4 text-center text-sm text-slate-400">No announcements yet.</p>}
      </div>
    </Panel>
  );
}

const ACT_ICON = {
  join: { icon: FiUserPlus, tone: "text-emerald-500" },
  chat: { icon: FiMessageSquare, tone: "text-blue-500" },
  qa: { icon: FiHelpCircle, tone: "text-violet-500" },
  poll: { icon: FiBarChart2, tone: "text-amber-500" },
  mod: { icon: FiShield, tone: "text-rose-500" },
  system: { icon: FiActivity, tone: "text-slate-500" },
};

function ActivityFeed({ activity }) {
  return (
    <Panel title="Live Activity" scroll={false}>
      <ol className="space-y-3">
        {activity.map((a) => {
          const { icon: Icon, tone } = ACT_ICON[a.kind] || ACT_ICON.system;
          return (
            <li key={a.id} className="flex items-start gap-2.5">
              <Icon className={cx("mt-0.5 shrink-0", tone)} />
              <div className="min-w-0 flex-1">
                <p className="break-words text-sm text-slate-600 dark:text-slate-300">{a.text}</p>
                <p className="text-[11px] text-slate-400">{a.time}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

export default function ModeratorSidebar({
  className,
  polls,
  announcements,
  activity,
  onLaunchPoll,
  onClosePoll,
  onCreatePoll,
  onPostAnnouncement,
}) {
  return (
    <div className={cx("flex min-h-0 flex-col gap-4 overflow-y-auto", className)}>
      <PollManagement polls={polls} onLaunch={onLaunchPoll} onClose={onClosePoll} onCreate={onCreatePoll} />
      <Announcements announcements={announcements} onPost={onPostAnnouncement} />
      <ActivityFeed activity={activity} />
    </div>
  );
}
