// client/src/pages/host/Dashboard.jsx
// The HOST's landing page. Route: /host/dashboard (where roleHome sends a host after login).
//
// Deliberately not the control room — that is /host/live. A host arriving at work needs to see
// what they are responsible for, not be dropped into a broadcast surface for whichever event
// happened to be live.
//
// Everything here is real: GET /events?assigned=me is the events this person was actually
// ASSIGNED to (an org member can READ every event in the org, which is not the same thing), and
// GET /events/activity?assigned=me is the feed across those events.
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiActivity, FiAlertTriangle, FiCalendar, FiCheckCircle, FiClock, FiEdit3, FiRadio,
  FiSlash, FiUsers, FiVideo,
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

// The five buckets the brief asks for, in the order a host cares about them: what is happening
// now, what is next, then the archive.
const BUCKETS = [
  { key: "live", label: "On air", icon: FiRadio, match: (e) => isOnAir(e.status) },
  { key: "upcoming", label: "Upcoming", icon: FiCalendar,
    match: (e) => ["published", "scheduled"].includes(e.status) },
  { key: "draft", label: "Drafts", icon: FiEdit3, match: (e) => e.status === "draft" },
  { key: "completed", label: "Completed", icon: FiCheckCircle,
    match: (e) => ["ended", "archived"].includes(e.status) },
  { key: "cancelled", label: "Cancelled", icon: FiSlash, match: (e) => e.status === "cancelled" },
];

const ACT_TONE = {
  recording: "text-rose-500", system: "text-violet-500", join: "text-emerald-500",
  leave: "text-slate-400", mod: "text-amber-500", role: "text-blue-500",
};

const sameDay = (iso) => {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
};

/** One event row. The primary action depends on the event's state, because "open the studio" and
 *  "join the broadcast in progress" are different intents. */
