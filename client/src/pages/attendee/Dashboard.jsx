// client/src/pages/attendee/Dashboard.jsx
// The ATTENDEE's home. Route: /attendee/dashboard (where roleHome sends a viewer after login).
//
// Everything comes from ONE request: GET /attendee/events returns every event this person can see,
// each carrying their own relationship with it (registered / bookmarked / watched / reminder). The
// buckets below are derived from those flags in the browser, because they are five views of the
// same twenty rows — five status-filtered requests would be five round trips for the same data.
//
// A viewer had no app home before this: roleHome mapped `viewer` to null and they were sent to the
// public marketing page. That is why this is the landing route rather than an extra tab.
import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  FiBookmark, FiCalendar, FiClock, FiDownload, FiPlayCircle, FiRadio, FiSearch, FiSettings,
  FiSliders, FiUserCheck, FiVideo, FiAward, FiBell,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { useAuth } from "../../auth/AuthContext";
import Skeleton from "../../ui/Skeleton";
import Badge from "../../ui/Badge";
import { Input } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import SectionCard from "../../components/admin/SectionCard";
import StatCard from "../../components/admin/StatCard";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import ErrorState from "../../components/organization/OrganizationErrorState";
import EventStatusBadge from "../../components/organization/EventStatusBadge";
import AttendeePreferences from "../../components/watch/AttendeePreferences";
import { cx, focusRing, type } from "../../ui/tokens";
import {
  DASHBOARD_BUCKETS, fmtCountdown, fmtInZone, fmtWatched, isOnAirStatus, msUntil, watchHistory,
} from "../../data/attendee";

const REFRESH_MS = 60_000;

