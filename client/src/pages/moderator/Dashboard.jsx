// client/src/pages/moderator/Dashboard.jsx
// The MODERATOR's landing page. Route: /moderator/dashboard (where roleHome sends them).
//
// Deliberately not the console — that is /moderator/live. Same split as the host, for the same
// reason: a moderator arriving at work needs to see which events they are responsible for and
// what is queued on each, not be dropped into whichever room happened to be live.
//
// Everything here is real. GET /events?assigned=me&assigned_role=moderator is the events this
// person was actually ASSIGNED to moderate (an org member can READ every event in the org,
// which is a different thing), and the queue columns — raised hands, lobby depth, open
// questions, running polls — come from crud.summarize on that same response. No new endpoint:
// hands and lobby ride on the newest analytics sample (so ~15s old, and null before an event
// has ever been live), the two counts are live rows.
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiActivity, FiAlertTriangle, FiBarChart2, FiCalendar, FiCheckCircle, FiClock,
  FiHelpCircle, FiRadio, FiShield, FiUserCheck, FiUsers,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { useAuth } from "../../auth/AuthContext";
import Skeleton from "../../ui/Skeleton";
import Badge from "../../ui/Badge";
import { ConsoleButton as Button } from "../../ui/Button";
import SectionCard from "../../components/admin/SectionCard";
import StatCard from "../../components/admin/StatCard";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import ErrorState from "../../components/organization/OrganizationErrorState";
import EventStatusBadge from "../../components/organization/EventStatusBadge";
import { cx, focusRing, type } from "../../ui/tokens";
import { fmtDateTime, isOnAir } from "../../data/events";

const REFRESH_MS = 30_000;

const sameDay = (iso) => {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
};

const ACT_TONE = {
  recording: "text-rose-500", system: "text-violet-500", join: "text-emerald-500",
  leave: "text-slate-400", mod: "text-amber-500", role: "text-blue-500",
  chat: "text-blue-500", qa: "text-violet-500", poll: "text-amber-500",
};

/** One queue figure. Renders "—" rather than 0 when the server sent null, because "no lobby
 *  has ever been measured on this event" and "the lobby is empty" are different facts. */
function Queue({ icon: Icon, value, label }) {
  const has = value != null && value > 0;
  return (
    <span
      title={label}
      className={cx(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium tabular-nums",
        has
          ? "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
          : "text-slate-400"
      )}
    >
      <Icon aria-hidden="true" /> {value == null ? "—" : value}
    </span>
  );
}

