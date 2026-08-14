// client/src/components/host/HostPanel.jsx
// Right sidebar for the Producer console. Tabs: People, Chat, Q&A, Polls, Analytics, Activity.
//
// This file deliberately contains almost no panel implementation. Chat, Q&A, polls,
// announcements, the activity feed and the participant roster already exist as live
// components in components/moderation/* — the host console composes the SAME components as
// tabs rather than a column. The only thing genuinely new to the host is the analytics tab
// and the waiting-room queue, so that is all that's written here.
//
// SCOPE NOTE: components/moderation/* are shared with /moderator/dashboard, so this file
// restyles only the panel CHROME it owns (tab rail, waiting room, analytics). Restyling the
// roster or chat internals would have changed the moderator console too.
//
// The tab rail is a 6-column GRID, not a scrolling flex row. The old row overflowed at the
// 380px sidebar width and put a horizontal scrollbar between the operator and their tabs;
// six equal 1/6 columns with a stacked icon+label fit the same width with room to spare.
import { useState } from "react";
import {
  FiUsers, FiMessageSquare, FiHelpCircle, FiBarChart2, FiTrendingUp, FiActivity,
  FiCheck, FiX, FiMicOff, FiClock, FiSmartphone, FiMonitor, FiGlobe,
  FiEye, FiHeart,
} from "react-icons/fi";
import { cx, ACCENT, SERIES } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import ParticipantsPanel from "../moderation/ParticipantsPanel";
import { ChatTab, QATab } from "../moderation/ChatQAPanel";
import { PollManagement, Announcements, ActivityFeed } from "../moderation/ModeratorSidebar";
import { STUDIO, focus, t150 } from "./studio";
import { ALERT, ALERT_DOT } from "../moderation/panelTokens";
import { initials, accentFor } from "../../data/host";

const TABS = [
  { key: "participants", label: "People", icon: FiUsers },
  { key: "chat", label: "Chat", icon: FiMessageSquare },
  { key: "qa", label: "Q&A", icon: FiHelpCircle },
  { key: "polls", label: "Polls", icon: FiBarChart2 },
  { key: "analytics", label: "Stats", icon: FiTrendingUp },
  { key: "activity", label: "Feed", icon: FiActivity },
];

// ── waiting room ──────────────────────────────────────────────────────────────

