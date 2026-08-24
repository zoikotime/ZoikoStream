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
import api, { errMsg } from "../../api";
import WatchHeader from "../../components/watch/WatchHeader";
import VideoPlayer from "../../components/watch/VideoPlayer";
import WatchPanel from "../../components/watch/WatchPanel";
import EventInfo from "../../components/watch/EventInfo";
import ReactionBar from "../../components/watch/ReactionBar";
import RegistrationGate from "../../components/watch/RegistrationGate";
import AccessWindowNotice from "../../components/watch/AccessWindowNotice";
import FeedbackModal from "../../components/common/FeedbackModal";
import Spinner from "../../ui/Spinner";
import Logo from "../../ui/Logo";
import { notify } from "../../ui/Toast";
import { playAlertChime, unlockAudio } from "../../utils/sound";

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
  // This connection's own identity (server/app/services/moderation.py snapshot's "you") —
  // how this viewer recognizes ITS OWN row in `participants` (am I on stage right now?)
  // and matches a broadcast session.removed envelope against itself rather than reacting
  // to somebody else being removed.
  you: null,
  // Set once a session/removed envelope (see services/moderation.py _participant_action,
  // op remove/ban) names THIS identity. The server closes the socket right after sending
  // it, so this never clears itself — the viewer has actually been removed.
  removed: null,
};

