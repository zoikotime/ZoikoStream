// client/src/components/host/HostPanel.jsx
// Right sidebar for the Producer console. Tabs: People, Chat, Q&A, Polls, Analytics, Activity.
//
// This file deliberately contains almost no panel implementation. Chat, Q&A, polls,
// announcements, the activity feed and the participant roster already exist as live
// components in components/moderation/* — the host console composes the SAME components as
// tabs rather than a column. The only thing genuinely new to the host is the analytics tab
// and the waiting-room queue, so that is all that's written here.
import { useState } from "react";
import {
  FiUsers, FiMessageSquare, FiHelpCircle, FiBarChart2, FiTrendingUp, FiActivity,
  FiCheck, FiX, FiMicOff, FiClock, FiSmartphone, FiMonitor, FiGlobe,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import ParticipantsPanel from "../moderation/ParticipantsPanel";
import { ChatTab, QATab } from "../moderation/ChatQAPanel";
import { PollManagement, Announcements, ActivityFeed } from "../moderation/ModeratorSidebar";
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
    <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50/70 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-amber-800 dark:text-amber-300">
          <FiClock aria-hidden="true" /> Waiting room · {waiting.length}
        </p>
        {canModerate && (
          <button
            onClick={() => send("stage.admit_all", {})}
            className="rounded-lg bg-amber-600 px-2 py-1 text-[11px] font-semibold text-white transition hover:bg-amber-500"
          >
            Admit all
          </button>
        )}
      </div>
      <div className="space-y-1">
        {waiting.map((p) => (
          <div key={p.identity} className="flex items-center gap-2 motion-safe:animate-[zk-fade-in_.25s]">
            <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-full text-[11px] font-semibold", ACCENT[accentFor(p.identity)].chip)}>
              {initials(p.name)}
            </span>
            <span className="min-w-0 flex-1 truncate text-sm text-slate-700 dark:text-slate-200">{p.name || p.identity}</span>
            {canModerate && (
              <>
                <button
                  onClick={() => send("stage.admit", { identity: p.identity, admit: true })}
                  className="rounded-lg p-1 text-emerald-600 transition hover:bg-emerald-100 dark:text-emerald-400 dark:hover:bg-emerald-500/15"
                  aria-label={`Admit ${p.name}`}
                  title="Admit"
                >
                  <FiCheck />
                </button>
                <button
                  onClick={() => send("stage.admit", { identity: p.identity, admit: false })}
                  className="rounded-lg p-1 text-rose-600 transition hover:bg-rose-100 dark:text-rose-400 dark:hover:bg-rose-500/15"
                  aria-label={`Deny ${p.name}`}
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

function Stat({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-200 p-2.5 dark:border-slate-800">
      <p className="truncate text-[11px] text-slate-500 dark:text-slate-400" title={hint}>{label}</p>
      <p className="text-lg font-semibold tabular-nums text-slate-900 dark:text-white">{value}</p>
    </div>
  );
}

// Retention sparkline. Inline SVG rather than a chart library: it's a single polyline over
// the server's analytics samples, and recharts is already loaded elsewhere for real charts.
function Retention({ points }) {
  if (!points?.length) {
    return <p className="py-6 text-center text-xs text-slate-400">Collecting samples… the graph fills in every 15s.</p>;
  }
  const values = points.map((p) => p.viewers);
  const max = Math.max(...values, 1);
  const step = points.length > 1 ? 100 / (points.length - 1) : 0;
  const path = values.map((v, i) => `${i * step},${40 - (v / max) * 36}`).join(" ");
  return (
    <div>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-20 w-full" role="img"
           aria-label={`Viewer retention, peaking at ${max}`}>
        <polyline points={`0,40 ${path} 100,40`} fill="rgb(16 185 129 / 0.15)" stroke="none" />
        <polyline points={path} fill="none" stroke="rgb(16 185 129)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between text-[10px] text-slate-400">
        <span>{points.length} samples</span>
        <span>peak {max.toLocaleString()}</span>
      </div>
    </div>
  );
}

function Distribution({ title, icon: Icon, rows, note }) {
  const total = (rows || []).reduce((s, r) => s + r.value, 0);
  return (
    <div>
      <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        <Icon aria-hidden="true" /> {title}
      </p>
      {!rows?.length ? (
        <p className="text-xs text-slate-400">{note || "No data yet."}</p>
      ) : (
        <div className="space-y-1">
          {rows.map((r) => (
            <div key={r.label} className="relative overflow-hidden rounded-lg border border-slate-200 px-2 py-1 dark:border-slate-700">
              <div className="absolute inset-y-0 left-0 bg-violet-500/15 transition-[width] duration-500"
                   style={{ width: `${total ? (r.value / total) * 100 : 0}%` }} />
              <div className="relative flex items-center justify-between text-xs">
                <span className="text-slate-700 dark:text-slate-200">{r.label}</span>
                <span className="tabular-nums text-slate-500 dark:text-slate-400">{r.value}</span>
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
    return <EmptyState icon={FiTrendingUp} title="No analytics yet" description="Numbers appear once the broadcast has an audience." className="py-10" />;
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Live viewers" value={(a.viewers ?? 0).toLocaleString()} />
        <Stat label="Peak viewers" value={(a.peak_viewers ?? 0).toLocaleString()} />
        <Stat label="Concurrent users" value={(a.participants ?? 0).toLocaleString()} hint="Everyone connected, including staff" />
        <Stat label="Avg watch time" value={fmtDuration(a.avg_watch_seconds)} hint="Mean time in room of everyone currently connected" />
        <Stat label="Engagement" value={`${a.engagement ?? 0}/100`} hint="Weighted interactions per viewer — a heuristic, see services/broadcast.engagement_score" />
        <Stat label="Chat rate" value={`${a.chat_per_minute ?? 0}/min`} hint="Messages in the last minute" />
        <Stat label="Questions" value={(a.questions_asked ?? 0).toLocaleString()} />
        <Stat label="Reactions" value={(a.reactions ?? 0).toLocaleString()} />
        <Stat label="Poll votes" value={(a.poll_votes ?? 0).toLocaleString()} />
        <Stat
          label="Poll participation"
          value={a.poll_participation == null ? "—" : `${a.poll_participation}%`}
          hint="Votes as a share of peak viewers"
        />
      </div>

      {health?.issues?.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-2.5 dark:border-amber-500/30 dark:bg-amber-500/10">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">Health</p>
          <ul className="mt-1 space-y-0.5 text-xs text-amber-800 dark:text-amber-300">
            {health.issues.map((i) => <li key={i}>• {i}</li>)}
          </ul>
        </div>
      )}

      <div>
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Retention</p>
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
      <div role="tablist" className="flex shrink-0 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
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
                ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            )}
          >
            <t.icon className="text-base" aria-hidden="true" />
            <span className="hidden sm:inline lg:hidden xl:inline">{t.label}</span>
            {pending[t.key] > 0 && (
              <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-500/15 dark:text-amber-400">
                {pending[t.key]}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {tab === "participants" && (
          <div className="flex min-h-0 flex-1 flex-col p-3">
            <WaitingRoom waiting={waiting} canModerate={canModerate} send={send} />
            {canModerate && participants.length > 1 && (
              <button
                onClick={() => (muteArmed ? (setMuteArmed(false), send("stage.mute_all", {})) : setMuteArmed(true))}
                className={cx(
                  "mb-2 inline-flex items-center justify-center gap-1.5 rounded-lg border px-2 py-1.5 text-xs font-medium transition",
                  muteArmed
                    ? "border-rose-300 bg-rose-50 text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-300"
                    : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                )}
              >
                <FiMicOff aria-hidden="true" /> {muteArmed ? "Confirm — mute everyone?" : "Mute all except staff"}
              </button>
            )}
            {/* The moderator console's roster, unchanged — search, filters, sort, profile
                drawer and the full action set all come for free. */}
            <ParticipantsPanel
              className="min-h-0 flex-1 !rounded-xl"
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
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
            <PollManagement polls={polls} canModerate={canModerate} send={send} />
            <Announcements announcements={announcements} canModerate={canModerate} send={send} />
          </div>
        )}

        {tab === "analytics" && (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <AnalyticsTab analytics={analytics} health={health} />
          </div>
        )}

        {tab === "activity" && (
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <ActivityFeed activity={activity} />
          </div>
        )}
      </div>

      {!canModerate && (
        <p className="shrink-0 border-t border-slate-100 px-3 py-2 text-center text-[11px] text-slate-400 dark:border-slate-800">
          <Badge tone="warning" size="sm" dot>Read-only</Badge> You aren't assigned to run this event.
        </p>
      )}
    </aside>
  );
}
