// client/src/components/moderation/ModeratorSidebar.jsx
// Right column — Poll Management, Announcements, and the Live Activity Feed.
// Poll results, announcement delivery and the feed all stream in over the socket;
// scheduled polls/announcements and poll countdowns are flipped by the server's ticker,
// so this file never has to time anything itself beyond rendering the countdown.
//
// The three panels are exported individually because the HOST console needs the same
// surfaces arranged as tabs rather than a column (see components/host/HostPanel). The
// default export is the moderator console's column. One implementation, two layouts —
// nothing here is duplicated on the host side.
import { useMemo, useState } from "react";
import {
  FiPlay, FiXCircle, FiPlus, FiSend, FiVolume2, FiEdit3, FiTrash2, FiClock,
  FiUserPlus, FiUserMinus, FiMessageSquare, FiHelpCircle, FiBarChart2, FiShield,
  FiActivity, FiDownload, FiVideo, FiX, FiCheck, FiUsers,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import { Input, Select } from "../../ui/forms";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import { PANEL, PANEL_PRIMARY, PANEL_SECONDARY } from "./panelTokens";
import SearchField from "./SearchField";
import useInterval from "../../hooks/useInterval";
import { downloadCsv } from "../../utils/export";
import {
  hhmm, POLL_STATUS_TONE, POLL_DURATIONS, ANNOUNCEMENT_PRIORITIES,
  ANNOUNCEMENT_TEMPLATES, ACTIVITY_FILTERS, ACTIVITY_GROUPS,
} from "../../data/moderation";

const cap = (s = "") => s.charAt(0).toUpperCase() + s.slice(1);

function PollResults({ options }) {
  const total = options.reduce((s, o) => s + o.votes, 0);
  return (
    <div className="mt-2 space-y-1.5">
      {options.map((o, i) => {
        const pct = total ? Math.round((o.votes / total) * 100) : 0;
        return (
          <div key={i} className={cx("relative overflow-hidden px-2.5 py-1.5", PANEL.inset)}>
            {/* Width transition = the bar visibly grows as votes land. */}
            <div className="absolute inset-y-0 left-0 bg-violet-500/20 transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${pct}%` }} />
            <div className="relative flex items-center justify-between text-xs">
              <span className="font-medium text-slate-700 dark:text-slate-200">{o.label}</span>
              <span className="tabular-nums text-slate-500 dark:text-slate-400">
                {o.votes.toLocaleString()} · {pct}%
              </span>
            </div>
          </div>
        );
      })}
      <p className="text-[11px] text-slate-400">{total.toLocaleString()} votes</p>
    </div>
  );
}

// Countdown to `closes_at`. The server closes the poll when it expires; this only shows
// the remaining time, so the two can't disagree about whether voting is open.
function Countdown({ closesAt }) {
  const target = new Date(closesAt).getTime();
  const [left, setLeft] = useState(() => Math.max(0, Math.round((target - Date.now()) / 1000)));
  useInterval(() => setLeft(Math.max(0, Math.round((target - Date.now()) / 1000))), 1000, left > 0);
  if (left <= 0) return <Badge tone="neutral" size="sm">Closing…</Badge>;
  return (
    <Badge tone={left <= 10 ? "danger" : "warning"} size="sm">
      <FiClock aria-hidden="true" /> {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}
    </Badge>
  );
}

function PollForm({ poll, onSubmit, onCancel }) {
  const [question, setQuestion] = useState(poll?.question || "");
  const [opts, setOpts] = useState(() => (poll?.options?.length ? poll.options.map((o) => o.label) : ["", ""]));
  const [duration, setDuration] = useState(0);
  const [scheduledAt, setScheduledAt] = useState("");
  const setOpt = (i, v) => setOpts((p) => p.map((o, j) => (j === i ? v : o)));

  const submit = (e) => {
    e.preventDefault();
    const clean = opts.map((o) => o.trim()).filter(Boolean);
    if (!question.trim() || clean.length < 2) return;
    onSubmit({
      question: question.trim(),
      options: clean,
      duration_seconds: duration,
      // datetime-local has no timezone; convert through Date so the server gets UTC.
      scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
    });
  };

  return (
    <form onSubmit={submit} className={cx("mb-2.5 space-y-2 p-2.5", PANEL.card)}>
      <Input variant="console" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Poll question" aria-label="Poll question" autoFocus />
      {opts.map((o, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input variant="console" value={o} onChange={(e) => setOpt(i, e.target.value)} placeholder={`Option ${i + 1}`} aria-label={`Option ${i + 1}`} />
          {opts.length > 2 && (
            <button type="button" onClick={() => setOpts((p) => p.filter((_, j) => j !== i))} aria-label={`Remove option ${i + 1}`} className="shrink-0 text-slate-400 hover:text-rose-500">
              <FiX />
            </button>
          )}
        </div>
      ))}
      {/* Capped at the server's own limit (services/moderation._clean_options) so extra
          options are refused here, not silently dropped on save. */}
      {opts.length < 10 && (
        <button type="button" onClick={() => setOpts((o) => [...o, ""])} className={cx("rounded text-[11px] font-semibold text-violet-600 hover:underline dark:text-violet-400", focusRing)}>
          + Add option
        </button>
      )}

      <div className="flex items-center gap-2">
        <Select variant="console" className="flex-1" value={duration} onChange={(e) => setDuration(Number(e.target.value))} aria-label="Poll duration">
          {POLL_DURATIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
        </Select>
        <Input
          variant="console"
          type="datetime-local"
          value={scheduledAt}
          onChange={(e) => setScheduledAt(e.target.value)}
          aria-label="Schedule poll for later"
          title="Leave empty to launch now"
          className="flex-1"
        />
      </div>

      <div className="flex items-center justify-end gap-2">
        <button type="button" onClick={onCancel} className={PANEL_SECONDARY}>
          Cancel
        </button>
        <button type="submit" className={PANEL_PRIMARY}>
          {poll ? "Save" : scheduledAt ? "Schedule" : "Launch"}
        </button>
      </div>
    </form>
  );
}

export function PollManagement({ polls, canModerate, send }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);

  const create = (data) => {
    send("poll.create", data);
    setOpen(false);
  };
  const update = (data) => {
    send("poll.update", { id: editing.id, ...data });
    setEditing(null);
  };

  return (
    <Panel
      title="Poll Management"
      count={polls.length}
      scroll={false}
      action={
        <div className="flex items-center gap-1">
          <ActionButton
            icon={FiDownload}
            title="Export poll results as CSV"
            onClick={() => downloadCsv("poll-results.csv", polls.flatMap((p) => (p.options || []).map((o) => ({ ...o, poll: p }))), [
              ["Poll", (r) => r.poll.question], ["Status", (r) => r.poll.status],
              ["Option", (r) => r.label], ["Votes", (r) => r.votes],
            ])}
          />
          {canModerate && (
            <button
              type="button"
              onClick={() => { setOpen((v) => !v); setEditing(null); }}
              aria-expanded={open}
              className={PANEL_PRIMARY}
              title={open ? "Close the new-poll form" : "Create a poll"}
            >
              <FiPlus aria-hidden="true" /> New
            </button>
          )}
        </div>
      }
    >
      {open && canModerate && <PollForm onSubmit={create} onCancel={() => setOpen(false)} />}

      <div className="space-y-3">
        {polls.map((p) =>
          editing?.id === p.id ? (
            <PollForm key={p.id} poll={p} onSubmit={update} onCancel={() => setEditing(null)} />
          ) : (
            <div key={p.id} className={cx("p-2.5 motion-safe:animate-[zk-fade-in_.25s]", PANEL.card, PANEL.cardHover, PANEL.t150)}>
              <div className="flex items-start justify-between gap-2">
                <p className={cx("min-w-0 break-words text-[13px] font-semibold leading-snug", PANEL.heading)}>{p.question}</p>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Badge status={POLL_STATUS_TONE[p.status]} live={p.status === "live"}>{cap(p.status)}</Badge>
                  {p.status === "live" && p.closes_at && <Countdown closesAt={p.closes_at} />}
                </div>
              </div>

              {/* Options count and total responses, both read off the poll's own options
                  array. A "completion rate" would need the concurrent audience size, which
                  this component is never passed — so it is omitted rather than estimated. */}
              <dl className={cx("mt-1.5 flex items-center gap-3 text-[11px]", PANEL.muted)}>
                <div className="flex items-center gap-1">
                  <dt>Options</dt>
                  <dd className={cx("font-semibold tabular-nums", PANEL.body)}>{(p.options || []).length}</dd>
                </div>
                <div className="flex items-center gap-1">
                  <dt className="flex items-center gap-1"><FiUsers aria-hidden="true" /> Responses</dt>
                  <dd className={cx("font-semibold tabular-nums", PANEL.body)}>
                    {(p.options || []).reduce((t, o) => t + (o.votes || 0), 0).toLocaleString()}
                  </dd>
                </div>
                {p.created_at && (
                  <div className="ml-auto flex items-center gap-1">
                    <dt className="sr-only">Created</dt>
                    <dd className={PANEL.faint}>{hhmm(p.created_at)}</dd>
                  </div>
                )}
              </dl>

              {p.status === "scheduled" && p.scheduled_at && (
                <p className={cx("mt-1 text-[11px]", PANEL.faint)}>Launches at {hhmm(p.scheduled_at)}</p>
              )}
              {(p.status === "live" || p.status === "closed") && <PollResults options={p.options || []} />}

              {canModerate && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {p.status !== "live" && p.status !== "closed" && (
                    <ActionButton icon={FiPlay} label="Launch" tone="violet" onClick={() => send("poll.launch", { id: p.id })} />
                  )}
                  {p.status === "live" && (
                    <ActionButton icon={FiXCircle} label="Close poll" tone="rose" onClick={() => send("poll.close", { id: p.id })} />
                  )}
                  {p.status !== "closed" && (
                    <ActionButton icon={FiEdit3} title="Edit poll" onClick={() => { setEditing(p); setOpen(false); }} />
                  )}
                  <ActionButton icon={FiTrash2} title="Delete poll" tone="rose" onClick={() => send("poll.delete", { id: p.id })} />
                </div>
              )}
            </div>
          )
        )}
        {polls.length === 0 && (
          <EmptyState icon={FiBarChart2} title="No polls yet" description="Launch one to get a read on the room." className="py-8" />
        )}
      </div>
    </Panel>
  );
}

