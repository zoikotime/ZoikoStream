// client/src/pages/watch/EventWatch.jsx
// Attendee watch page. Route: /events/:eventId/watch (auth required — see App.jsx).
//
// Viewer scope: this page renders ONLY what an attendee may see. That boundary is enforced
// on the server, not here — GET /events/:id/viewer returns a viewer-safe projection and the
// live socket is filtered per-connection (server/app/services/moderation.viewer_envelope).
// There is no organizer, moderator or host affordance to hide, because none of that data
// reaches this page in the first place.
//
// Two data sources, both real: one read for everything static, the existing socket for
// everything live. See hooks/useViewerEvent.
import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { FiSun, FiMoon, FiWifiOff, FiAlertCircle, FiLock, FiUserCheck } from "react-icons/fi";
import { useTheme } from "../../theme/ThemeContext";
import api, { errMsg } from "../../api";
import useViewerEvent from "../../hooks/useViewerEvent";
import usePlaybackCheck from "../../hooks/usePlaybackCheck";
import useInterval from "../../hooks/useInterval";
import VideoPlayer from "../../components/watch/VideoPlayer";
import EventInfo, { EventActions, SecurityRow } from "../../components/watch/EventInfo";
import { OrganizerCard, ReadinessCard, SupportCard } from "../../components/watch/WatchPanel";
import ParticipationRail from "../../components/watch/ParticipationRail";
import AttendeeControls from "../../components/watch/AttendeeControls";
import Skeleton from "../../ui/Skeleton";
import { notify } from "../../ui/Toast";
import { cx, focusRing } from "../../ui/tokens";

// How often the page banks watch time. The server clamps each claim
// (crud.attendee.MAX_HEARTBEAT_SECONDS), so a suspended tab cannot bank the gap.
const HEARTBEAT_MS = 30_000;

// True-black dark surface, matching the console (tokens.CONSOLE.page). The attendee page
// is the one place a viewer sees the product, so it uses the same page/panel ladder rather
// than a fork of it.
const PAGE = "min-h-screen bg-slate-50 text-slate-800 dark:bg-black dark:text-neutral-200";

function Shell({ children }) {
  const { theme, toggle } = useTheme();
  return (
    <div className={PAGE}>
      <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/85 backdrop-blur dark:border-white/10 dark:bg-black/85">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3 sm:px-6">
          <Link to="/" className="text-lg font-bold tracking-tight text-slate-900 dark:text-white">
            Zoiko<span className="text-violet-500">Stream</span>
          </Link>
          <button
            onClick={toggle}
            className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 dark:text-neutral-400 dark:hover:bg-white/[0.07] dark:hover:text-white"
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
          </button>
        </div>
      </header>
      {children}
    </div>
  );
}

function Loading() {
  return (
    <Shell>
      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-4 py-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_340px] lg:py-12">
        <div className="space-y-5">
          <Skeleton className="h-6 w-48 rounded-full" />
          <Skeleton className="h-11 w-4/5 rounded-lg" />
          <Skeleton className="h-4 w-full rounded" />
          <Skeleton className="aspect-video w-full rounded-xl" />
          <Skeleton className="h-12 w-full rounded-xl" />
        </div>
        <div className="space-y-5">
          <Skeleton className="h-44 w-full rounded-2xl" />
          <Skeleton className="h-56 w-full rounded-2xl" />
        </div>
      </main>
    </Shell>
  );
}

function Problem({ title, detail }) {
  return (
    <Shell>
      <div className="grid min-h-[60vh] place-items-center px-6">
        <div className="max-w-sm text-center">
          <FiAlertCircle className="mx-auto text-3xl text-slate-400 dark:text-neutral-600" />
          <p className="mt-3 font-semibold text-slate-900 dark:text-white">{title}</p>
          {detail && <p className="mt-1 text-sm text-slate-500 dark:text-neutral-400">{detail}</p>}
          <Link
            to="/"
            className="mt-4 inline-block text-sm font-medium text-violet-600 hover:text-violet-500 dark:text-violet-400"
          >
            Back to home
          </Link>
        </div>
      </div>
    </Shell>
  );
}

/** Registration gate. Renders instead of the player when the organizer requires sign-up and this
 *  attendee hasn't — the page itself still loads, which is the whole point: this is where the
 *  Register button lives. Enforcement is server-side (services/viewer.playback_blocked_reason);
 *  this only explains it. */