function WaitingRoom({ waiting, canModerate, send }) {
  if (!waiting.length) return null;
  return (
    <div className="mb-2 rounded-lg border border-amber-300 bg-amber-50 p-2 dark:border-amber-500/30 dark:bg-amber-500/10">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
          <FiClock aria-hidden="true" /> Waiting room
          <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-bold tabular-nums text-white">
            {waiting.length}
          </span>
        </p>
        {canModerate && (
          <button
            type="button"
            onClick={() => send("stage.admit_all", {})}
            className={cx(
              "rounded-md bg-amber-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-amber-500",
              t150, focus
            )}
          >
            Admit all
          </button>
        )}
      </div>
      <div className="space-y-0.5">
        {waiting.map((p) => (
          <div
            key={p.identity}
            className="flex items-center gap-2 rounded-md px-1 py-0.5 motion-safe:animate-[zk-fade-in_.25s]"
          >
            <span
              className={cx(
                "grid h-6 w-6 shrink-0 place-items-center rounded-full text-[10px] font-semibold",
                ACCENT[accentFor(p.identity)].chip
              )}
            >
              {initials(p.name)}
            </span>
            <span className={cx("min-w-0 flex-1 truncate text-[13px]", STUDIO.body)}>
              {p.name || p.identity}
            </span>
            {canModerate && (
              <>
                <button
                  type="button"
                  onClick={() => send("stage.admit", { identity: p.identity, admit: true })}
                  className={cx(
                    "grid h-6 w-6 place-items-center rounded-md text-green-600 hover:bg-green-100 dark:text-green-400 dark:hover:bg-green-500/15",
                    t150, focus
                  )}
                  aria-label={`Admit ${p.name || p.identity}`}
                  title="Admit"
                >
                  <FiCheck />
                </button>
                <button
                  type="button"
                  onClick={() => send("stage.admit", { identity: p.identity, admit: false })}
                  className={cx(
                    "grid h-6 w-6 place-items-center rounded-md text-rose-600 hover:bg-rose-100 dark:text-rose-400 dark:hover:bg-rose-500/15",
                    t150, focus
                  )}
                  aria-label={`Deny entry to ${p.name || p.identity}`}
                  title="Deny entry"
                >
                  <FiX />
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── analytics ─────────────────────────────────────────────────────────────────

const fmtDuration = (s) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : m >= 1 ? `${m}m ${s % 60}s` : `${s}s`;
};

// One analytics tile: uppercase label, the value as the loudest thing in the tile, then a
// supporting line that says what the number is OF. `hint` stays the tooltip — it carries the
// honest explanation of how the figure is derived (see services/broadcast.py).
function Stat({ label, value, sub: subtitle, icon: Icon, hint }) {
  return (
    <div className={cx("px-2 py-1.5", STUDIO.inset)} title={hint}>
      <div className="flex items-start justify-between gap-1">
        <span className={cx("min-w-0 truncate text-[10px] font-medium uppercase tracking-wide", STUDIO.faint)}>
          {label}
        </span>
        {Icon && <Icon aria-hidden="true" className={cx("mt-px shrink-0 text-[11px]", STUDIO.faint)} />}
      </div>
      <p className={cx("mt-0.5 text-[15px] font-semibold leading-none tabular-nums", STUDIO.heading)}>
        {value}
      </p>
      {subtitle && (
        <span className={cx("mt-0.5 block truncate text-[10px] leading-none", STUDIO.faint)}>{subtitle}</span>
      )}
    </div>
  );
}

// Section heading inside the analytics tab — one shape for all of them.
function SectionLabel({ icon: Icon, children }) {
  return (
    <p className={cx("mb-1.5 flex items-center gap-1.5", STUDIO.eyebrow, STUDIO.faint)}>
      {Icon && <Icon aria-hidden="true" />} {children}
    </p>
  );
}

// Retention sparkline. Inline SVG rather than a chart library: it's a single polyline over
// the server's analytics samples, and recharts is already loaded elsewhere for real charts.
// Uses the shared brand series colour so it matches every other chart in the app.
function Retention({ points }) {
  if (!points?.length) {
    return (
      <p className={cx("py-5 text-center text-[11px]", STUDIO.faint)}>
        Collecting samples… the graph fills in every 15s.
      </p>
    );
  }
  const values = points.map((p) => p.viewers);
  const max = Math.max(...values, 1);
  const step = points.length > 1 ? 100 / (points.length - 1) : 0;
  const path = values.map((v, i) => `${i * step},${40 - (v / max) * 36}`).join(" ");
  return (
    <div className={cx("p-2", STUDIO.inset)}>
      <svg
        viewBox="0 0 100 40"
        preserveAspectRatio="none"
        className="h-16 w-full"
        role="img"
        aria-label={`Viewer retention over ${points.length} samples, peaking at ${max} viewers`}
      >
        {/* Three gridlines at 25/50/75% of peak plus a solid baseline, so the curve is read
            against something instead of floating. vectorEffect keeps every stroke hairline
            despite the non-uniform viewBox scaling. */}
        {[10, 20, 30].map((y) => (
          <line
            key={y} x1="0" y1={y} x2="100" y2={y}
            stroke="currentColor" strokeOpacity="0.12" strokeWidth="1"
            vectorEffect="non-scaling-stroke" className={STUDIO.faint}
          />
        ))}
        <polyline points={`0,40 ${path} 100,40`} fill={SERIES.brand} fillOpacity="0.14" stroke="none" />
        <polyline
          points={path}
          fill="none"
          stroke={SERIES.brand}
          strokeWidth="1.5"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        <line
          x1="0" y1="40" x2="100" y2="40"
          stroke="currentColor" strokeOpacity="0.3" strokeWidth="1"
          vectorEffect="non-scaling-stroke" className={STUDIO.faint}
        />
      </svg>
      <div className={cx("mt-1 flex items-center justify-between text-[10px] tabular-nums", STUDIO.faint)}>
        <span>{points.length} samples · every 15s</span>
        <span className="inline-flex items-center gap-1">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full" style={{ background: SERIES.brand }} />
          peak {max.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

function Distribution({ title, icon: Icon, rows, note }) {
  const total = (rows || []).reduce((s, r) => s + r.value, 0);
  return (
    <div>
      <SectionLabel icon={Icon}>{title}</SectionLabel>
      {!rows?.length ? (
        <p className={cx("text-[11px]", STUDIO.faint)}>{note || "No data yet."}</p>
      ) : (
        <div className="space-y-1">
          {rows.map((r) => (
            <div
              key={r.label}
              className={cx("relative overflow-hidden px-2 py-1", STUDIO.inset)}
              title={`${r.label}: ${r.value}${total ? ` of ${total}` : ""}`}
            >
              <div
                aria-hidden="true"
                className="absolute inset-y-0 left-0 bg-violet-500/20 transition-[width] duration-500 motion-reduce:transition-none"
                style={{ width: `${total ? (r.value / total) * 100 : 0}%` }}
              />
              <div className="relative flex items-center justify-between gap-2 text-[11px]">
                <span className={cx("truncate", STUDIO.body)}>{r.label}</span>
                <span className={cx("shrink-0 font-semibold tabular-nums", STUDIO.muted)}>{r.value}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AnalyticsTab({ analytics, health }) {
  const a = analytics;
  if (!a) {
    return (
      <EmptyState
        icon={FiTrendingUp}
        title="No analytics yet"
        description="Numbers appear once the broadcast has an audience."
        className="py-10"
      />
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-1.5">
        <Stat label="Live viewers" value={(a.viewers ?? 0).toLocaleString()} sub="Watching now" icon={FiEye} />
        <Stat label="Peak viewers" value={(a.peak_viewers ?? 0).toLocaleString()} sub="This broadcast" icon={FiTrendingUp} />
        <Stat label="Connected" value={(a.participants ?? 0).toLocaleString()} sub="Incl. staff" icon={FiUsers} hint="Everyone connected, including staff" />
        <Stat label="Avg watch" value={fmtDuration(a.avg_watch_seconds)} sub="Mean in room" icon={FiClock} hint="Mean time in room of everyone currently connected" />
        <Stat label="Engagement" value={`${a.engagement ?? 0}/100`} sub="Weighted index" icon={FiActivity} hint="Weighted interactions per viewer — a heuristic, see services/broadcast.engagement_score" />
        <Stat label="Chat rate" value={`${a.chat_per_minute ?? 0}/min`} sub="Last minute" icon={FiMessageSquare} hint="Messages in the last minute" />
        <Stat label="Questions" value={(a.questions_asked ?? 0).toLocaleString()} sub="Asked so far" icon={FiHelpCircle} />
        <Stat label="Reactions" value={(a.reactions ?? 0).toLocaleString()} sub="On messages" icon={FiHeart} />
        <Stat label="Poll votes" value={(a.poll_votes ?? 0).toLocaleString()} sub="Across all polls" icon={FiBarChart2} />
        <Stat
          label="Poll turnout"
          value={a.poll_participation == null ? "—" : `${a.poll_participation}%`}
          sub="Of peak viewers"
          icon={FiTrendingUp}
          hint="Votes as a share of peak viewers"
        />
      </div>

      {/* Colour comes from the server's own health level (services/broadcast.health_of),
          not from the issue count: "down" is red, "warn" amber, "ok" green. Each issue keeps
          a leading dot so severity is never carried by hue alone. */}
      {health?.issues?.length > 0 && (
        <div
          role="status"
          className={cx("rounded-lg border p-2", ALERT[health.level] || ALERT.warn)}
        >
          <p className="text-[10px] font-semibold uppercase tracking-wide">Broadcast health</p>
          <ul className="mt-1 space-y-1 text-[11px]">
            {health.issues.map((i) => (
              <li key={i} className="flex items-start gap-1.5">
                <span
                  aria-hidden="true"
                  className={cx("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", ALERT_DOT[health.level] || ALERT_DOT.warn)}
                />
                <span className="min-w-0">{i}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <SectionLabel>Retention</SectionLabel>
        <Retention points={a.retention} />
      </div>

      <Distribution title="Devices" icon={FiSmartphone} rows={a.devices} />
      <Distribution title="Platforms" icon={FiMonitor} rows={a.platforms} />
      <Distribution title="Browsers" icon={FiGlobe} rows={a.browsers} />
      {/* Stated, not faked: there's no GeoIP in this stack. */}
      <Distribution title="Countries" icon={FiGlobe} rows={a.countries} note={a.countries_note} />
    </div>
  );
}

// ── shell ─────────────────────────────────────────────────────────────────────

export default function HostPanel({ tab, setTab, state, canModerate, send, className = "" }) {
  const [muteArmed, setMuteArmed] = useState(false);
  const {
    participants = [], messages = [], questions = [], polls = [], announcements = [],
    activity = [], speakers = [], typing = {}, analytics, health, ready,
  } = state;

  const waiting = participants.filter((p) => p.waiting);
  const visibleMessages = messages.filter((m) => m.status !== "deleted");
  const pending = {
    chat: visibleMessages.filter((m) => m.status === "pending").length,
    qa: questions.filter((q) => q.status === "pending").length,
    participants: waiting.length,
  };

  return (
    <aside className={cx("flex flex-col bg-white dark:bg-slate-900", className)}>
      {/* Active tab: violet label + icon over a 2px violet underline. Two signals, so the
          state doesn't rest on colour alone, and subtle enough not to compete with the
          stage for attention. */}
      <div
        role="tablist"
        aria-label="Producer panels"
        className={cx("grid shrink-0 grid-cols-6 border-b", STUDIO.divider)}
      >
        {TABS.map((t) => {
          const on = tab === t.key;
          const count = pending[t.key] || 0;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setTab(t.key)}
              title={count > 0 ? `${t.label} — ${count} need attention` : t.label}
              className={cx(
                "group relative flex min-w-0 flex-col items-center justify-center gap-1 border-b-2 px-1 py-2",
                on
                  ? "border-violet-600 bg-violet-50/60 text-violet-700 dark:border-violet-400 dark:bg-violet-500/[0.09] dark:text-violet-300"
                  : cx("border-transparent", STUDIO.muted, "hover:bg-violet-50/70 hover:text-slate-900 dark:hover:bg-violet-500/[0.07] dark:hover:text-white"),
                t150,
                focus
              )}
            >
              <t.icon
                aria-hidden="true"
                className={cx(
                  "text-[15px] transition-transform duration-200 ease-out",
                  "group-hover:scale-110 motion-reduce:transition-none motion-reduce:group-hover:scale-100",
                  on && "scale-110 motion-reduce:scale-100"
                )}
              />
              <span className="max-w-full truncate text-[10px] font-semibold leading-none">
                {t.label}
              </span>
              {/* A count here means something is waiting on the operator, so it pulses until
                  the queue is cleared — an unmoving badge on a busy console gets missed. */}
              {count > 0 && (
                <span className="absolute right-0.5 top-0.5 min-w-[15px] animate-pulse rounded-full bg-amber-500 px-1 text-[9px] font-bold leading-[15px] tabular-nums text-white shadow-sm shadow-amber-500/40 motion-reduce:animate-none">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* @container: the tab bodies size their action labels off THIS rail's width
          (~380px) rather than the viewport — see the note in moderation/Panel.jsx. */}
      <div className="@container flex min-h-0 flex-1 flex-col overflow-hidden">
        {tab === "participants" && (
          <div className="flex min-h-0 flex-1 flex-col p-2.5">
            <WaitingRoom waiting={waiting} canModerate={canModerate} send={send} />
            {canModerate && participants.length > 1 && (
              <button
                type="button"
                onClick={() => (muteArmed ? (setMuteArmed(false), send("stage.mute_all", {})) : setMuteArmed(true))}
                className={cx(
                  "mb-2 inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border text-[12px] font-medium",
                  muteArmed
                    ? "border-rose-400 bg-rose-50 text-rose-700 dark:border-rose-500/50 dark:bg-rose-500/10 dark:text-rose-300"
                    : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800",
                  t150, focus
                )}
              >
                <FiMicOff aria-hidden="true" />
                {muteArmed ? "Confirm — mute everyone?" : "Mute all except staff"}
              </button>
            )}
            {/* The moderator console's roster, unchanged — search, filters, sort, profile
                drawer and the full action set all come for free. */}
            <ParticipantsPanel
              className="min-h-0 flex-1 !rounded-lg"
              participants={participants.filter((p) => !p.waiting)}
              canModerate={canModerate}
              loading={!ready}
              send={send}
            />
          </div>
        )}

        {tab === "chat" && (
          <ChatTab messages={visibleMessages} typing={typing} canModerate={canModerate} send={send} />
        )}

        {tab === "qa" && (
          <QATab questions={questions} speakers={speakers} canModerate={canModerate} send={send} />
        )}

        {tab === "polls" && (
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-2.5">
            <PollManagement polls={polls} canModerate={canModerate} send={send} />
            <Announcements announcements={announcements} canModerate={canModerate} send={send} />
          </div>
        )}

        {tab === "analytics" && (
          <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
            <AnalyticsTab analytics={analytics} health={health} />
          </div>
        )}

        {tab === "activity" && (
          <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
            <ActivityFeed activity={activity} />
          </div>
        )}
      </div>

      {!canModerate && (
        <p className={cx("flex shrink-0 items-center justify-center gap-1.5 border-t px-3 py-1.5 text-[11px]", STUDIO.divider, STUDIO.faint)}>
          <Badge tone="warning" size="sm" dot>Read-only</Badge>
          You aren&apos;t assigned to run this event.
        </p>
      )}
    </aside>
  );
}