function liveReducer(state, env) {
  const { channel, type, data } = env;

  switch (`${channel}/${type}`) {
    case "moderator/snapshot":
      return {
        ...LIVE_EMPTY,
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
        you: data.you || null,
      };

    case "session/removed":
      // Broadcast to every connection on the event (see services/bus.py "session"
      // channel) — only the one whose identity matches is actually being told anything.
      if (!state.you || data.identity !== state.you.identity) return state;
      return { ...state, removed: { reason: data.reason || "You were removed from this event." } };

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

  const [watch, setWatch] = useState(null);
  const [notFound, setNotFound] = useState(false);
  const [blockedReason, setBlockedReason] = useState(null);
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
    setBlockedReason(null);
    setLoading(true);
  }

  const fetchWatch = useCallback(() => {
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
      .catch((e) => {
        // A 403 here means the visitor was recognized but refused (private event, or an
        // invite link already claimed by another device) — worth a real reason, not the
        // generic "not found" a stranger with no token at all should see.
        if (e?.response?.status === 403) setBlockedReason(errMsg(e));
        setNotFound(true);
      })
      .finally(() => setLoading(false));
  }, [eventId, regToken, linkToken, setRegToken, setLinkToken]);

  const [panel, dispatchPanel] = useReducer(liveReducer, LIVE_EMPTY);
  // Visual "new activity" alert per WatchPanel tab — independent of the message/question/
  // poll counts, which never reset and so can't say "something NEW happened since you last
  // looked". Set true when a host action lands for a tab the viewer isn't currently on;
  // cleared by WatchPanel the moment that tab is opened. Sound is fire-and-forget
  // (playAlertChime in onLiveEnvelope below); this is the visible half of the same alert.
  const [alerts, setAlerts] = useState({ chat: false, qa: false, polls: false });
  const markAlert = useCallback((tabKey) => setAlerts((a) => ({ ...a, [tabKey]: true })), []);
  const clearAlert = useCallback((tabKey) => setAlerts((a) => (a[tabKey] ? { ...a, [tabKey]: false } : a)), []);
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
    // The host ending (or emergency-stopping) the broadcast lands here as
    // broadcast/broadcast.update with the session's new status — normally the signal
    // that the event just ended. Without re-fetching, `watch.status` (and therefore
    // canStream/isEnded in VideoPlayer) stays stuck at "live": the LiveKit room drops
    // and useLiveKitViewer just retries a now-dead room forever ("Reconnecting…") until
    // it gives up, instead of showing the real "This event has ended" state.
    //
    // That live push can be missed outright: useEventStream reconnects on any drop
    // (network blip, backgrounded tab, laptop sleep) and the exact moment the host ends
    // the stream is exactly when a viewer's socket is most likely to be mid-reconnect. A
    // missed push used to mean staying stuck until a manual refresh (which re-runs
    // fetchWatch from scratch). But every reconnect already gets a fresh opening
    // moderator/snapshot with the same up-to-date broadcast.status baked in — checking it
    // too means a reconnect alone repairs the stale "live" view, no refresh needed.
    const endedSignal =
      (env.channel === "broadcast" && env.type === "broadcast.update" && env.data?.status === "ended") ||
      (env.channel === "moderator" && env.type === "snapshot" && env.data?.broadcast?.status === "ended");
    if (endedSignal) {
      fetchWatch();
    }
    // Live sound + toast + tab-badge alert for host/moderator-initiated actions — chat,
    // Q&A, polls, announcements — so a viewer notices without having the chat panel
    // focused or the sound on. `actor_role` (server/app/services/moderation.py
    // _actor_role) is "host" for anyone who can moderate the event (host, moderator,
    // org_admin) since a viewer's own actions are never notified back to itself.
    //
    // THE BUG THIS FIXES: `fromHost` used to require actor_role === "host" || "moderator"
    // exactly, but the server never sent that field at all — so the chat notification here
    // never fired, and NONE of these four had a sound or a visible alert, only whichever
    // ones happened to already be unconditional got a toast.
    const fromHost = env.data?.actor_role === "host";
    if (env.channel === "chat" && env.type === "message.new" && fromHost) {
      playAlertChime();
      markAlert("chat");
      notify.alert(`${env.data.name}: ${env.data.text}`);
    }
    if (env.channel === "poll" && env.type === "poll.new") {
      playAlertChime();
      markAlert("polls");
      notify.alert("Host started a new poll");
    }
    if (env.channel === "qa" && env.type === "question.update" && env.data?.status === "answered") {
      playAlertChime();
      markAlert("qa");
      notify.alert("Host answered a question");
    }
    if (env.channel === "announcement" && env.type === "announcement.new") {
      playAlertChime();
      notify.alert(env.data?.text ? `Announcement: ${env.data.text}` : "New announcement from the host");
    }
    // This viewer's own promote/demote — a toast is the only signal they'd otherwise get
    // that their mic just started (or stopped) being published; VideoPlayer's on-stage
    // badge only shows up on the video itself, which they might not be looking at.
    if (env.channel === "participants" && env.type === "participant.update" && panel.you && env.data.identity === panel.you.identity) {
      const wasOnStage = Boolean(panel.participants[env.data.identity]?.on_stage);
      const nowOnStage = Boolean(env.data.on_stage);
      if (nowOnStage && !wasOnStage) notify.success("The host invited you on stage — your mic is now live.");
      else if (!nowOnStage && wasOnStage) notify.info("You're no longer on stage.");
    }
    dispatchPanel(env);
  }, [fetchWatch, markAlert, panel.you, panel.participants]);
  const {
    status: liveStatus,
    send: sendLive,
    disconnect: disconnectLive,
  } = useEventStream(eventId, onLiveEnvelope, regToken, linkToken);

  // This viewer's own presence record, once the snapshot has named it — whether they're
  // currently invited on stage (mic live, real LiveKit publish grant; see
  // services/moderation.py participant.role/participant.stage and useLiveKitViewer.js).
  const isOnStage = Boolean(
    panel.you && panel.participants[panel.you.identity]?.on_stage
  );

  useEffect(() => {
    // fetchWatch only sets state inside its own .then/.catch/.finally (an async
    // continuation, exactly what this rule asks for) — the lint rule's static analysis
    // just can't see through that indirection to tell this apart from a synchronous
    // setState call, which is the actual anti-pattern it exists to catch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchWatch();
  }, [fetchWatch]);

  // Warm up the notification chime's AudioContext on this page's first click/keypress —
  // browsers refuse to play audio before a user gesture. Mirrors useLiveEvent.js's own
  // call for the host/moderator console; without it here, a viewer's FIRST host-action
  // alert (before they've clicked anything on this page) would silently not sound.
  useEffect(() => {
    unlockAudio();
  }, []);

  // Private/unlisted events must never be indexable — a leaked or guessed watch URL
  // showing up in search results defeats the whole point of restricting access. There's no
  // SSR here, so this can't be a response header; a robots meta tag is the SPA-native
  // equivalent, and Google (unlike most crawlers) does execute JS before indexing, so it's
  // still effective for the crawler that matters most. Injected directly rather than via a
  // Helmet-style library — one tag doesn't earn a new dependency. Removed on unmount so a
  // later public page in the same session never inherits a stale noindex.
  useEffect(() => {
    if (!watch || watch.visibility === "public") return undefined;
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, [watch]);

  // Keeps a page opened before the host goes live from needing a manual refresh. Once
  // live, polling stays off — the live socket's own broadcast.update "ended" signal
  // (onLiveEnvelope above) is what triggers the one fetchWatch() that matters, instead of
  // a timer racing it.
  useInterval(fetchWatch, POLL_MS, Boolean(watch) && watch.status !== "live" && watch.status !== "ended");

  const event = watch ? watchToMockEvent(watch) : null;
  const live = event?.status === "Live";
  const ended = event?.status === "Completed";
  const identified = !!(user || regToken || linkToken);
  // "Leave Event" opens the feedback modal first (the live socket is still connected at
  // that point, so the modal's onSubmit can ride it) — the actual disconnect/token-clear/
  // redirect that used to fire immediately on click now happens in finishLeave, which the
  // modal calls via onDone whether the viewer submitted or skipped.
  const [showLeaveFeedback, setShowLeaveFeedback] = useState(false);
  const handleLeaveEvent = useCallback(() => {
    setShowLeaveFeedback(true);
  }, []);
  const finishLeave = useCallback(() => {
    setShowLeaveFeedback(false);
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
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {blockedReason || "This event could not be found."}
          </p>
          <Link to="/" className="mt-2 inline-block text-sm font-medium text-emerald-600 hover:text-emerald-500 dark:text-emerald-400">
            Back to home
          </Link>
        </div>
      </div>
    );

  // The host removed/banned this viewer (services/moderation.py participant.remove /
  // participant.ban -> "session"/"removed", handled server-side by closing this socket —
  // see routers/live.py). The live connection is already gone at this point; this is
  // purely the honest "here's why the page just stopped working" the viewer is owed,
  // instead of a stream that silently freezes with no explanation.
  if (panel.removed)
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 px-4 dark:bg-slate-950">
        <div className="max-w-sm text-center">
          <p className="text-lg font-semibold text-slate-900 dark:text-white">You&apos;ve left this event</p>
          <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{panel.removed.reason}</p>
          <Link
            to="/"
            className="mt-4 inline-block rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500"
          >
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
              <VideoPlayer event={event} viewers={viewers} watch={watch} onStage={isOnStage} />
            )}
            {/* reactions_enabled is False only for a memorial-category event (doc Sec.
                11.3/19, non-waivable LE-AC-16) — computed server-side in routers/events.py's
                watch_event, since reactions have no persisted Event column of their own. */}
            {!timeGated && watch.reactions_enabled && (
              <ReactionBar
                reactions={panel.reactions}
                onReact={(key) => sendLive("reaction.add", { key })}
                disabled={liveStatus !== "open"}
              />
            )}
          </div>

          {/* row-span-2 so the panel's grid area covers the player AND the info card —
              without it `sticky` has no travel and the info card scrolls past dead space.
              WatchPanel itself renders nothing once none of chat/qa/polls are enabled — a
              memorial event has all three off, so no empty tab strip shows either. */}
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
              alerts={alerts}
              onTabView={clearAlert}
              enabledTabs={{ chat: watch.chat_enabled, qa: watch.qa_enabled, polls: watch.polls_enabled }}
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

      <FeedbackModal
        open={showLeaveFeedback}
        role="viewer"
        onSubmit={(payload) => sendLive("feedback.submit", payload)}
        onDone={finishLeave}
      />
    </div>
  );
}