export function Announcements({ announcements, canModerate, send }) {
  const [text, setText] = useState("");
  const [priority, setPriority] = useState("normal");
  const [scheduledAt, setScheduledAt] = useState("");

  const post = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    send("announce.send", {
      text: t,
      priority,
      scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
    });
    setText("");
    setScheduledAt("");
    setPriority("normal");
  };

  return (
    <Panel title="Announcements" count={announcements.length} scroll={false}>
      {canModerate && (
        <form onSubmit={post} className="mb-3 space-y-2">
          {/* Templates — the sentences a moderator types on every event. */}
          <div className="flex flex-wrap gap-1">
            {ANNOUNCEMENT_TEMPLATES.map((t) => (
              <button
                key={t.label}
                type="button"
                onClick={() => setText(t.text)}
                className={cx(
                  "rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-600",
                  "hover:border-violet-300 hover:bg-violet-50 hover:text-violet-700",
                  "dark:border-slate-700 dark:text-slate-300 dark:hover:border-violet-500/40 dark:hover:bg-violet-500/10 dark:hover:text-violet-300",
                  PANEL.t150, focusRing
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
          <Input variant="console" value={text} onChange={(e) => setText(e.target.value)} placeholder="Broadcast to all viewers…" aria-label="Announcement message" />
          <div className="flex items-center gap-2">
            <Select variant="console" className="h-9 w-28 py-0 text-[13px]" value={priority} onChange={(e) => setPriority(e.target.value)} aria-label="Announcement priority">
              {ANNOUNCEMENT_PRIORITIES.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </Select>
            <Input
              variant="console"
              type="datetime-local"
              value={scheduledAt}
              onChange={(e) => setScheduledAt(e.target.value)}
              aria-label="Schedule announcement"
              title="Leave empty to send now"
              className="flex-1"
            />
            <button
              type="submit"
              disabled={!text.trim()}
              className={cx(PANEL_PRIMARY, "h-9 w-9 justify-center px-0")}
              aria-label={scheduledAt ? "Schedule announcement" : "Send announcement"}
              title={scheduledAt ? "Schedule announcement" : "Send announcement"}
            >
              {scheduledAt ? <FiClock className="text-sm" aria-hidden="true" /> : <FiSend className="text-sm" aria-hidden="true" />}
            </button>
          </div>
        </form>
      )}

      <div className="space-y-2">
        {announcements.map((a) => {
          const tone = ANNOUNCEMENT_PRIORITIES.find((p) => p.key === a.priority);
          return (
            <div key={a.id} className={cx("flex items-start gap-2 px-2.5 py-2 motion-safe:animate-[zk-fade-in_.25s]", PANEL.card, PANEL.t150)}>
              <FiVolume2 className="mt-0.5 shrink-0 text-violet-500 dark:text-violet-400" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="break-words text-sm text-slate-700 dark:text-slate-200">{a.text}</p>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {a.priority !== "normal" && <Badge tone={tone?.tone} size="sm">{tone?.label}</Badge>}
                  {/* Delivery status: a real connection count at send time, or the pending schedule. */}
                  {a.sent_at ? (
                    <Badge tone="success" size="sm">
                      <FiCheck aria-hidden="true" /> Sent {hhmm(a.sent_at)}
                      {a.delivered_to != null && ` · ${a.delivered_to} connected`}
                    </Badge>
                  ) : (
                    <Badge tone="warning" size="sm"><FiClock aria-hidden="true" /> {hhmm(a.scheduled_at)}</Badge>
                  )}
                </div>
              </div>
              {canModerate && <ActionButton icon={FiTrash2} title="Delete announcement" tone="rose" onClick={() => send("announce.delete", { id: a.id })} />}
            </div>
          );
        })}
        {announcements.length === 0 && (
          <EmptyState icon={FiVolume2} title="No announcements yet" description="Broadcasts you send appear here with their delivery status." className="py-8" />
        )}
      </div>
    </Panel>
  );
}

const ACT_ICON = {
  join: { icon: FiUserPlus, tone: "text-emerald-500" },
  leave: { icon: FiUserMinus, tone: "text-slate-400" },
  chat: { icon: FiMessageSquare, tone: "text-blue-500" },
  qa: { icon: FiHelpCircle, tone: "text-violet-500" },
  poll: { icon: FiBarChart2, tone: "text-amber-500" },
  mod: { icon: FiShield, tone: "text-rose-500" },
  role: { icon: FiUserPlus, tone: "text-indigo-500" },
  recording: { icon: FiVideo, tone: "text-rose-500" },
  system: { icon: FiActivity, tone: "text-slate-500" },
};

export function ActivityFeed({ activity }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const kinds = ACTIVITY_GROUPS[filter];
    return activity
      .filter((a) => !kinds || kinds.includes(a.kind))
      .filter((a) => !q || `${a.text} ${a.actor || ""}`.toLowerCase().includes(q));
  }, [activity, query, filter]);

  return (
    <Panel
      title="Live Activity"
      count={activity.length}
      scroll={false}
      action={
        <ActionButton
          icon={FiDownload}
          title="Export activity feed as CSV"
          onClick={() => downloadCsv("activity-feed.csv", activity, [
            ["Time", (a) => hhmm(a.created_at)], ["Type", (a) => a.kind],
            ["Event", (a) => a.text], ["Actor", (a) => a.actor || ""],
          ])}
        />
      }
    >
      <div className="mb-2 flex items-center gap-2">
        <SearchField
          className="flex-1"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search activity…"
          label="Search activity"
        />
        <Select variant="console" className="h-8 w-28 py-0 text-[13px]" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter activity">
          {ACTIVITY_FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </Select>
      </div>

      {/* Vertical timeline: one hairline rail behind the icon column, drawn per-item as a
          ::before so it stops cleanly at the last entry instead of trailing into empty space.
          The newest entry sits at the top and is the only one at full text contrast. */}
      <ol className="relative">
        {shown.map((a, i) => {
          const { icon: Icon, tone } = ACT_ICON[a.kind] || ACT_ICON.system;
          const newest = i === 0;
          return (
            <li
              key={a.id}
              className={cx(
                "relative flex items-start gap-2.5 pb-2.5 pl-0 last:pb-0 motion-safe:animate-[zk-fade-in_.25s]",
                "before:absolute before:left-[9px] before:top-5 before:h-full before:w-px before:bg-slate-200 last:before:hidden dark:before:bg-slate-700"
              )}
            >
              <span
                className={cx(
                  "relative z-[1] grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full border bg-white dark:bg-slate-900",
                  newest ? "border-violet-300 dark:border-violet-500/40" : "border-slate-200 dark:border-slate-700"
                )}
              >
                <Icon className={cx("h-2.5 w-2.5", tone)} aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <p className={cx("break-words text-[13px] leading-snug", newest ? PANEL.body : PANEL.muted)}>
                  {a.text}
                </p>
                <p className={cx("mt-0.5 text-[11px] tabular-nums", PANEL.faint)}>
                  {hhmm(a.created_at)}{a.actor ? ` · ${a.actor}` : ""}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
      {shown.length === 0 && (
        <EmptyState
          icon={FiActivity}
          title={activity.length === 0 ? "Nothing yet" : "No matching activity"}
          description={activity.length === 0 ? "Joins, moderation and broadcasts land here as they happen." : "Try a different search or filter."}
          className="py-8"
        />
      )}
    </Panel>
  );
}

export default function ModeratorSidebar({ className, polls, announcements, activity, canModerate, loading, send }) {
  if (loading) {
    return (
      <div className={cx("flex min-h-0 flex-col gap-4 overflow-y-auto", className)} aria-hidden="true">
        {[0, 1, 2].map((i) => <Skeleton key={i} variant="block" className="shrink-0" />)}
      </div>
    );
  }
  return (
    <div className={cx("flex min-h-0 flex-col gap-4 overflow-y-auto", className)}>
      <PollManagement polls={polls} canModerate={canModerate} send={send} />
      <Announcements announcements={announcements} canModerate={canModerate} send={send} />
      <ActivityFeed activity={activity} />
    </div>
  );
}
