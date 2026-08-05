// client/src/pages/watch/EventWatch.jsx
// Viewer Portal — what attendees see from an event invite link.
// Route: /events/:eventId/watch. Standalone public page (NOT the Org Dashboard).
//
// The video itself is real: GET /events/:eventId/watch (no auth required for public/
// unlisted events) returns a subscribe-only LiveKit token while the event is live, and a
// signed recording URL once it's ended (see services/livekit.py signed_url) — VideoPlayer
// renders whichever applies. Chat/Q&A/polls are real too, over the same live socket the
// host/moderator consoles use — see the `liveReducer` below. The header/info copy is still
// the original mock data layer. `watchToMockEvent` below is the seam: it maps the real API
// response onto the shape those mock-driven components already expect.
import { useCallback, useEffect, useReducer, useState } from "react";
import useInterval from "../../hooks/useInterval";
import useEventStream from "../../hooks/useEventStream";
import { Link, useParams } from "react-router-dom";
import { FiRadio, FiSun, FiMoon } from "react-icons/fi";
import { useTheme } from "../../theme/ThemeContext";
import { useAuth } from "../../auth/AuthContext";
import api from "../../api";
import { startingViewers } from "../../data/watch";
import WatchHeader from "../../components/watch/WatchHeader";
import VideoPlayer from "../../components/watch/VideoPlayer";
import WatchPanel from "../../components/watch/WatchPanel";
import EventInfo from "../../components/watch/EventInfo";
import RegistrationGate from "../../components/watch/RegistrationGate";
import AccessWindowNotice from "../../components/watch/AccessWindowNotice";
import Spinner from "../../ui/Spinner";

const STATUS_LABEL = { live: "Live", ended: "Completed" }; // anything else -> "Upcoming"

// The header/info/panel components speak the old mock Event shape (name, host, date,
// start/end, accent…). Real watch data doesn't have most of that — fill in what's real,
// default the rest to something these components already render fine as empty.
function watchToMockEvent(watch) {
  const start = watch.start_time ? new Date(watch.start_time) : null;
  const hhmm = (d) => (d ? d.toISOString().slice(11, 16) : "");
  return {
    id: watch.id,
    name: watch.title || "Untitled event",
    status: STATUS_LABEL[watch.status] || "Upcoming",
    date: start ? start.toISOString().slice(0, 10) : "",
    start: hhmm(start),
    end: "",
    timezone: "UTC",
    host: watch.host_name || watch.organization_name || "Host",
    moderators: [],
    speakers: [],
    category: null,
    visibility: watch.visibility,
    registration: "Open",
    registered: null,
    viewers: null,
    accent: "emerald",
    description: watch.description,
  };
}

const POLL_MS = 10000; // how often a not-yet-live page checks whether the event went live

// Real chat/Q&A/polls over the same live socket the host/moderator consoles use (see
// live.py — any authenticated attendee may chat.send, qa.ask, qa.vote or poll.vote, no
// moderator role needed). Anonymous public visitors have no JWT to open that socket with,
// so all three are gated on being signed in; the video itself has no such gate (see the
// /watch endpoint's anonymous LiveKit token).
const LIVE_EMPTY = { messages: [], typing: {}, questions: [], polls: [] };
function liveReducer(state, env) {
  const { channel, type, data } = env;
  switch (`${channel}/${type}`) {
    case "moderator/snapshot":
      return {
        messages: data.messages || [], typing: {},
        questions: data.questions || [], polls: data.polls || [],
      };
    case "chat/message.new":
      return { ...state, messages: [...state.messages, data] };
    case "chat/message.update":
      return { ...state, messages: state.messages.map((m) => (m.id === data.id ? { ...m, ...data } : m)) };
    case "chat/message.delete":
      return { ...state, messages: state.messages.filter((m) => m.id !== data.id) };
    case "chat/typing": {
      const typing = { ...state.typing };
      if (data.typing) typing[data.identity] = { name: data.name };
      else delete typing[data.identity];
      return { ...state, typing };
    }
    case "qa/question.new":
      return { ...state, questions: [...state.questions, data] };
    case "qa/question.update":
      return { ...state, questions: state.questions.map((q) => (q.id === data.id ? { ...q, ...data } : q)) };
    case "qa/question.delete":
      return { ...state, questions: state.questions.filter((q) => q.id !== data.id) };
    case "poll/poll.new":
      return { ...state, polls: [data, ...state.polls] };
    case "poll/poll.update":
      return { ...state, polls: state.polls.map((p) => (p.id === data.id ? { ...p, ...data } : p)) };
    case "poll/poll.delete":
      return { ...state, polls: state.polls.filter((p) => p.id !== data.id) };
    default:
      return state;
  }
}

