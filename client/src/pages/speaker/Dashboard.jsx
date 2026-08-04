// client/src/pages/speaker/Dashboard.jsx
// The SPEAKER's landing page. Route: /speaker/dashboard (where roleHome sends them).
//
// Same split as the host and moderator consoles: this is what you are on and when, /speaker/live
// is the session itself. A speaker's job before going on is different from everybody else's —
// check the schedule, check the deck is approved, check the camera works — so those three are the
// page.
//
// Everything is real. GET /events?assigned=me&assigned_role=speaker|panelist is what this person
// was actually assigned to speak at, the announcements and activity come from the existing
// endpoints, and the files come from the speaker assets API. The calendar file is generated in the
// browser (data/speaker.buildIcs) — a .ics is a text format, so a server endpoint for it would be
// work for nothing.
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiActivity, FiAlertTriangle, FiCalendar, FiCheckCircle, FiClock, FiDownload, FiFileText,
  FiMic, FiRadio, FiUsers, FiVolume2,
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
import TechnicalHealth from "../../components/speaker/TechnicalHealth";
import { cx, focusRing, type } from "../../ui/tokens";
import { fmtDateTime, isOnAir } from "../../data/events";
import {
  ASSET_STATUS_LABEL, ASSET_STATUS_TONE, downloadIcs, fmtBytes, fmtCountdown, fmtInZone, msUntil,
} from "../../data/speaker";

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

