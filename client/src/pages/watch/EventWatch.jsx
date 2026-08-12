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
import WatchHeader from "../../components/watch/WatchHeader";
import VideoPlayer from "../../components/watch/VideoPlayer";
import WatchPanel from "../../components/watch/WatchPanel";
import EventInfo from "../../components/watch/EventInfo";
import ReactionBar from "../../components/watch/ReactionBar";
import RegistrationGate from "../../components/watch/RegistrationGate";
import AccessWindowNotice from "../../components/watch/AccessWindowNotice";
import Spinner from "../../ui/Spinner";
import Logo from "../../ui/Logo";
import { notify } from "../../ui/Toast";

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
    startISO: watch.start_time || null, // raw, for the .ics the banner builds client-side
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
// live.py — any authenticated attendee, OR a name+email self-registration (mustIdentify
// below), may chat.send, qa.ask, qa.vote or poll.vote — no moderator role, and no login,
// needed.
const LIVE_EMPTY = {
  messages: [],
  typing: {},
  questions: [],
  polls: [],
  participants: {},
  // Backend-authoritative reaction counts (server/app/services/bus.py reaction_all),
  // keyed like ReactionBar's REACTIONS list. Empty until the snapshot/first update
  // arrives — ReactionBar defaults any missing key to 0 rather than a fake baseline.
  reactions: {},
};

