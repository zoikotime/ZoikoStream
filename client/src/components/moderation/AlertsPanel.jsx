// client/src/components/moderation/AlertsPanel.jsx
// Alert Center + abuse summary.
//
// Nothing here is fetched. Alerts are derived from the envelope stream in hooks/useLiveEvent
// (they are transitions — "the host ended the event" — which no snapshot can express), and the
// abuse figures are computed from the messages and participants this console ALREADY holds.
// That is deliberate: adding a server-side abuse endpoint would mean a query per tick to
// recount rows the client has in memory.
//
// Two detections in the brief are honestly absent rather than faked:
//   • Fake account detection — this platform has no signal for it (no device fingerprint, no
//     email reputation, no IP history). A heuristic dressed up as detection would get real
//     people ejected, so it says so instead.
//   • Mass join — measured, but only from presence join timestamps on THIS event.
import { useMemo, useState } from "react";
import {
  FiAlertTriangle, FiBell, FiShield, FiUserX, FiZap, FiCopy, FiLink, FiSlash, FiInfo,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "./Panel";
import useInterval from "../../hooks/useInterval";
import { hhmm, ALERT_TONE, FLAG_LABELS } from "../../data/moderation";

// Joins inside this window are what "mass join" means here.
const JOIN_WINDOW_SECONDS = 60;
const MASS_JOIN_THRESHOLD = 10;
// A poster this far above the room's average is flooding, not chatting.
const FLOOD_MESSAGES = 8;
const FLOOD_WINDOW_MS = 60_000;

const FLAG_ICON = {
  profanity: FiSlash, spam: FiZap, link: FiLink, duplicate: FiCopy, reported: FiUserX,
};

export default function AlertsPanel({
  alerts = [], messages = [], participants = [], canModerate, send, className,
}) {
  const [dismissed, setDismissed] = useState(() => new Set());
  // The flood and mass-join windows are relative to NOW, so this panel needs a clock rather than
  // a Date.now() inside the memo (which would only advance when a message arrived — and is an
  // impure read on the render path). Ticks slowly: these are one-minute windows.
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), 5000, true);

  const shown = alerts.filter((a) => !dismissed.has(a.id));

  const abuse = useMemo(() => {
    const live = messages.filter((m) => m.status !== "deleted");

    // Per-flag totals, straight off what the server already detected (flag_text) plus the
    // "reported" flag a viewer adds. No re-scanning of text in the browser.
    const byFlag = {};
    for (const m of live) for (const f of m.flags || []) byFlag[f] = (byFlag[f] || 0) + 1;

    // Flooding: messages per author inside the last minute. Uses created_at, so a message
    // without a timestamp (never persisted) simply doesn't count toward a flood.
    const recent = new Map();
    const offenders = new Map();
    for (const m of live) {
      const t = m.created_at ? new Date(m.created_at).getTime() : 0;
      if (t && now - t <= FLOOD_WINDOW_MS) recent.set(m.name, (recent.get(m.name) || 0) + 1);
      if ((m.flags || []).length) {
        const prior = offenders.get(m.name) || { name: m.name, user_id: m.user_id, count: 0 };
        offenders.set(m.name, { ...prior, count: prior.count + 1 });
      }
    }
    const flooding = [...recent.entries()]
      .filter(([, n]) => n >= FLOOD_MESSAGES)
      .map(([name, n]) => ({ name, count: n }))
      .sort((a, b) => b.count - a.count);

    // Mass join, from real presence join timestamps (epoch seconds, Redis-friendly).
    const cutoff = now / 1000 - JOIN_WINDOW_SECONDS;
    const joins = participants.filter((p) => (p.joined_at || 0) >= cutoff).length;

    return {
      byFlag,
      pending: live.filter((m) => m.status === "pending").length,
      flooding,
      joins,
      massJoin: joins >= MASS_JOIN_THRESHOLD,
      offenders: [...offenders.values()].sort((a, b) => b.count - a.count).slice(0, 5),
      chatMuted: participants.filter((p) => p.chat_muted).length,
    };
  }, [messages, participants, now]);

  return (
    <Panel
      title="Alerts"
      count={shown.length}
      badge={abuse.pending > 0 && (
        <Badge tone="warning" size="sm" dot>{abuse.pending} to review</Badge>
      )}
      className={className}
      action={shown.length > 0 && (
        <ActionButton
          icon={FiBell}
          label="Clear"
          title="Dismiss all alerts"
          onClick={() => setDismissed(new Set(alerts.map((a) => a.id)))}
        />
      )}
    >
      <div className="space-y-4">
        {/* ── the alert feed ─────────────────────────────────────────────────── */}
        {shown.length === 0 ? (
          <EmptyState
            icon={FiShield}
            title="All clear"
            description="Broadcast, recording, network and abuse alerts land here the moment they happen."
            className="py-8"
          />
        ) : (
          <ol className="space-y-1.5">
            {shown.map((a) => {
              const tone = ALERT_TONE[a.tone] || ALERT_TONE.info;
              return (
                <li
                  key={a.id}
                  className={cx("flex items-start gap-2 rounded-xl border px-2.5 py-2 motion-safe:animate-[zk-fade-in_.25s]", tone.box)}
                >
                  <FiAlertTriangle className={cx("mt-0.5 shrink-0", tone.icon)} aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm text-slate-700 dark:text-slate-200">{a.text}</p>
                    <p className="text-[11px] text-slate-400">{hhmm(new Date(a.at).toISOString())}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDismissed((prev) => new Set(prev).add(a.id))}
                    aria-label="Dismiss alert"
                    className="shrink-0 rounded text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
                  >
                    ✕
                  </button>
                </li>
              );
            })}
          </ol>
        )}

        {/* ── abuse summary ──────────────────────────────────────────────────── */}
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Abuse detection
          </p>
          <div className="grid grid-cols-2 gap-1.5">
            {Object.entries(FLAG_ICON).map(([flag, Icon]) => (
              <div key={flag} className="flex items-center gap-2 rounded-lg border border-slate-100 px-2 py-1.5 dark:border-slate-800">
                <Icon className={cx("shrink-0 text-sm", abuse.byFlag[flag] ? "text-rose-500" : "text-slate-300 dark:text-slate-600")} aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate text-xs text-slate-600 dark:text-slate-300">
                  {FLAG_LABELS[flag] || flag}
                </span>
                <span className="shrink-0 text-xs font-semibold tabular-nums text-slate-800 dark:text-slate-100">
                  {abuse.byFlag[flag] || 0}
                </span>
              </div>
            ))}
          </div>

          <div className="mt-1.5 space-y-1.5">
            {abuse.flooding.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-xs dark:border-amber-500/30 dark:bg-amber-500/10">
                <p className="font-semibold text-amber-800 dark:text-amber-300">Flooding chat</p>
                {abuse.flooding.map((f) => (
                  <p key={f.name} className="text-amber-700 dark:text-amber-400">
                    {f.name} — {f.count} messages in the last minute
                  </p>
                ))}
              </div>
            )}

            <div className={cx(
              "rounded-lg border px-2.5 py-1.5 text-xs",
              abuse.massJoin
                ? "border-amber-200 bg-amber-50/60 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
                : "border-slate-100 text-slate-500 dark:border-slate-800 dark:text-slate-400"
            )}>
              <span className="font-semibold">Join rate</span> — {abuse.joins} in the last minute
              {abuse.massJoin && " · unusual, check for a coordinated join"}
            </div>

            {abuse.chatMuted > 0 && (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                {abuse.chatMuted} participant{abuse.chatMuted === 1 ? " is" : "s are"} muted in chat.
              </p>
            )}

            {/* Said out loud rather than shipped as a guess. */}
            <p className="flex items-start gap-1.5 text-[11px] text-slate-400">
              <FiInfo className="mt-0.5 shrink-0" aria-hidden="true" />
              Fake-account detection needs signals this platform doesn't collect (device
              fingerprint, email reputation, IP history), so it isn't reported.
            </p>
          </div>
        </div>

        {/* ── repeat offenders ───────────────────────────────────────────────── */}
        {abuse.offenders.length > 0 && (
          <div>
            <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Most flagged
            </p>
            <div className="space-y-1">
              {abuse.offenders.map((o) => (
                <div key={o.name} className="flex items-center gap-2 rounded-lg border border-slate-100 px-2 py-1.5 dark:border-slate-800">
                  <span className="min-w-0 flex-1 truncate text-xs text-slate-700 dark:text-slate-200">{o.name}</span>
                  <Badge tone="danger" size="sm">{o.count} flagged</Badge>
                  {canModerate && o.user_id && (
                    <ActionButton
                      icon={FiSlash}
                      tone="rose"
                      title={`Mute ${o.name} in chat for 10 minutes`}
                      onClick={() => send("chat.mute", { identity: o.user_id, muted: true, minutes: 10 })}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