function EventCard({ event, onOpen, onBookmark }) {
  const onAir = isOnAirStatus(event.status);
  const until = msUntil(event.start_time);
  const watched = fmtWatched(event.watch_seconds);
  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3">
      {event.thumbnail || event.banner_image ? (
        <img
          src={event.thumbnail || event.banner_image}
          alt=""
          loading="lazy"
          className="hidden h-12 w-20 shrink-0 rounded-lg object-cover sm:block"
        />
      ) : (
        <span className="hidden h-12 w-20 shrink-0 place-items-center rounded-lg bg-slate-100 sm:grid dark:bg-white/[0.06]">
          <FiVideo className="text-slate-400" aria-hidden="true" />
        </span>
      )}

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/events/${event.id}/watch`}
            className={cx("truncate font-medium text-slate-800 hover:text-violet-600 dark:text-neutral-100 dark:hover:text-violet-400", focusRing)}
          >
            {event.title || "Untitled event"}
          </Link>
          <EventStatusBadge status={event.status} size="sm" />
          {onAir && <Badge tone="danger" size="sm" dot>Live now</Badge>}
          {event.registered && <Badge tone="success" size="sm">Registered</Badge>}
          {event.registration_required && !event.registered && (
            <Badge tone="warning" size="sm">Registration needed</Badge>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-slate-500 dark:text-neutral-400">
          {fmtInZone(event.start_time, event.timezone)}
          {until != null && !onAir && ` · ${fmtCountdown(until)}`}
          {watched && ` · ${watched}`}
          {event.reminder_at && " · reminder set"}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => onBookmark(event, !event.bookmarked)}
          aria-pressed={event.bookmarked}
          aria-label={event.bookmarked ? `Remove ${event.title} from saved` : `Save ${event.title}`}
          className={cx(
            "grid h-8 w-8 place-items-center rounded-lg transition",
            event.bookmarked
              ? "text-violet-600 dark:text-violet-400"
              : "text-slate-300 hover:text-slate-500 dark:text-neutral-600 dark:hover:text-neutral-300",
            focusRing
          )}
        >
          <FiBookmark />
        </button>
        <button
          type="button"
          onClick={() => onOpen(event)}
          className={cx(
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition",
            onAir
              ? "bg-violet-600 text-white hover:bg-violet-500"
              : "border border-slate-200 text-slate-700 hover:bg-slate-50 dark:border-white/10 dark:text-neutral-200 dark:hover:bg-white/[0.06]",
            focusRing
          )}
        >
          {onAir ? <><FiRadio aria-hidden="true" /> Join</> : <><FiPlayCircle aria-hidden="true" /> View</>}
        </button>
      </div>
    </li>
  );
}

export default function AttendeeDashboard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [query, setQuery] = useState("");
  const [overrides, setOverrides] = useState({});   // optimistic bookmark flips, by event id
  const [prefsOpen, setPrefsOpen] = useState(false);

  const { data, loading, error, reload } = useApi(() =>
    api.get("/attendee/events").then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS, !loading && !error);

  const events = useMemo(() => {
    const rows = data || [];
    const q = query.trim().toLowerCase();
    return rows
      .map((e) => (overrides[e.id] === undefined ? e : { ...e, bookmarked: overrides[e.id] }))
      .filter((e) => !q || `${e.title || ""} ${e.category || ""} ${(e.tags || []).join(" ")}`
        .toLowerCase().includes(q));
  }, [data, query, overrides]);

  // Each event lands in the FIRST bucket it matches, so "live now" always wins over "upcoming".
  const buckets = useMemo(() => {
    const claimed = new Set();
    return DASHBOARD_BUCKETS.map((b) => {
      const items = events.filter((e) => !claimed.has(e.id) && b.match(e));
      items.forEach((e) => claimed.add(e.id));
      return { ...b, items };
    });
  }, [events]);

  const history = useMemo(() => watchHistory(events).slice(0, 8), [events]);
  const reminders = useMemo(
    () => events.filter((e) => e.reminder_at && msUntil(e.start_time) > 0)
      .sort((a, b) => new Date(a.start_time) - new Date(b.start_time)),
    [events]
  );
  const liveNow = buckets.find((b) => b.key === "live")?.items || [];
  const registeredCount = events.filter((e) => e.registered).length;
  const savedCount = events.filter((e) => e.bookmarked).length;
  const totalWatched = events.reduce((s, e) => s + (e.watch_seconds || 0), 0);

  const open = (event) => navigate(`/events/${event.id}/watch`);

  const bookmark = useCallback(async (event, on) => {
    setOverrides((prev) => ({ ...prev, [event.id]: on }));
    try {
      await api.post(`/attendee/events/${event.id}/bookmark`, { on });
    } catch (e) {
      setOverrides((prev) => ({ ...prev, [event.id]: !on }));
      notify.error(errMsg(e, "Couldn't save that"));
    }
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen space-y-4 bg-slate-50 p-4 sm:p-6 dark:bg-black">
        <Skeleton variant="title" className="w-72" />
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} variant="block" className="h-24" />)}
        </div>
        <Skeleton variant="block" className="h-64" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 sm:p-6 dark:bg-black">
      <div className="mx-auto max-w-[1400px] space-y-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className={cx(type.h1, "truncate text-slate-900 dark:text-white")}>
              {user?.full_name ? `Hello, ${user.full_name.split(" ")[0]}` : "My events"}
            </h1>
            <p className="mt-0.5 text-sm text-slate-500 dark:text-neutral-400">
              Everything you're watching, saved, or signed up for
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <Input
                variant="console"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search events…"
                aria-label="Search events"
                className="w-56 pl-9"
              />
            </div>
            <button
              type="button"
              onClick={() => setPrefsOpen((v) => !v)}
              aria-expanded={prefsOpen}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-white dark:border-white/10 dark:text-neutral-200 dark:hover:bg-white/[0.06]",
                focusRing
              )}
            >
              <FiSliders aria-hidden="true" /> Settings
            </button>
            {liveNow.length > 0 && (
              <button
                type="button"
                onClick={() => open(liveNow[0])}
                className={cx("inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-violet-500", focusRing)}
              >
                <FiRadio aria-hidden="true" /> Join {liveNow[0].title || "the live event"}
              </button>
            )}
          </div>
        </div>

        {error ? (
          <ErrorState error={error} onRetry={reload} title="Couldn't load your events" />
        ) : (
          <>
            {prefsOpen && <AttendeePreferences onClose={() => setPrefsOpen(false)} />}

            <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
              <StatCard label="Live now" value={liveNow.length} />
              <StatCard label="Registered" value={registeredCount} />
              <StatCard label="Saved" value={savedCount} />
              <StatCard
                label="Time watched"
                value={totalWatched >= 3600
                  ? `${Math.floor(totalWatched / 3600)}h ${Math.round((totalWatched % 3600) / 60)}m`
                  : `${Math.round(totalWatched / 60)}m`}
              />
            </div>

            {reminders.length > 0 && (
              <SectionCard title="Reminders" icon={FiBell} accent="amber">
                <ul className="space-y-2">
                  {reminders.slice(0, 4).map((e) => (
                    <li key={e.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge tone="warning" size="sm" dot>{e.title || "Untitled event"}</Badge>
                      <span className="text-slate-600 dark:text-neutral-300">
                        Starts {fmtCountdown(msUntil(e.start_time))}
                      </span>
                      <button
                        onClick={() => open(e)}
                        className={cx("ml-auto rounded text-xs font-semibold text-violet-600 hover:underline dark:text-violet-400", focusRing)}
                      >
                        Open
                      </button>
                    </li>
                  ))}
                </ul>
                {/* Said out loud: the reminder is a countdown here, not an email. */}
                <p className="mt-2 text-[11px] text-slate-400">
                  Reminders show up on this page. This deployment has no scheduled email sender, so
                  they don't arrive in your inbox.
                </p>
              </SectionCard>
            )}

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="space-y-5">
                {buckets.filter((b) => b.items.length > 0).map((b) => (
                  <SectionCard
                    key={b.key}
                    title={b.label}
                    subtitle={`${b.items.length} event${b.items.length === 1 ? "" : "s"}`}
                    icon={b.key === "live" ? FiRadio
                      : b.key === "registered" ? FiUserCheck
                        : b.key === "bookmarked" ? FiBookmark
                          : b.key === "upcoming" ? FiCalendar : FiClock}
                    padding="none"
                  >
                    <ul className="divide-y divide-slate-100 dark:divide-white/[0.06]">
                      {b.items.slice(0, 10).map((e) => (
                        <EventCard key={e.id} event={e} onOpen={open} onBookmark={bookmark} />
                      ))}
                    </ul>
                  </SectionCard>
                ))}

                {events.length === 0 && (
                  <SectionCard title="Events" icon={FiVideo} padding="none">
                    <EmptyState
                      icon={FiCalendar}
                      title={query ? "Nothing matches that search" : "No events yet"}
                      description={query
                        ? "Try a different search."
                        : "Public events appear here as organizers publish them. An invitation link will bring you straight to its page."}
                    />
                  </SectionCard>
                )}
              </div>

              <div className="space-y-5">
                <SectionCard
                  title="Continue watching"
                  subtitle={history.length ? "Where you left off" : "Nothing yet"}
                  icon={FiClock}
                  padding="none"
                >
                  {history.length === 0 ? (
                    <EmptyState
                      icon={FiPlayCircle}
                      title="No watch history"
                      description="Events you've watched show up here with how long you spent in them."
                      className="py-8"
                    />
                  ) : (
                    <ul className="divide-y divide-slate-100 dark:divide-white/[0.06]">
                      {history.map((e) => (
                        <li key={e.id} className="px-4 py-2.5">
                          <Link
                            to={`/events/${e.id}/watch`}
                            className={cx("truncate text-sm font-medium text-slate-800 hover:text-violet-600 dark:text-neutral-100 dark:hover:text-violet-400", focusRing)}
                          >
                            {e.title || "Untitled event"}
                          </Link>
                          <p className="mt-0.5 truncate text-xs text-slate-400">
                            {fmtWatched(e.watch_seconds) || "opened"}
                            {e.join_count > 1 && ` · ${e.join_count} visits`}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </SectionCard>

                {/* Downloads live on each event's page, because that is where the permission is
                    checked — a cross-event downloads list would need a fan-out request per event
                    for files most people never open. Said plainly rather than shown as an empty
                    panel that never fills. */}
                <SectionCard title="Downloads" icon={FiDownload}>
                  <p className="text-sm text-slate-600 dark:text-neutral-300">
                    Slides and handouts appear on each event's page, in the <strong>Files</strong> tab
                    beside the player — the organizers release them per event.
                  </p>
                </SectionCard>

                <SectionCard title="Certificates" icon={FiAward}>
                  <p className="text-sm text-slate-600 dark:text-neutral-300">
                    Not available yet.
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    Attendance is already being recorded (join times and watch duration per event),
                    which is what a certificate would be issued from — but no certificate template
                    or issuing flow exists in this platform, so nothing is offered here rather than
                    showing a button that produces nothing.
                  </p>
                </SectionCard>

                <SectionCard title="Your settings" icon={FiSettings}>
                  <p className="text-sm text-slate-600 dark:text-neutral-300">
                    Language, text size, contrast, motion and notification choices.
                  </p>
                  <button
                    type="button"
                    onClick={() => setPrefsOpen(true)}
                    className={cx("mt-2 rounded text-sm font-semibold text-violet-600 hover:underline dark:text-violet-400", focusRing)}
                  >
                    Open settings
                  </button>
                </SectionCard>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