function RegistrationGate({ access, event, busy, onRegister }) {
  const full = access.registration_limit != null;
  return (
    <div className="grid aspect-video w-full place-items-center rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 text-center dark:border-white/15 dark:bg-white/[0.02]">
      <div className="max-w-sm">
        <FiLock className="mx-auto text-3xl text-slate-400 dark:text-neutral-600" aria-hidden="true" />
        <p className="mt-3 font-semibold text-slate-900 dark:text-white">Register to watch</p>
        <p className="mt-1 text-sm text-slate-500 dark:text-neutral-400">
          {event.title ? `“${event.title}”` : "This event"} needs a free registration before the
          stream opens.
          {full && " Places are limited."}
        </p>
        <button
          type="button"
          onClick={onRegister}
          disabled={busy}
          className={cx(
            "mt-4 inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-violet-500 disabled:opacity-60",
            focusRing
          )}
        >
          <FiUserCheck aria-hidden="true" /> {busy ? "Registering…" : "Register now"}
        </button>
      </div>
    </div>
  );
}

export default function EventWatch() {
  const { eventId } = useParams();
  const { landing, loading, error, reload, live, connection, send, onReaction } = useViewerEvent(eventId);
  const { checks, running, run } = usePlaybackCheck();
  const playerBoxRef = useRef(null);   // the wrapper, for scrollIntoView
  const playerRef = useRef(null);      // the player's imperative handle: { start, stop }

  // Optimistic overlays on the landing read, so a tap responds immediately instead of waiting for
  // a refetch. `null` means "no local opinion — use the server's".
  const [regBusy, setRegBusy] = useState(false);
  const [localRegistered, setLocalRegistered] = useState(null);
  const [localBookmark, setLocalBookmark] = useState(null);
  const [localReminder, setLocalReminder] = useState(undefined);
  const [saved, setSaved] = useState(null);
  const [handRaised, setHandRaised] = useState(false);
  const [downloading, setDownloading] = useState(null);

  const access = landing?.access;
  const registered = localRegistered ?? !!access?.registered;
  const needsRegistration = !!access?.registration_required && !registered;
  const bookmarked = localBookmark ?? !!landing?.me?.bookmarked;
  const reminderAt = localReminder === undefined ? landing?.me?.reminder_at : localReminder;
  const savedQuestions = saved ?? landing?.me?.question_bookmarks ?? [];

  const register = useCallback(async () => {
    setRegBusy(true);
    try {
      await api.post(`/attendee/events/${eventId}/register`);
      setLocalRegistered(true);
      notify.success("You're registered. The stream will open when it goes live.");
      // Refetch, because registering changes `stream.playback_ready` — the player reads that to
      // decide whether it may connect at all.
      reload();
    } catch (e) {
      notify.error(errMsg(e, "Couldn't register for this event"));
    } finally {
      setRegBusy(false);
    }
  }, [eventId, reload]);

  const bookmark = useCallback(async (on) => {
    setLocalBookmark(on);
    try {
      await api.post(`/attendee/events/${eventId}/bookmark`, { on });
    } catch (e) {
      setLocalBookmark(!on);
      notify.error(errMsg(e, "Couldn't save that"));
    }
  }, [eventId]);

  const setReminder = useCallback(async (minutes) => {
    try {
      const { data } = await api.post(`/attendee/events/${eventId}/reminder`,
        { minutes_before: minutes });
      setLocalReminder(data.reminder_at);
      notify.info(minutes == null ? "Reminder cleared." : "Reminder set.");
    } catch (e) {
      notify.error(errMsg(e, "Couldn't set that reminder"));
    }
  }, [eventId]);

  const saveQuestion = useCallback(async (questionId) => {
    try {
      const { data } = await api.post(
        `/attendee/events/${eventId}/questions/${questionId}/bookmark`);
      setSaved(data.question_bookmarks);
    } catch (e) {
      notify.error(errMsg(e, "Couldn't save that question"));
    }
  }, [eventId]);

  const toggleHand = useCallback(() => {
    const next = !handRaised;
    setHandRaised(next);
    send("participant.hand", { raised: next });
  }, [handRaised, send]);

  /** Downloads go through axios so the Bearer token travels — a plain link would 401. */
  const download = useCallback(async (resource) => {
    setDownloading(resource.id);
    try {
      const { data } = await api.get(
        `/attendee/events/${eventId}/resources/${resource.id}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = resource.filename || "resource";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      notify.error(errMsg(e, "Couldn't download that file"));
    } finally {
      setDownloading(null);
    }
  }, [eventId]);

  // Bank watch time only while actually watching a live event. A page left open on a scheduled
  // event is not watching, and counting it would make the history meaningless.
  const watching = live.isLive && !needsRegistration;
  useInterval(() => {
    api.post(`/attendee/events/${eventId}/heartbeat`, { seconds: HEARTBEAT_MS / 1000 })
      .catch(() => { /* watch history is not worth a toast */ });
  }, HEARTBEAT_MS, watching);

  const features = useMemo(() => live.features || {}, [live.features]);

  if (loading) return <Loading />;
  if (error) {
    // The endpoint answers 404 for missing, unpublished AND not-permitted alike, so this
    // page must not guess which — it reports what the server said and nothing more.
    return (
      <Problem
        title={error?.response?.status === 404 ? "This event isn't available" : "Something went wrong"}
        detail={error?.response?.status === 404
          ? "The link may have expired, or you may not have access to this event."
          : errMsg(error)}
      />
    );
  }
  if (!landing) return <Problem title="This event isn't available" />;

  const { event, organizer, security, support } = landing;
  const isLive = live.isLive;

  // "Watch live" scrolls the player into view and calls its start(). Going through the
  // player's own handle means this button and the player's play affordance share one
  // connect path, so they can never disagree about whether playback started.
  const watch = () => {
    playerBoxRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    playerRef.current?.start();
  };

  return (
    <Shell>
      <main className="mx-auto grid max-w-[1500px] grid-cols-1 items-start gap-8 px-4 py-8 sm:px-6 xl:grid-cols-[minmax(0,1fr)_380px] lg:py-12">
        {/* Left: identity, player, participation, actions, security */}
        <div className="space-y-6">
          <EventInfo event={event} security={security} />

          <div ref={playerBoxRef}>
            {needsRegistration ? (
              <RegistrationGate
                access={access}
                event={event}
                busy={regBusy}
                onRegister={register}
              />
            ) : (
              <VideoPlayer
                ref={playerRef}
                eventId={event.id}
                live={isLive}
                viewers={live.viewers}
                poster={event.banner_image || event.thumbnail}
                title={event.title}
              />
            )}
          </div>

          {/* Reactions, raise hand, save, remind — and the live connection readout. */}
          {!needsRegistration && (
            <AttendeeControls
              live={{ ...live, isLive }}
              connection={connection}
              features={features}
              handRaised={handRaised}
              bookmarked={bookmarked}
              reminderAt={reminderAt}
              startsAt={event.start_time}
              onReaction={onReaction}
              send={send}
              onHand={toggleHand}
              onBookmark={bookmark}
              onReminder={setReminder}
            />
          )}

          {/* Chat, Q&A, polls, updates and files. On a narrow screen this sits under the player;
              from xl up it moves into its own column beside it (see the grid below). */}
          {!needsRegistration && (
            <ParticipationRail
              className="h-[34rem] xl:hidden"
              live={{ ...live, isLive }}
              identity={live.identity}
              savedQuestions={savedQuestions}
              send={send}
              onSaveQuestion={saveQuestion}
              onDownload={download}
              downloading={downloading}
            />
          )}

          {/* Connection trouble on the DATA socket (not the media). Worth saying, because
              a stalled viewer count otherwise looks like an empty room. */}
          {(connection.status === "reconnecting" || connection.status === "offline") && (
            <p className="inline-flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
              <FiWifiOff />
              {connection.status === "offline"
                ? "Live updates are offline — the viewer count may be out of date."
                : `Reconnecting to live updates… (attempt ${connection.attempt})`}
            </p>
          )}

          {live.recovering && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              The broadcast dropped briefly — waiting for the organizer to reconnect.
            </p>
          )}

          <EventActions event={event} organizer={organizer} isLive={isLive} onWatch={watch} />

          <SecurityRow security={security} />
        </div>

        {/* Right rail. From xl up the participation surface lives here, sticky, so chat scrolls
            independently of the page and the player never leaves the viewport. */}
        <aside className="space-y-5 xl:sticky xl:top-20">
          {!needsRegistration && (
            <ParticipationRail
              className="hidden h-[calc(100vh-8rem)] xl:flex"
              live={{ ...live, isLive }}
              identity={live.identity}
              savedQuestions={savedQuestions}
              send={send}
              onSaveQuestion={saveQuestion}
              onDownload={download}
              downloading={downloading}
            />
          )}
          <OrganizerCard organizer={organizer} />
          <ReadinessCard checks={checks} running={running} onRerun={run} />
          <SupportCard support={support} organizer={organizer} />
        </aside>
      </main>

      <footer className="border-t border-slate-200 py-6 text-center text-xs text-slate-400 dark:border-white/10 dark:text-neutral-600">
        © {new Date().getFullYear()} ZoikoStream. All rights reserved.
      </footer>
    </Shell>
  );
}
