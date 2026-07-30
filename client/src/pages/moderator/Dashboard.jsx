// client/src/pages/moderator/Dashboard.jsx
// Moderator Console — real-time audience management during a live event.
// Route: /moderator/dashboard (optionally ?event=<id>). Standalone page, NOT the Org Dashboard.
//
// All live state comes from hooks/useLiveEvent (one WebSocket, full snapshot on connect,
// deltas after) — the same hook the Host console uses, so the reducer exists once.
import { useMemo } from "react";
import { FiRadio } from "react-icons/fi";
import useLiveEvent from "../../hooks/useLiveEvent";
import Skeleton from "../../ui/Skeleton";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import ModeratorHeader from "../../components/moderation/ModeratorHeader";
import SummaryCards from "../../components/moderation/SummaryCards";
import ParticipantsPanel from "../../components/moderation/ParticipantsPanel";
import ChatQAPanel from "../../components/moderation/ChatQAPanel";
import ModeratorSidebar from "../../components/moderation/ModeratorSidebar";

export default function ModeratorDashboard() {
  const { state, resolved, loading, error, status, latency, attempt, send } = useLiveEvent();
  const { participants, messages, questions, polls, announcements, activity } = state;

  const visibleMessages = useMemo(() => messages.filter((m) => m.status !== "deleted"), [messages]);
  const viewers = useMemo(
    () => participants.filter((p) => (p.role || "viewer") === "viewer").length,
    [participants]
  );

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
              : "The console attaches to your organization's live event. Start the event, or open this page with ?event=<id>."
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
        participants={participants.length}
        connection={status}
        latency={latency}
        attempt={attempt}
        canModerate={state.canModerate}
        enforced={state.livekitEnforced}
      />

      <div className="flex flex-1 flex-col gap-4 p-4 sm:p-6 lg:min-h-0">
        <SummaryCards
          participants={participants.length}
          viewers={viewers}
          questions={questions.length}
          polls={polls.length}
          messages={visibleMessages.length}
        />

        <div className="flex flex-1 flex-col gap-4 lg:min-h-0 lg:flex-row">
          <ParticipantsPanel
            className="min-h-[420px] lg:min-h-0 lg:w-[300px] lg:shrink-0"
            participants={participants}
            canModerate={state.canModerate}
            loading={!state.ready}
            send={send}
          />
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
          <ModeratorSidebar
            className="min-h-[520px] lg:min-h-0 lg:w-[360px] lg:shrink-0"
            polls={polls}
            announcements={announcements}
            activity={activity}
            canModerate={state.canModerate}
            loading={!state.ready}
            send={send}
          />
        </div>
      </div>

      {status === "unauthorized" && (
        <p role="alert" className="border-t border-rose-200 bg-rose-50 px-6 py-2 text-center text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          You're not signed in as a moderator of this event, so the console is read-only.
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
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
      </div>
      <div className="flex flex-1 flex-col gap-4 lg:flex-row">
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[300px]" />
        <Skeleton variant="block" className="h-full min-h-[420px] lg:flex-1" />
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[360px]" />
      </div>
    </div>
  );
}
