// client/src/components/watch/AttendeeControls.jsx
// The strip under the player: reactions, raise hand, bookmark, reminder, and the live connection
// readout.
//
// Two honesty notes that shape what is here:
//
//   * QUEUE POSITION. The brief asks for it, and an attendee cannot be told it: the presence roster
//     is a moderator-tier projection (services/moderation.SPEAKER_PRESENCE_KEYS onwards), so an
//     attendee legitimately does not know who else has their hand up. Rather than invent a number,
//     this shows the state that IS true — "your hand is up, the moderators can see it" — and the
//     moderator's private reply is what carries "you're next" (participant.notice, already wired).
//
//   * REMINDERS record intent. There is no scheduled-delivery worker in this platform, so a
//     reminder is what this page counts down to; it does not send an email. Said on the control
//     itself, because a reminder that silently never arrives is worse than no reminder.
import { useState } from "react";
import {
  FiActivity, FiBell, FiBellOff, FiBookmark, FiWifi, FiWifiOff, FiRefreshCw, FiCheck,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { Select } from "../../ui/forms";
import Reactions from "./Reactions";
import { REMINDER_OFFSETS, fmtCountdown, msUntil } from "../../data/attendee";

const CONNECTION = {
  open: { icon: FiWifi, label: "Live updates on", tone: "text-emerald-600 dark:text-emerald-400" },
  connecting: { icon: FiRefreshCw, label: "Connecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  reconnecting: { icon: FiRefreshCw, label: "Reconnecting", tone: "text-amber-600 dark:text-amber-400", spin: true },
  offline: { icon: FiWifiOff, label: "Offline", tone: "text-rose-600 dark:text-rose-400" },
  unauthorized: { icon: FiWifiOff, label: "Not authorized", tone: "text-rose-600 dark:text-rose-400" },
};

const latencyTone = (ms) =>
  ms == null ? "text-slate-400" : ms < 200 ? "text-emerald-500" : ms < 600 ? "text-amber-500" : "text-rose-500";

export default function AttendeeControls({
  live, connection, features = {}, handRaised, bookmarked, reminderAt, startsAt,
  onReaction, send, onHand, onBookmark, onReminder, className,
}) {
  const [reminderOpen, setReminderOpen] = useState(false);
  const conn = CONNECTION[connection.status] || CONNECTION.connecting;
  const until = msUntil(startsAt);

  return (
    <div className={cx("space-y-3", className)}>
      <Reactions
        onReaction={onReaction}
        send={send}
        enabled={features.reactions !== false && live.isLive}
        totals={live.reactions}
      />

      <div className="flex flex-wrap items-center gap-2">
        {features.raise_hand !== false && (
          <button
            type="button"
            onClick={onHand}
            aria-pressed={handRaised}
            className={cx(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition",
              handRaised
                ? "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-500/50 dark:bg-amber-500/15 dark:text-amber-300"
                : "border-slate-200 text-slate-700 hover:border-violet-300 dark:border-white/10 dark:text-neutral-200 dark:hover:border-violet-500/40",
              focusRing
            )}
          >
            ✋ {handRaised ? "Lower hand" : "Raise hand"}
          </button>
        )}

        <button
          type="button"
          onClick={() => onBookmark(!bookmarked)}
          aria-pressed={bookmarked}
          className={cx(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition",
            bookmarked
              ? "border-violet-400 bg-violet-50 text-violet-700 dark:border-violet-500/50 dark:bg-violet-500/15 dark:text-violet-300"
              : "border-slate-200 text-slate-700 hover:border-violet-300 dark:border-white/10 dark:text-neutral-200 dark:hover:border-violet-500/40",
            focusRing
          )}
        >
          <FiBookmark aria-hidden="true" /> {bookmarked ? "Saved" : "Save"}
        </button>

        {/* A reminder only makes sense before the thing starts. */}
        {startsAt && until != null && until > 0 && (
          <div className="relative">
            <button
              type="button"
              onClick={() => setReminderOpen((v) => !v)}
              aria-expanded={reminderOpen}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition",
                reminderAt
                  ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:border-emerald-500/50 dark:bg-emerald-500/15 dark:text-emerald-300"
                  : "border-slate-200 text-slate-700 hover:border-violet-300 dark:border-white/10 dark:text-neutral-200 dark:hover:border-violet-500/40",
                focusRing
              )}
            >
              {reminderAt ? <FiBell aria-hidden="true" /> : <FiBellOff aria-hidden="true" />}
              {reminderAt ? "Reminder set" : "Remind me"}
            </button>

            {reminderOpen && (
              <div className="absolute left-0 top-full z-20 mt-2 w-64 rounded-xl border border-slate-200 bg-white p-3 shadow-lg dark:border-white/10 dark:bg-neutral-950">
                <Select
                  variant="console"
                  aria-label="Remind me"
                  defaultValue={15}
                  onChange={(e) => { onReminder(Number(e.target.value)); setReminderOpen(false); }}
                >
                  {REMINDER_OFFSETS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </Select>
                {/* Stated, not implied. */}
                <p className="mt-2 text-[11px] text-slate-400">
                  Shown on your dashboard as a countdown. This deployment has no scheduled email
                  sender, so it won't arrive in your inbox.
                </p>
                {reminderAt && (
                  <button
                    type="button"
                    onClick={() => { onReminder(null); setReminderOpen(false); }}
                    className="mt-2 text-xs font-medium text-rose-600 hover:underline dark:text-rose-400"
                  >
                    Clear reminder
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        <div className="ml-auto flex items-center gap-3 text-xs">
          <span className={cx("inline-flex items-center gap-1.5 font-medium", conn.tone)}>
            <conn.icon className={cx(conn.spin && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
            {conn.label}
            {connection.attempt > 0 && connection.status !== "open" && ` ·${connection.attempt}`}
          </span>
          {connection.latency != null && (
            <span
              className={cx("inline-flex items-center gap-1 font-medium tabular-nums", latencyTone(connection.latency))}
              title="Round-trip to the live-updates server. The player shows its own media latency."
            >
              <FiActivity aria-hidden="true" /> {connection.latency} ms
            </span>
          )}
        </div>
      </div>

      {/* What a raised hand actually means here — see the header note on queue position. */}
      {handRaised && (
        <p className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          <FiCheck aria-hidden="true" className="shrink-0" />
          Your hand is up and the moderators can see it. They'll message you here if they bring you
          on — you won't be unmuted without being asked.
        </p>
      )}

      {until != null && until > 0 && !live.isLive && (
        <p className="text-xs text-slate-500 dark:text-neutral-400">
          Starts {fmtCountdown(until)}
          {reminderAt && <Badge tone="success" size="sm" className="ml-2">reminder set</Badge>}
        </p>
      )}
    </div>
  );
}