function EventRow({ event, onOpen }) {
  const onAir = isOnAir(event.status);
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/moderator/live?event=${event.id}`}
            className={cx("truncate font-medium text-slate-800 hover:text-emerald-600 dark:text-slate-100 dark:hover:text-emerald-400", focusRing)}
          >
            {event.title || "Untitled event"}
          </Link>
          <EventStatusBadge status={event.status} size="sm" />
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
          {fmtDateTime(event.start_time)}
          {event.current_viewers != null && ` · ${event.current_viewers} watching`}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <Queue icon={FiUserCheck} value={event.waiting} label="Waiting in the lobby" />
          <Queue icon={FiUsers} value={event.raised_hands} label="Raised hands" />
          <Queue icon={FiHelpCircle} value={event.open_questions} label="Questions awaiting review" />
          <Queue icon={FiBarChart2} value={event.live_polls} label="Polls running" />
        </div>
      </div>
      <Button
        size="sm"
        variant={onAir ? "primary" : "secondary"}
        leftIcon={onAir ? FiRadio : FiShield}
        onClick={() => onOpen(event)}
      >
        {onAir ? "Join moderation" : "Open console"}
      </Button>
    </li>
  );
}

// The buckets a moderator cares about. No drafts: a draft has no audience to moderate, so
// showing one here would be a row they can do nothing with.
const BUCKETS = [
  { key: "live", label: "On air", icon: FiRadio, match: (e) => isOnAir(e.status) },
  { key: "today", label: "Today", icon: FiClock,
    match: (e) => sameDay(e.start_time) && !isOnAir(e.status) },
  { key: "upcoming", label: "Upcoming", icon: FiCalendar,
    match: (e) => ["published", "scheduled"].includes(e.status) && !sameDay(e.start_time) },
  { key: "completed", label: "Completed", icon: FiCheckCircle,
    match: (e) => ["ended", "archived"].includes(e.status) },
];

export default function ModeratorDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

  // One page_size=100 read rather than four status-filtered requests: an assigned list is small
  // by nature and bucketing in the browser beats four round trips.
  //
  // assigned_role=moderator is the point of this page. An org's host is also allowed in here
  // (they may moderate), and without the role filter they would see every event they host as
  // something to moderate.
  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get("/events", {
        params: {
          assigned: "me", assigned_role: ["moderator", "host"],
          page_size: 100, sort_by: "start_time", order: "asc",
        },
      }).then((r) => r.data),
      api.get("/events/activity", { params: { assigned: "me", limit: 25 } }).then((r) => r.data),
    ]).then(([events, activity]) => ({ events, activity }))
  );

  useInterval(reload, REFRESH_MS, !loading && !error);

  const events = useMemo(() => data?.events?.items || [], [data]);
  const activity = useMemo(() => data?.activity || [], [data]);

  const buckets = useMemo(
    () => BUCKETS.map((b) => ({ ...b, items: events.filter(b.match) })),
    [events]
  );
  const onAir = buckets.find((b) => b.key === "live")?.items || [];

  // Totals across the events this moderator is on air for — the "is anything waiting for me
  // right now" read. Only live events contribute a lobby/hand figure, so summing the rest
  // would inflate it with stale samples.
  const queue = useMemo(() => {
    const live = events.filter((e) => isOnAir(e.status));
    const sum = (key, from) => from.reduce((n, e) => n + (e[key] || 0), 0);
    return {
      waiting: sum("waiting", live),
      hands: sum("raised_hands", live),
      questions: sum("open_questions", events),
      polls: sum("live_polls", live),
    };
  }, [events]);

  // Notifications, derived from data we hold rather than a table that doesn't exist. Each row
  // is something the moderator can act on by opening the console.
  const alerts = useMemo(() => {
    const out = [];
    for (const e of events) {
      if (!isOnAir(e.status)) continue;
      if (e.waiting > 0) {
        out.push({ id: `lobby-${e.id}`, tone: "warning", event: e,
                   text: `${e.waiting} waiting in the lobby.` });
      }
      if (e.raised_hands > 0) {
        out.push({ id: `hands-${e.id}`, tone: "warning", event: e,
                   text: `${e.raised_hands} raised hand${e.raised_hands === 1 ? "" : "s"} unanswered.` });
      }
      if (e.open_questions > 0) {
        out.push({ id: `qa-${e.id}`, tone: "info", event: e,
                   text: `${e.open_questions} question${e.open_questions === 1 ? "" : "s"} awaiting review.` });
      }
    }
    return out.slice(0, 6);
  }, [events]);

  const open = (event) => navigate(`/moderator/live?event=${event.id}`);

  if (loading) {
    return (
      <div className="min-h-screen space-y-4 bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
        <Skeleton variant="title" className="w-72" />
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="block" className="h-24" />)}
        </div>
        <Skeleton variant="block" className="h-64" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
      <div className="mx-auto max-w-[1400px] space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className={cx(type.h1, "truncate text-slate-900 dark:text-white")}>
              {user?.full_name ? `Welcome, ${user.full_name.split(" ")[0]}` : "Moderator dashboard"}
            </h1>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
              The events you're assigned to moderate
            </p>
          </div>
          {onAir.length > 0 && (
            <Button leftIcon={FiRadio} onClick={() => open(onAir[0])}>
              Join {onAir[0].title || "the live event"}
            </Button>
          )}
        </div>

        {error ? (
          <ErrorState error={error} onRetry={reload} title="Couldn't load your events" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              {buckets.map((b) => (
                <StatCard key={b.key} label={b.label} value={b.items.length} />
              ))}
            </div>

            {/* The moderation queue across everything on air. Figures are ~15s old by
                construction (the analytics sampler's cadence) — the console is live. */}
            <SectionCard
              title="Your queue"
              subtitle={onAir.length ? "Across the events you're moderating right now" : "Nothing on air"}
              icon={FiShield}
            >
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {[
                  ["In the lobby", queue.waiting, FiUserCheck],
                  ["Raised hands", queue.hands, FiUsers],
                  ["Questions to review", queue.questions, FiHelpCircle],
                  ["Polls running", queue.polls, FiBarChart2],
                ].map(([label, value, Icon]) => (
                  <div key={label} className="flex items-center gap-2.5">
                    <Icon className={cx("shrink-0 text-lg", value > 0 ? "text-amber-500" : "text-slate-300 dark:text-slate-600")} aria-hidden="true" />
                    <div className="min-w-0">
                      <p className="text-xl font-semibold tabular-nums text-slate-900 dark:text-white">{value}</p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">{label}</p>
                    </div>
                  </div>
                ))}
              </div>
            </SectionCard>

            {alerts.length > 0 && (
              <SectionCard title="Needs your attention" icon={FiAlertTriangle} accent="amber">
                <ul className="space-y-2">
                  {alerts.map((a) => (
                    <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge tone={a.tone === "warning" ? "warning" : "info"} size="sm" dot>
                        {a.event.title || "Untitled event"}
                      </Badge>
                      <span className="text-slate-600 dark:text-slate-300">{a.text}</span>
                      <button
                        onClick={() => open(a.event)}
                        className={cx("ml-auto rounded text-xs font-semibold text-emerald-600 hover:underline dark:text-emerald-400", focusRing)}
                      >
                        Open
                      </button>
                    </li>
                  ))}
                </ul>
              </SectionCard>
            )}

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="space-y-5">
                {buckets.filter((b) => b.items.length > 0).map((b) => (
                  <SectionCard
                    key={b.key}
                    title={b.label}
                    subtitle={`${b.items.length} event${b.items.length === 1 ? "" : "s"}`}
                    icon={b.icon}
                    padding="none"
                  >
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                      {b.items.slice(0, 8).map((e) => <EventRow key={e.id} event={e} onOpen={open} />)}
                    </ul>
                  </SectionCard>
                ))}

                {events.length === 0 && (
                  <SectionCard title="Your events" icon={FiShield} padding="none">
                    <EmptyState
                      icon={FiUsers}
                      title="You're not assigned to any events yet"
                      description="An organization admin assigns moderators to events. Once you're assigned, the event appears here and you can open its moderation console."
                    />
                  </SectionCard>
                )}
              </div>

              <SectionCard title="Recent activity" subtitle="Across your events" icon={FiActivity} padding="none">
                {activity.length === 0 ? (
                  <EmptyState
                    icon={FiActivity}
                    title="No activity yet"
                    description="Chat moderation, lobby decisions and stage changes on your events show up here."
                  />
                ) : (
                  <ul className="max-h-[32rem] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
                    {activity.map((a) => (
                      <li key={a.id} className="px-4 py-2.5">
                        <p className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                          <span className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-current", ACT_TONE[a.kind] || "text-slate-400")} aria-hidden="true" />
                          <span className="min-w-0">{a.text}</span>
                        </p>
                        <p className="mt-0.5 truncate pl-3.5 text-xs text-slate-400">
                          {a.event_title} · {fmtDateTime(a.created_at)}
                          {a.actor ? ` · ${a.actor}` : ""}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </SectionCard>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