export default function EventWatch() {
  const { eventId } = useParams();
  const { theme, toggle } = useTheme();
  const { user } = useAuth();

  const [panel, dispatchPanel] = useReducer(liveReducer, LIVE_EMPTY);
  const onLiveEnvelope = useCallback((env) => dispatchPanel(env), []);
  const { status: liveStatus, send: sendLive } = useEventStream(eventId, onLiveEnvelope);

  const [watch, setWatch] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchWatch = () => {
    const reg = localStorage.getItem(`zk_reg_${eventId}`);
    api
      .get(`/events/${eventId}/watch`, { params: reg ? { reg } : undefined })
      .then(({ data }) => setWatch(data))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    fetchWatch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  // Keeps a page opened before the host goes live from needing a manual refresh. Once
  // live, useLiveKitViewer's own room connection is what actually reflects state.
  useInterval(fetchWatch, POLL_MS, Boolean(watch) && watch.status !== "live" && watch.status !== "ended");

  const event = watch ? watchToMockEvent(watch) : null;
  const live = event?.status === "Live";
  const ended = event?.status === "Completed";
  // watch.expired means "the scheduled window lapsed before the host ever went live" —
  // it does NOT mean "there's nothing left to show". Ending a broadcast backfills
  // Event.end_time to that moment (services/broadcast.py `ev.end_time = ev.end_time or
  // now`), so `expired` flips true within milliseconds of any normal "End Event" click.
  // Checked here so a real ended-with-replay event is never mistaken for an event that
  // simply expired unwatched.
  const timeGated = Boolean((watch?.expired && !ended) || watch?.not_started);

  // Live viewer count that gently drifts (setState only in the interval callback).
  const [viewers, setViewers] = useState(startingViewers);
  useInterval(() => setViewers((v) => Math.max(0, v + Math.floor(Math.random() * 15) - 6)), 3000, live);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <Spinner />
      </div>
    );
  }

  if (notFound || !event)
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <div className="text-center">
          <p className="text-sm text-slate-500 dark:text-slate-400">This event could not be found.</p>
          <Link to="/" className="mt-2 inline-block text-sm font-medium text-emerald-600 hover:text-emerald-500 dark:text-emerald-400">
            Back to home
          </Link>
        </div>
      </div>
    );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      {/* Brand bar */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
          <Link to="/" className="text-lg font-bold tracking-tight text-slate-900 dark:text-white">
            Zoiko<span className="text-emerald-500">Stream</span>
          </Link>
          <div className="flex items-center gap-3">
            {live && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/10 px-3 py-1 text-xs font-semibold text-rose-600 dark:text-rose-400">
                <FiRadio className="animate-pulse" /> Live now
              </span>
            )}
            <button
              onClick={toggle}
              className="grid h-9 w-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
              aria-label="Toggle theme"
              title={theme === "dark" ? "Switch to light" : "Switch to dark"}
            >
              {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
            </button>
          </div>
        </div>
      </header>

      {/* Top section: banner */}
      <WatchHeader event={event} viewers={viewers} />

      {/* Main layout: player + info (70%) / chat panel (30%) */}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        {/* Outside the scheduled start_time/end_time window: no video, no chat — just the
            notice. The host's own broadcast/console is unaffected by this (see watch_event). */}
        <div className={`grid grid-cols-1 gap-6 ${timeGated ? "" : "lg:grid-cols-[minmax(0,1fr)_360px]"}`}>
          <div className="space-y-6">
            {ended ? (
              <VideoPlayer event={event} viewers={viewers} watch={watch} />
            ) : watch.expired ? (
              <AccessWindowNotice variant="expired" />
            ) : watch.registration_required && !watch.registered ? (
              <RegistrationGate eventId={eventId} eventTitle={event.name} onRegistered={fetchWatch} />
            ) : watch.not_started ? (
              <AccessWindowNotice variant="not_started" startTime={watch.start_time} />
            ) : (
              <VideoPlayer event={event} viewers={viewers} watch={watch} />
            )}
            <EventInfo event={event} />
          </div>

          {!timeGated && (
            <WatchPanel
              className="h-[70vh] self-start lg:sticky lg:top-20 lg:h-[calc(100vh-6rem)]"
              messages={panel.messages}
              typing={panel.typing}
              questions={panel.questions}
              polls={panel.polls}
              send={sendLive}
              authed={!!user}
              connected={liveStatus === "open"}
            />
          )}
        </div>
      </main>

      <footer className="border-t border-slate-200 py-6 text-center text-xs text-slate-400 dark:border-slate-800">
        © 2024 ZoikoStream. All rights reserved.
      </footer>
    </div>
  );
}