function liveReducer(state, env) {
  const { channel, type, data } = env;

  switch (`${channel}/${type}`) {
    case "moderator/snapshot":
      return {
        messages: data.messages || [],
        typing: {},
        questions: data.questions || [],
        polls: data.polls || [],
        participants: Object.fromEntries(
          (data.participants || []).map((p) => [p.identity, p])
        ),
        // A viewer joining (or reconnecting) sees the CURRENT tally immediately, not
        // 0/0/0/0/0 waiting for the next tap — same guarantee the rest of the snapshot
        // gives messages/questions/polls.
        reactions: data.reactions || {},
      };

    case "participants/participant.join":
    case "participants/participant.update":
      return {
        ...state,
        participants: {
          ...state.participants,
          [data.identity]: data,
        },
      };

    case "participants/participant.leave": {
      const participants = { ...state.participants };
      delete participants[data.identity];

      return {
        ...state,
        participants,
      };
    }
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
    case "reactions/reaction.update":
      // Every connected viewer of THIS event gets this envelope (server/app/services/
      // bus.py publish is scoped per event_id), so event isolation is inherited for
      // free — the reducer never has to check data.event_id against eventId here.
      return { ...state, reactions: data.reactions || {} };
    default:
      return state;
  }
}
export default function EventWatch() {
  const { eventId } = useParams();
  const { theme, toggle } = useTheme();
  const { user } = useAuth();
  // The anonymous-viewer counterpart to `user`: a self-serve name+email registration
  // (RegistrationGate for a registration_required event, or IdentifyForm for chat/Q&A/polls
  // on any other event — both call the same POST /events/:id/register). Lifted to state
  // (not read fresh from localStorage each render) so identifying mid-visit reconnects the
  // live socket with the new identity instead of waiting for a refresh.
  const [regToken, setRegTokenState] = useState(() => localStorage.getItem(`zk_reg_${eventId}`));
  const setRegToken = useCallback((token) => {
    localStorage.setItem(`zk_reg_${eventId}`, token);
    setRegTokenState(token);
  }, [eventId]);
  const [linkToken, setLinkTokenState] = useState(() => localStorage.getItem(`zk_link_${eventId}`));
  const setLinkToken = useCallback((token) => {
    localStorage.setItem(`zk_link_${eventId}`, token);
    setLinkTokenState(token);
  }, [eventId]);

  const [panel, dispatchPanel] = useReducer(liveReducer, LIVE_EMPTY);
  // Real viewers only — staff and waiting-room entries never count as "watching".
  const viewers = Object.values(panel.participants || {}).filter(
    (participant) =>
      participant.role === "viewer" &&
      !participant.waiting
  ).length;
  // A rejected chat.send/qa.ask/poll.vote (chat turned off, slow mode, emoji-only mode,
  // banned, …) comes back as a moderator/error envelope addressed only to this socket — the
  // reducer above doesn't have a case for it (nothing to store), so without this the
  // message just silently vanishes and "chat isn't working" is the only symptom a viewer
  // ever sees. Surfacing the server's actual reason instead.
  const onLiveEnvelope = useCallback((env) => {
    if (env.channel === "moderator" && env.type === "error") {
      notify.error(env.data.message);
      return;
    }
    dispatchPanel(env);
  }, []);
  const {
    status: liveStatus,
    send: sendLive,
    disconnect: disconnectLive,
  } = useEventStream(eventId, onLiveEnvelope, regToken, linkToken);

  const [watch, setWatch] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [loading, setLoading] = useState(true);

  // Reset for a new eventId synchronously during render, not inside an effect — this is
  // React's own documented pattern for "adjusting state when a prop changes"
  // (react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes).
  // Setting state mid-render like this is safe (React restarts the render immediately,
  // no extra commit) and avoids a wasted first paint of the previous event's data/loading
  // state before the effect below even runs.
  const [loadedEventId, setLoadedEventId] = useState(eventId);
  if (eventId !== loadedEventId) {
    setLoadedEventId(eventId);
    setWatch(null);
    setNotFound(false);
    setLoading(true);
  }

  const fetchWatch = () => {
    // A host-invited or self-registered link carries the access token in the URL
    // (?reg=... or ?link=...) — save it locally so a refresh (or a later visit with no
    // query string) keeps working without the visitor needing to click the shared link again.
    const params = new URLSearchParams(window.location.search);
    const urlReg = params.get("reg");
    const urlLink = params.get("link");
    if (urlReg) setRegToken(urlReg);
    if (urlLink) setLinkToken(urlLink);
    const reg = urlReg || regToken;
    const link = urlLink || linkToken;
    const accessParams = { ...(reg ? { reg } : {}), ...(link ? { link } : {}) };
    api
      .get(`/events/${eventId}/watch`, { params: Object.keys(accessParams).length ? accessParams : undefined })
      .then(({ data }) => setWatch(data))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    // fetchWatch only sets state inside its own .then/.catch/.finally (an async
    // continuation, exactly what this rule asks for) — the lint rule's static analysis
    // just can't see through that indirection to tell this apart from a synchronous
    // setState call, which is the actual anti-pattern it exists to catch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchWatch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  // Keeps a page opened before the host goes live from needing a manual refresh. Once
  // live, useLiveKitViewer's own room connection is what actually reflects state.
  useInterval(fetchWatch, POLL_MS, Boolean(watch) && watch.status !== "live" && watch.status !== "ended");

  const event = watch ? watchToMockEvent(watch) : null;
  const live = event?.status === "Live";
  const ended = event?.status === "Completed";
  const identified = !!(user || regToken || linkToken);
  const handleLeaveEvent = useCallback(() => {
    disconnectLive();

    localStorage.removeItem(`zk_reg_${eventId}`);
    localStorage.removeItem(`zk_link_${eventId}`);
    setRegTokenState(null);
    setLinkTokenState(null);

    window.location.href = "/";
  }, [disconnectLive, eventId]);
  // Every public/unlisted visitor identifies with name+email before seeing any video —
  // not just when the host turned on "registration required". Private events are exempt:
  // reaching this page with real watch data already means the visitor passed a
  // host-controlled check (org membership, an invite's `reg` token, or an access `link`),
  // and register_for_event refuses self-serve registration on a private event outright
  // (doc-level anti-side-door rule), so routing them through this same gate would just 403.
  const mustIdentify = Boolean(watch && watch.visibility !== "private" && !identified);
  // watch.expired means "the scheduled window lapsed before the host ever went live" —
  // it does NOT mean "there's nothing left to show". Ending a broadcast backfills
  // Event.end_time to that moment (services/broadcast.py `ev.end_time = ev.end_time or
  // now`), so `expired` flips true within milliseconds of any normal "End Event" click.
  // Checked here so a real ended-with-replay event is never mistaken for an event that
  // simply expired unwatched.
  const timeGated = Boolean(mustIdentify || (watch?.expired && !ended) || watch?.not_started);

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
      {/* Brand bar — the real wordmark asset (ui/Logo), not a text stand-in. */}
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/75 backdrop-blur-xl dark:border-white/10 dark:bg-slate-950/75">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
          <Link
            to="/"
            aria-label="ZoikoStream home"
            className="shrink-0 rounded-xl transition duration-150 hover:opacity-85 motion-reduce:transition-none"
          >
            <Logo height="h-6 sm:h-7" />
          </Link>
          <div className="flex items-center gap-2 sm:gap-3">
            {live && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/10 px-3 py-1.5 text-xs font-semibold text-rose-600 ring-1 ring-rose-500/20 dark:text-rose-400">
                <FiRadio className="animate-pulse" aria-hidden /> <span className="hidden sm:inline">Live now</span><span className="sm:hidden">Live</span>
              </span>
            )}
            <button
              type="button"
              onClick={handleLeaveEvent}
              className="rounded-xl px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-white/10 dark:hover:text-white"
            >
              Leave Event
            </button>
            <button
              onClick={toggle}
              className="grid h-11 w-11 place-items-center rounded-xl text-slate-500 transition duration-150 hover:bg-slate-100 hover:text-slate-900 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white"
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              title={theme === "dark" ? "Switch to light" : "Switch to dark"}
            >
              {theme === "dark" ? <FiSun className="text-lg" /> : <FiMoon className="text-lg" />}
            </button>
          </div>
        </div>
      </header>

      {/* Top section: banner */}
      <WatchHeader event={event} viewers={viewers} />

      {/* Main layout: player + info (70%) / chat panel (30%).
          Explicit row/column placement rather than nesting the info card inside the left
          column: on mobile that ordering put the whole About/Speakers/Schedule card between
          the video and the chat. Here the stack reads player → reactions → chat → info on
          small screens, and stays two-column from lg up. */}
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {/* Outside the scheduled start_time/end_time window: no video, no chat — just the
            notice. The host's own broadcast/console is unaffected by this (see watch_event). */}
        <div className={`grid grid-cols-1 items-start gap-6 ${timeGated ? "" : "lg:grid-cols-[minmax(0,1fr)_minmax(320px,380px)]"}`}>
          <div className="min-w-0 space-y-4 lg:col-start-1 lg:row-start-1">
            {mustIdentify ? (
              <RegistrationGate
                eventId={eventId} eventTitle={event.name}
                onRegistered={(token) => { setRegToken(token); fetchWatch(); }}
              />
            ) : ended ? (
              <VideoPlayer event={event} viewers={viewers} watch={watch} />
            ) : watch.expired ? (
              <AccessWindowNotice variant="expired" />
            ) : watch.not_started ? (
              <AccessWindowNotice variant="not_started" startTime={watch.start_time} />
            ) : (
              <VideoPlayer event={event} viewers={viewers} watch={watch} />
            )}
            {!timeGated && (
              <ReactionBar
                reactions={panel.reactions}
                onReact={(key) => sendLive("reaction.add", { key })}
                disabled={liveStatus !== "open"}
              />
            )}
          </div>

          {/* row-span-2 so the panel's grid area covers the player AND the info card —
              without it `sticky` has no travel and the info card scrolls past dead space. */}
          {!timeGated && (
            <WatchPanel
              className="h-[70vh] min-w-0 lg:col-start-2 lg:row-start-1 lg:row-span-2 lg:sticky lg:top-20 lg:h-[calc(100vh-6rem)]"
              messages={panel.messages}
              typing={panel.typing}
              questions={panel.questions}
              polls={panel.polls}
              send={sendLive}
              identified={identified}
              eventId={eventId}
              onIdentified={setRegToken}
              connected={liveStatus === "open"}
            />
          )}

          <div className="min-w-0 lg:col-start-1 lg:row-start-2">
            <EventInfo event={event} />
          </div>
        </div>
      </main>

      <footer className="border-t border-slate-200 py-6 text-center text-xs text-slate-400 dark:border-white/10">
        © 2024 ZoikoStream. All rights reserved.
      </footer>
    </div>
  );
}
