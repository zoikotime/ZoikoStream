// client/src/pages/moderator/Console.jsx
// Moderation Center — the live console. Route: /moderator/live?event=<id>.
//
// All live state comes from hooks/useLiveEvent (one WebSocket, full snapshot on connect, deltas
// after) — the same hook and reducer the host control room uses, so nothing about chat, Q&A,
// polls, participants or analytics is implemented twice. What differs between the two consoles
// is the LAYOUT and which actions are offered; the host gets a video stage and broadcast deck,
// the moderator gets the lobby, the hand queue and the alert center.
//
// Three columns, each with tabs, because nine panels do not fit side by side and a moderator
// works two of them at a time (the queue on the left, chat in the middle).
import { useMemo, useState } from "react";
import { FiRadio, FiUsers, FiUserCheck, FiMic, FiBarChart2, FiTrendingUp, FiBell, FiActivity, FiSliders } from "react-icons/fi";
import useLiveEvent from "../../hooks/useLiveEvent";
import Skeleton from "../../ui/Skeleton";
import Badge from "../../ui/Badge";
import { cx } from "../../ui/tokens";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import ModeratorHeader from "../../components/moderation/ModeratorHeader";
import SummaryCards from "../../components/moderation/SummaryCards";
import ParticipantsPanel from "../../components/moderation/ParticipantsPanel";
import LobbyPanel from "../../components/moderation/LobbyPanel";
import StagePanel from "../../components/moderation/StagePanel";
import ChatQAPanel from "../../components/moderation/ChatQAPanel";
import AlertsPanel from "../../components/moderation/AlertsPanel";
import AnalyticsPanel from "../../components/moderation/AnalyticsPanel";
import AudienceControls from "../../components/moderation/AudienceControls";
import { PollManagement, Announcements, ActivityFeed } from "../../components/moderation/ModeratorSidebar";

// Left column: the people work. Right column: the room work.
const LEFT_TABS = [
  { key: "people", label: "People", icon: FiUsers },
  { key: "lobby", label: "Lobby", icon: FiUserCheck },
  { key: "stage", label: "Stage", icon: FiMic },
];
const RIGHT_TABS = [
  { key: "polls", label: "Polls", icon: FiBarChart2 },
  { key: "alerts", label: "Alerts", icon: FiBell },
  { key: "analytics", label: "Stats", icon: FiTrendingUp },
  { key: "controls", label: "Room", icon: FiSliders },
  { key: "activity", label: "Feed", icon: FiActivity },
];