function SessionRow({ event, onOpen }) {
  const onAir = isOnAir(event.status);
  const until = msUntil(event.start_time);
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/speaker/live?event=${event.id}`}
            className={cx("truncate font-medium text-slate-800 hover:text-emerald-600 dark:text-slate-100 dark:hover:text-emerald-400", focusRing)}
          >
            {event.title || "Untitled session"}
          </Link>
          <EventStatusBadge status={event.status} size="sm" />
          {onAir && <Badge tone="danger" size="sm" dot>On air now</Badge>}
        </div>
        {/* Both timezones, because a speaker in another country needs both and guessing which one
            is meant is how people miss their slot. */}
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
          {fmtInZone(event.start_time, event.timezone)}
          {until != null && !onAir && ` · ${fmtCountdown(until)}`}
          {event.duration_minutes ? ` · ${event.duration_minutes} min` : ""}
        </p>
      </div>
      <Button
        size="sm"
        variant={onAir ? "primary" : "secondary"}
        leftIcon={onAir ? FiRadio : FiMic}
        onClick={() => onOpen(event)}
      >
        {onAir ? "Join session" : "Open green room"}
      </Button>
    </li>
  );
}

const BUCKETS = [
  { key: "live", label: "On air", icon: FiRadio, match: (e) => isOnAir(e.status) },
  { key: "today", label: "Today", icon: FiClock,
    match: (e) => sameDay(e.start_time) && !isOnAir(e.status) },
  { key: "upcoming", label: "Upcoming", icon: FiCalendar,
    match: (e) => ["published", "scheduled"].includes(e.status) && !sameDay(e.start_time) },
  { key: "done", label: "Completed", icon: FiCheckCircle,
    match: (e) => ["ended", "archived"].includes(e.status) },
];

export default function SpeakerDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();

  // One thunk for the whole page. The presentation files depend on WHICH sessions came back, so
  // fetching them here rather than in a second effect keeps it to one load with one loading state
  // — and avoids mirroring a fetch result into state through an effect.
  const { data, loading, error, reload } = useApi(async () => {
    const [events, activity] = await Promise.all([
      api.get("/events", {
        params: {
          assigned: "me", assigned_role: ["speaker", "panelist"],
          page_size: 100, sort_by: "start_time", order: "asc",
        },
      }).then((r) => r.data),
      api.get("/events/activity", { params: { assigned: "me", limit: 20 } }).then((r) => r.data),
    ]);
    // Files for the sessions that still matter. One request per session rather than a new
    // aggregate endpoint: a speaker has a handful, and the per-event route already enforces
    // exactly the permission this needs.
    const relevant = (events.items || [])
      .filter((e) => !["ended", "archived", "cancelled"].includes(e.status))
      .slice(0, 6);
    const files = (await Promise.all(relevant.map((e) =>
      api.get(`/speaker/events/${e.id}/assets`)
        .then((r) => r.data.map((a) => ({ ...a, event: e })))
        .catch(() => [])      // a session whose team changed must not blank the whole list
    ))).flat();
    return { events, activity, files };
  });

  useInterval(reload, REFRESH_MS, !loading && !error);

  const events = useMemo(() => data?.events?.items || [], [data]);
  const activity = useMemo(() => data?.activity || [], [data]);
  const files = useMemo(() => data?.files || [], [data]);

  const buckets = useMemo(
    () => BUCKETS.map((b) => ({ ...b, items: events.filter(b.match) })),
    [events]
  );
  const onAir = useMemo(
    () => buckets.find((b) => b.key === "live")?.items || [],
    [buckets]
  );
  // The next thing that actually needs this person, live first.
  const next = useMemo(() => {
    const upcoming = events
      .filter((e) => e.start_time && msUntil(e.start_time) > -3600_000
        && !["ended", "archived", "cancelled"].includes(e.status))
      .sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
    return onAir[0] || upcoming[0] || null;
  }, [events, onAir]);

  // Notifications a speaker can act on, derived from data we hold rather than a table that does
  // not exist. Each one is a real thing to do before going on.
  const alerts = useMemo(() => {
    const out = [];
    for (const e of events) {
      if (isOnAir(e.status)) {
        out.push({ id: `live-${e.id}`, tone: "warning", event: e, text: "This session is on air now." });
      } else if (sameDay(e.start_time) && ["published", "scheduled"].includes(e.status)) {
        out.push({ id: `today-${e.id}`, tone: "info", event: e,
                   text: `Starts ${fmtCountdown(msUntil(e.start_time))}.` });
      }
    }
    for (const f of files) {
      if (f.status === "pending") {
        out.push({ id: `pend-${f.id}`, tone: "info", event: f.event,
                   text: `"${f.filename}" is waiting for a host to approve it.` });
      } else if (f.status === "rejected") {
        out.push({ id: `rej-${f.id}`, tone: "warning", event: f.event,
                   text: `"${f.filename}" was rejected${f.review_note ? `: ${f.review_note}` : "."}` });
      }
    }
    return out.slice(0, 6);
  }, [events, files]);

  const open = (event) => navigate(`/speaker/live?event=${event.id}`);

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
              {user?.full_name ? `Welcome, ${user.full_name.split(" ")[0]}` : "Speaker dashboard"}
            </h1>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
              The sessions you're speaking at
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {events.length > 0 && (
              <Button
                variant="secondary"
                leftIcon={FiDownload}
                onClick={() => downloadIcs("my-sessions.ics", events, { origin: window.location.origin })}
                title="Download every session as a calendar file"
              >
                Add to calendar
              </Button>
            )}
            {next && (
              <Button leftIcon={isOnAir(next.status) ? FiRadio : FiMic} onClick={() => open(next)}>
                {isOnAir(next.status) ? "Join now" : "Open green room"}
              </Button>
            )}
          </div>
        </div>

        {error ? (
          <ErrorState error={error} onRetry={reload} title="Couldn't load your sessions" />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              {buckets.map((b) => (
                <StatCard key={b.key} label={b.label} value={b.items.length} />
              ))}
            </div>

            {/* Countdown to the next slot — the one number a speaker checks all morning. */}
            {next && (
              <SectionCard
                title={isOnAir(next.status) ? "On air now" : "Up next"}
                icon={isOnAir(next.status) ? FiRadio : FiClock}
                accent={isOnAir(next.status) ? "rose" : undefined}
              >
                <div className="flex flex-wrap items-center gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-lg font-semibold text-slate-900 dark:text-white">
                      {next.title || "Untitled session"}
                    </p>
                    <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                      {fmtInZone(next.start_time, next.timezone)}
                    </p>
                  </div>
                  <p className="text-2xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                    {isOnAir(next.status) ? "live" : fmtCountdown(msUntil(next.start_time))}
                  </p>
                  <Button size="sm" onClick={() => open(next)}>Open</Button>
                </div>
              </SectionCard>
            )}

            {alerts.length > 0 && (
              <SectionCard title="Before you go on" icon={FiAlertTriangle} accent="amber">
                <ul className="space-y-2">
                  {alerts.map((a) => (
                    <li key={a.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge tone={a.tone === "warning" ? "warning" : "info"} size="sm" dot>
                        {a.event?.title || "Session"}
                      </Badge>
                      <span className="text-slate-600 dark:text-slate-300">{a.text}</span>
                      {a.event && (
                        <button
                          onClick={() => open(a.event)}
                          className={cx("ml-auto rounded text-xs font-semibold text-emerald-600 hover:underline dark:text-emerald-400", focusRing)}
                        >
                          Open
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </SectionCard>
            )}

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
              <div className="space-y-5">
                {buckets.filter((b) => b.items.length > 0).map((b) => (
                  <SectionCard
                    key={b.key}
                    title={b.label}
                    subtitle={`${b.items.length} session${b.items.length === 1 ? "" : "s"}`}
                    icon={b.icon}
                    padding="none"
                  >
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                      {b.items.slice(0, 8).map((e) => (
                        <SessionRow key={e.id} event={e} onOpen={open} />
                      ))}
                    </ul>
                  </SectionCard>
                ))}

                {events.length === 0 && (
                  <SectionCard title="Your sessions" icon={FiMic} padding="none">
                    <EmptyState
                      icon={FiUsers}
                      title="You're not on any sessions yet"
                      description="An organization admin assigns speakers and panellists to events. Once you're on one, it appears here with its schedule and your presentation files."
                    />
                  </SectionCard>
                )}

                {/* Presentation files across the sessions that still matter. */}
                <SectionCard
                  title="Your presentation files"
                  subtitle={files.length ? `${files.length} file${files.length === 1 ? "" : "s"}` : "Nothing uploaded"}
                  icon={FiFileText}
                  padding="none"
                >
                  {files.length === 0 ? (
                    <EmptyState
                      icon={FiFileText}
                      title="No files yet"
                      description="Upload a PDF or images from inside a session. A host approves it, then you can put it on screen."
                    />
                  ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                      {files.map((f) => (
                        <li key={f.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                              {f.filename}
                            </p>
                            <p className="truncate text-xs text-slate-400">
                              {f.event?.title || "Session"} · {fmtBytes(f.size_bytes)}
                              {f.pages ? ` · ${f.pages} page${f.pages === 1 ? "" : "s"}` : ""}
                            </p>
                          </div>
                          <Badge tone={ASSET_STATUS_TONE[f.status]} size="sm">
                            {ASSET_STATUS_LABEL[f.status] || f.status}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                </SectionCard>
              </div>

              <div className="space-y-5">
                {/* The pre-flight, right here on the landing page: the point of a green room is to
                    find out your microphone is muted BEFORE you are on air. */}
                <TechnicalHealth
                  settings={{}}
                  stats={null}
                  quality={null}
                  speakingSeconds={null}
                  send={() => {}}
                />

                <SectionCard title="Announcements" icon={FiVolume2} padding="none">
                  <EmptyState
                    icon={FiVolume2}
                    title="Nothing right now"
                    description="Announcements a host broadcasts during a session appear in the session console, live."
                    className="py-8"
                  />
                </SectionCard>

                <SectionCard title="Recent activity" subtitle="Across your sessions" icon={FiActivity} padding="none">
                  {activity.length === 0 ? (
                    <EmptyState
                      icon={FiActivity}
                      title="No activity yet"
                      description="Presentations, stage changes and recordings on your sessions show up here."
                    />
                  ) : (
                    <ul className="max-h-[26rem] divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
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
            </div>
          </>
        )}
      </div>
    </div>
  );
}