function EventRow({ event, onOpen }) {
  const onAir = isOnAir(event.status);
  const team = event.team_counts || {};
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/host/live?event=${event.id}`}
            className={cx("truncate font-medium text-slate-800 hover:text-violet-600 dark:text-slate-100 dark:hover:text-violet-400", focusRing)}
          >
            {event.title || "Untitled event"}
          </Link>
          <EventStatusBadge status={event.status} size="sm" />
          {event.recording_enabled && <Badge tone="neutral" size="sm">Recording on</Badge>}
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
          {fmtDateTime(event.start_time)}
          {event.current_viewers != null && ` · ${event.current_viewers} watching`}
          {` · ${team.host || 0} host${(team.host || 0) === 1 ? "" : "s"}`}
          {` · ${team.moderator || 0} moderator${(team.moderator || 0) === 1 ? "" : "s"}`}
        </p>
        {/* Actionable, not decorative: a published event with no moderator is something the host
            can still fix before they go on air. */}
        {["published", "scheduled"].includes(event.status) && !team.moderator && (
          <p className="mt-1 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
            <FiAlertTriangle aria-hidden="true" /> No moderator assigned
          </p>
        )}
      </div>
      <Button
        size="sm"
        variant={onAir ? "primary" : "secondary"}
        leftIcon={onAir ? FiRadio : FiVideo}
        onClick={() => onOpen(event)}
      >
        {onAir ? "Join broadcast" : "Open studio"}
      </Button>
    </li>
  );
}

export default function HostDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

  // One page_size=100 read rather than five status-filtered requests: a host's assigned list is
  // small by nature, and bucketing 100 rows in the browser is cheaper than five round trips.
  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get("/events", { params: { assigned: "me", page_size: 100, sort_by: "start_time", order: "asc" } })
        .then((r) => r.data),
      api.get("/events/activity", { params: { assigned: "me", limit: 20 } }).then((r) => r.data),
    ]).then(([events, activity]) => ({ events, activity }))
  );

  // Live figures move; refresh on the same cadence as the org dashboard.
  useInterval(reload, REFRESH_MS, !loading && !error);

  const events = useMemo(() => data?.events?.items || [], [data]);
  const activity = useMemo(() => data?.activity || [], [data]);

  const buckets = useMemo(
    () => BUCKETS.map((b) => ({ ...b, items: events.filter(b.match) })),
    [events]
  );
  const today = useMemo(
    () => events.filter((e) => sameDay(e.start_time)).sort(
      (a, b) => new Date(a.start_time) - new Date(b.start_time)
    ),
    [events]
  );
  const onAir = buckets.find((b) => b.key === "live")?.items || [];

  // The host's "notifications": derived from data we actually hold, not a table that does not
  // exist. Each one is something the host can act on right now.
  const alerts = useMemo(() => {
    const out = [];
    for (const e of events) {
      const team = e.team_counts || {};
      if (isOnAir(e.status) && e.recording_enabled && !e.has_recording) {
        out.push({ id: `rec-${e.id}`, tone: "warning", event: e,
                   text: "On air with recording enabled, but nothing has been captured yet." });
      }
      if (["published", "scheduled"].includes(e.status) && !team.moderator) {
        out.push({ id: `mod-${e.id}`, tone: "warning", event: e,
                   text: "No moderator assigned — you'd be running the audience alone." });
      }
      if (sameDay(e.start_time) && ["published", "scheduled"].includes(e.status)) {
        out.push({ id: `today-${e.id}`, tone: "info", event: e, text: "Starts today." });
      }
    }
    return out.slice(0, 6);
  }, [events]);

  const open = (event) => navigate(`/host/live?event=${event.id}`);

  if (loading) {
    return (
      <div className="min-h-screen space-y-4 bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
        <Skeleton variant="title" className="w-72" />
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} variant="block" className="h-24" />)}
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
              {user?.full_name ? `Welcome, ${user.full_name.split(" ")[0]}` : "Host dashboard"}
            </h1>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
              The events you're assigned to run
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
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-5">
              {buckets.map((b) => (
                <StatCard key={b.key} label={b.label} value={b.items.length} />
              ))}
            </div>

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
                        className={cx("ml-auto rounded text-xs font-semibold text-violet-600 hover:underline dark:text-violet-400", focusRing)}
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
                <SectionCard
                  title="Today's schedule"
                  subtitle={today.length ? `${today.length} event${today.length === 1 ? "" : "s"}` : "Nothing scheduled for today"}
                  icon={FiClock}
                  padding="none"
                >
                  {today.length === 0 ? (
                    <EmptyState
                      icon={FiClock}
                      title="Nothing today"
                      description="Your next assigned event will appear here on the day it runs."
                    />
                  ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                      {today.map((e) => <EventRow key={e.id} event={e} onOpen={open} />)}
                    </ul>
                  )}
                </SectionCard>

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
                  <SectionCard title="Your events" icon={FiVideo} padding="none">
                    <EmptyState
                      icon={FiUsers}
                      title="You're not assigned to any events yet"
                      description="An organization admin assigns hosts to events. Once you're assigned, the event appears here and you can open its studio."
                    />
                  </SectionCard>
                )}
              </div>

              <SectionCard title="Recent activity" subtitle="Across your events" icon={FiActivity} padding="none">
                {activity.length === 0 ? (
                  <EmptyState
                    icon={FiActivity}
                    title="No activity yet"
                    description="Chat moderation, recordings and stage changes on your events show up here."
                  />
                ) : (
                  <ul className="max-h-[32rem] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
                    {activity.map((a) => (
                      <li key={a.id} className="px-4 py-2.5">
                        <p className="flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
                          <span className={cx("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-current", ACT_TONE[a.kind] || "text-slate-400")} aria-hidden="true" />
                          <span className="min-w-0">{a.text}</span>
                        </p>
                        <p className="mt-0.5 pl-3.5 truncate text-xs text-slate-400">
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