function Tabs({ tabs, active, onChange, pending = {} }) {
  return (
    <div role="tablist" className="flex shrink-0 overflow-x-auto rounded-t-2xl border border-b-0 border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={active === t.key}
          onClick={() => onChange(t.key)}
          title={t.label}
          className={cx(
            "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-2.5 text-xs font-medium transition",
            active === t.key
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
  );
}

export default function ModeratorConsole() {
  const { state, resolved, loading, error, status, latency, attempt, send } = useLiveEvent();
  const [left, setLeft] = useState("people");
  const [right, setRight] = useState("polls");

  const {
    participants, messages, questions, polls, announcements, activity, alerts,
  } = state;

  const visibleMessages = useMemo(() => messages.filter((m) => m.status !== "deleted"), [messages]);
  const waiting = useMemo(() => participants.filter((p) => p.waiting), [participants]);
  const active = useMemo(() => participants.filter((p) => !p.waiting), [participants]);
  const viewers = useMemo(
    () => active.filter((p) => (p.role || "viewer") === "viewer").length,
    [active]
  );
  const hands = useMemo(() => active.filter((p) => p.hand).length, [active]);

  const leftPending = {
    lobby: waiting.length,
    stage: hands,
    people: 0,
  };
  const rightPending = {
    alerts: alerts.length,
    polls: polls.filter((p) => p.status === "live").length,
  };

  if (loading) return <ConsoleSkeleton />;
  if (error || !resolved) {
    return (
      <ConsoleFrame>
        <EmptyState
          icon={FiRadio}
          title={error ? "Couldn't load your events" : "No live event right now"}
          description={
            error
              ? "The events API didn't respond. The console reconnects on its own once it's back."
              : "Open an event from your dashboard, or add ?event=<id> to this URL. The console attaches to your organization's live event when neither is given."
          }
        />
      </ConsoleFrame>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
      <ModeratorHeader
        event={state.event}
        viewers={viewers}
        participants={active.length}
        connection={status}
        latency={latency}
        attempt={attempt}
        canModerate={state.canModerate}
        enforced={state.livekitEnforced}
      />

      <div className="flex flex-1 flex-col gap-4 p-4 sm:p-6 lg:min-h-0">
        <SummaryCards
          participants={active.length}
          viewers={viewers}
          questions={questions.length}
          polls={polls.length}
          messages={visibleMessages.length}
          waiting={waiting.length}
          hands={hands}
        />

        <div className="flex flex-1 flex-col gap-4 lg:min-h-0 lg:flex-row">
          {/* ── left: people / lobby / stage ──────────────────────────────────── */}
          <div className="flex min-h-[480px] flex-col lg:min-h-0 lg:w-[330px] lg:shrink-0">
            <Tabs tabs={LEFT_TABS} active={left} onChange={setLeft} pending={leftPending} />
            {left === "people" && (
              <ParticipantsPanel
                className="min-h-0 flex-1 !rounded-t-none"
                participants={active}
                canModerate={state.canModerate}
                loading={!state.ready}
                send={send}
              />
            )}
            {left === "lobby" && (
              <LobbyPanel
                className="min-h-0 flex-1 !rounded-t-none"
                waiting={waiting}
                canModerate={state.canModerate}
                loading={!state.ready}
                send={send}
              />
            )}
            {left === "stage" && (
              <StagePanel
                className="min-h-0 flex-1 !rounded-t-none"
                participants={participants}
                canModerate={state.canModerate}
                loading={!state.ready}
                send={send}
              />
            )}
          </div>

          {/* ── centre: chat + Q&A ───────────────────────────────────────────── */}
          <ChatQAPanel
            className="min-h-[520px] lg:min-h-0 lg:min-w-0 lg:flex-1"
            messages={visibleMessages}
            questions={questions}
            speakers={state.speakers}
            typing={state.typing}
            canModerate={state.canModerate}
            loading={!state.ready}
            send={send}
          />

          {/* ── right: polls / alerts / stats / room / feed ───────────────────── */}
          <div className="flex min-h-[520px] flex-col lg:min-h-0 lg:w-[370px] lg:shrink-0">
            <Tabs tabs={RIGHT_TABS} active={right} onChange={setRight} pending={rightPending} />
            <div className="min-h-0 flex-1 overflow-y-auto rounded-b-2xl border border-t-0 border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
              {right === "polls" && (
                <div className="space-y-4 p-3">
                  <PollManagement polls={polls} canModerate={state.canModerate} send={send} />
                  <Announcements announcements={announcements} canModerate={state.canModerate} send={send} />
                </div>
              )}
              {right === "alerts" && (
                <AlertsPanel
                  className="!rounded-none !border-0 !shadow-none"
                  alerts={alerts}
                  messages={visibleMessages}
                  participants={participants}
                  canModerate={state.canModerate}
                  send={send}
                />
              )}
              {right === "analytics" && (
                <div className="p-3">
                  <AnalyticsPanel analytics={state.analytics} health={state.health} variant="moderator" />
                </div>
              )}
              {right === "controls" && (
                <AudienceControls
                  className="!rounded-none !border-0 !shadow-none"
                  settings={state.broadcast?.settings}
                  canModerate={state.canModerate}
                  send={send}
                />
              )}
              {right === "activity" && (
                <div className="p-3">
                  <ActivityFeed activity={activity} />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* A moderator who isn't assigned to THIS event still loads the page (the route only
          checks the platform role) — the socket is what decides, and it says so here. */}
      {status === "open" && !state.canModerate && (
        <p role="status" className="border-t border-amber-200 bg-amber-50 px-6 py-2 text-center text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          <Badge tone="warning" size="sm" dot>Read-only</Badge>{" "}
          You aren't assigned to moderate this event, so actions are disabled.
        </p>
      )}
      {status === "unauthorized" && (
        <p role="alert" className="border-t border-rose-200 bg-rose-50 px-6 py-2 text-center text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          The server refused this connection. Check you're signed in and assigned to this event.
        </p>
      )}
    </div>
  );
}

// Shell reused by the loading + empty states so they keep the console's chrome.
function ConsoleFrame({ children }) {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          {children}
        </div>
      </div>
    </div>
  );
}

function ConsoleSkeleton() {
  return (
    <div className="flex min-h-screen flex-col gap-4 bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
      <Skeleton variant="title" className="w-full" />
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-7">
        {[0, 1, 2, 3, 4, 5, 6].map((i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
      </div>
      <div className="flex flex-1 flex-col gap-4 lg:flex-row">
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[330px]" />
        <Skeleton variant="block" className="h-full min-h-[420px] lg:flex-1" />
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[370px]" />
      </div>
    </div>
  );
}
