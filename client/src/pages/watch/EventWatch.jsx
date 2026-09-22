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
import useKeepAwake from "../../hooks/useKeepAwake";
import { controlPlaneNotice } from "./controlPlaneNotice";
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
import ReactionOverlay from "../../components/live/ReactionOverlay";
import useReactionChannel from "../../hooks/useReactionChannel";
import RegistrationGate from "../../components/watch/RegistrationGate";
import AccessWindowNotice from "../../components/watch/AccessWindowNotice";
import FeedbackModal from "../../components/common/FeedbackModal";
import Spinner from "../../ui/Spinner";
import Logo from "../../ui/Logo";
import { notify } from "../../ui/Toast";
import { playAlertChime, unlockAudio } from "../../utils/sound";

// "degraded" is services/broadcast.py's marker for "this event IS live, but the producer's
// media has stopped flowing" (mark_degraded). Leaving it out of this map sent it down the
// "anything else" branch and labelled a live event "Upcoming", which is what put the
// "PREVIEW" badge over a broadcast in progress and made every isLive-derived affordance on
// this page (the LIVE chip, the viewer counter, the reaction bar) disappear mid-event.
// Media health is reported separately, by media_status.
const STATUS_LABEL = { live: "Live", degraded: "Live", ended: "Completed" }; // else -> "Upcoming"

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
    speakers: [],
    category: watch.category || null,
    endISO: watch.end_time || null, // raw, for the countdown card and the .ics DTEND
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
  // NOTE: there is deliberately no `reactions` here. Reactions are ephemeral events, not
  // panel state — they arrive as `reactions`/`reaction.burst` envelopes and go straight to
  // the overlay through hooks/useReactionChannel.js, so nothing about them is ever stored,
  // counted or replayed. See onLiveEnvelope below.
  // Read-only for a viewer — the value the host set via the moderation console, surfaced
  // so the chat panel can show a status pill. Never sent by this socket, only received.
  slowModeSeconds: null,
  // This connection's own identity (server/app/services/moderation.py snapshot's "you") —
  // how this viewer recognizes ITS OWN row in `participants` (am I on stage right now?)
  // and matches a broadcast session.removed envelope against itself rather than reacting
  // to somebody else being removed.
  you: null,
  // Set once a session/removed envelope (see services/moderation.py _participant_action,
  // op remove/ban) names THIS identity. The server closes the socket right after sending
  // it, so this never clears itself — the viewer has actually been removed.
  removed: null,
  // Set when the host asks THIS viewer to turn their microphone on (services/moderation.py
  // _participant_request_unmute). A request, not a state change: nothing about this person
  // is different until they act on it, so it carries no presence patch and clears itself
  // the moment they answer.
  unmuteRequest: null,
};

// Exported for the reaction regression tests only (see EventWatch.reactions.test.jsx) —
// the page itself still drives this through useReducer below. Same convention
// hooks/useLiveEvent.js already uses for its own reducer and initial state, so the live
// state transitions are assertable without standing up a socket.
//
// The disable is the cost of that: react-refresh wants a component file to export nothing
// but components, and it is right that the tidier home for these is a sibling module (the
// way controlPlaneNotice.js was split out of this very file). Moving the whole reducer is a
// bigger change than the reaction fix it would be riding along with, so it stays here for
// now — the only consequence is that editing THIS file does a full reload instead of a hot
// swap while the dev server is running.
// eslint-disable-next-line react-refresh/only-export-components
export { LIVE_EMPTY, liveReducer };

// `your_vote` is per-connection and DELIBERATELY absent from the public poll broadcasts:
// services/moderation.py::poll_out fills it only for the per-socket snapshot, and its
// docstring is explicit that "callers that build a public broadcast (poll.new/update/delete)
// simply omit it and every viewer gets your_vote: None". Merging that null over the value the
// snapshot — or this viewer's own vote — established would silently un-vote them the moment
// anyone else voted and a new tally arrived. So a blank here means "not included", never
// "no vote", and is dropped before the merge.
const keepingOwnVote = (data) => {
  if (data.your_vote != null) return data;
  const rest = { ...data };
  delete rest.your_vote;
  return rest;
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
        slowModeSeconds: data.slow_mode_seconds || null,
        you: data.you || null,
      };

    case "session/unmute.requested":
      // Broadcast to the whole event like every other session envelope; only the matching
      // identity is being spoken to.
      if (!state.you || data.identity !== state.you.identity) return state;
      return { ...state, unmuteRequest: { askedBy: data.asked_by || "The host", at: Date.now() } };

    case "session/unmute.answered":   // local-only, dispatched when the viewer answers
      return { ...state, unmuteRequest: null };

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
      return {
        ...state,
        polls: state.polls.map((p) => (p.id === data.id ? { ...p, ...keepingOwnVote(data) } : p)),
      };
    case "poll/poll.delete":
      return { ...state, polls: state.polls.filter((p) => p.id !== data.id) };
    // This viewer's OWN ballot, applied optimistically by the send wrapper below so the vote
    // buttons switch to the result view without waiting for the round trip.
    //
    // It lives here rather than as local state inside WatchPanel's Poll component for one
    // specific reason: the server answers a vote with a public `poll.update` carrying new
    // tallies, and a component holding its own copy then had to reconcile the two — which is
    // exactly the effect this replaced. One field, one owner.
    case "local/poll.vote":
      return {
        ...state,
        polls: state.polls.map((p) => (p.id === data.id ? { ...p, your_vote: data.option } : p)),
      };
    // `reactions/reaction.burst` intentionally has NO case: a reaction must not become
    // reducer state. Dispatching it here would re-render the whole page (and the chat
    // list, and the player) once per tap in the audience, for something that is already
    // handled in onLiveEnvelope and lives entirely inside the overlay.
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
  // ── Where the registration credential lives ────────────────────────────────────────
  //
  // The credential itself is unchanged: an opaque server-signed JWT from POST /register that
  // carries the event id and is verified per request (security.decode_registration_payload),
  // so it cannot be edited into a pass for a different event. Nothing here stores a name, an
  // email, or a "registered: true" flag — none of those would be proof of anything.
  //
  // What changed is the DURATION, which is now the viewer's choice:
  //   localStorage    "Remember me for this event" was ticked — survives a browser restart.
  //   sessionStorage  it was not — this tab's visit only, gone when the tab closes.
  //
  // Both are read back, localStorage first, so a remembered visitor is recognised before a
  // session-only one. The key is per-event (`zk_reg_<eventId>`), which is what keeps a
  // credential for Event A from doing anything at all on Event B.
  const [regToken, setRegTokenState] = useState(() => {
    try {
      return localStorage.getItem(`zk_reg_${eventId}`)
        || sessionStorage.getItem(`zk_reg_${eventId}`);
    } catch {
      return null;   // private mode with storage blocked: no saved credential is the safe answer
    }
  });
  const setRegToken = useCallback((token, remember = false) => {
    try {
      const store = remember ? localStorage : sessionStorage;
      store.setItem(`zk_reg_${eventId}`, token);
      // Never leave the same credential in both: an unticked re-registration must not keep
      // a persistent copy left over from an earlier ticked one.
      (remember ? sessionStorage : localStorage).removeItem(`zk_reg_${eventId}`);
    } catch { /* storage unavailable; the credential still works for this page load */ }
    setRegTokenState(token);
  }, [eventId]);
  const [linkToken, setLinkTokenState] = useState(() => localStorage.getItem(`zk_link_${eventId}`));
  const setLinkToken = useCallback((token) => {
    localStorage.setItem(`zk_link_${eventId}`, token);
    setLinkTokenState(token);
  }, [eventId]);

  const [watch, setWatch] = useState(null);
  // The registration credential the CURRENT `watch` payload was fetched with, or null if it
  // was fetched anonymously. Only meaningful to the stale-credential check further down.
  const [watchedReg, setWatchedReg] = useState(null);
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

  // `override` carries a credential the caller has IN HAND but that has not reached state
  // yet. That is the whole of the one-click registration fix: RegistrationGate hands back a
  // token and this function used to read `regToken` out of its closure, which React had not
  // updated (a setState is not synchronous), so the very request meant to prove the
  // registration went out with no credential at all. The server answered "not registered",
  // the form stayed up, and a second click — a second POST, a second attendee row — was the
  // only way in. Passing the token explicitly removes the dependency on a committed render.
  const fetchWatch = useCallback((override = {}) => {
    // A host-invited or self-registered link carries the access token in the URL
    // (?reg=... or ?link=...) — save it locally so a refresh (or a later visit with no
    // query string) keeps working without the visitor needing to click the shared link again.
    const params = new URLSearchParams(window.location.search);
    const urlReg = params.get("reg");
    const urlLink = params.get("link");
    if (urlReg) setRegToken(urlReg);
    if (urlLink) setLinkToken(urlLink);
    const reg = override.reg || urlReg || regToken;
    const link = override.link || urlLink || linkToken;
    const accessParams = { ...(reg ? { reg } : {}), ...(link ? { link } : {}) };
    api
      .get(`/events/${eventId}/watch`, { params: Object.keys(accessParams).length ? accessParams : undefined })
      .then(({ data }) => {
        // Which credential this payload was actually judged against — read by the
        // stale-credential check below, which must never condemn a token the server was
        // not shown.
        setWatchedReg(reg || null);
        setWatch((prev) => {
          // create_stream_token() mints a FRESH JWT on every call, so a naive setWatch(data)
          // handed useLiveKitViewer a brand-new `token` on every poll — and that hook keys
          // its connect effect on the token, so the viewer tore down and rebuilt its LiveKit
          // room every POLL_MS. Keep the token we already hold for the same room: it is
          // still valid (services/livekit.py's PLAYBACK_TOKEN_TTL), and everything else in
          // the payload (status, media_status, recording_url) still refreshes normally.
          if (prev?.livekit_token && data?.livekit_token && prev.room && prev.room === data.room) {
            return { ...data, livekit_token: prev.livekit_token, livekit_url: prev.livekit_url };
          }
          return data;
        });
      })
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
  // Floating reactions over the player (components/live/ReactionOverlay.jsx). The channel
  // is a stable pub/sub seam, not state: a reaction from anyone in the audience re-renders
  // the overlay and nothing else on this page. See hooks/useReactionChannel.js.
  const reactionChannel = useReactionChannel();
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
    // One reaction from someone in the audience — float it over the player. Straight to
    // the overlay, never into the reducer: no count, no aggregation, and nothing kept, so
    // a reconnect (which re-sends the full snapshot, and no reaction history — the server
    // has none to send) cannot replay reactions that already happened.
    //
    // The event-id check is belt and braces. bus.publish is already scoped per event
    // (server/app/services/bus.py), so an envelope from another event cannot reach this
    // socket; this makes a reaction from event A appearing on event B impossible on the
    // client side too, rather than trusting the transport alone.
    if (env.channel === "reactions" && env.type === "reaction.burst") {
      if (!env.data?.event_id || env.data.event_id === eventId) reactionChannel.emit(env.data);
    }
    dispatchPanel(env);
  }, [fetchWatch, markAlert, panel.you, panel.participants, eventId, reactionChannel]);
  const {
    status: liveStatus,
    closeReason: liveCloseReason,
    send: sendLive,
    disconnect: disconnectLive,
  } = useEventStream(eventId, onLiveEnvelope, regToken, linkToken);

  // The panel's send, wrapped so a poll vote also lands in reducer state at once. The server
  // answers with a public `poll.update` whose `your_vote` is blank for everybody (see
  // keepingOwnVote above), so without this the viewer would watch the tallies move while the
  // vote buttons sat there as if they had not voted. Everything else passes straight through.
  //
  // Dispatched unconditionally, exactly as the previous local `choice` state was set before
  // the send: a refused vote leaves the optimistic value showing and is surfaced by the
  // rejection toast, which is the behaviour this replaces rather than a new one.
  const sendPanel = useCallback(
    (type, payload) => {
      if (type === "poll.vote") {
        dispatchPanel({ channel: "local", type: "poll.vote", data: payload });
      }
      return sendLive(type, payload);
    },
    [sendLive]
  );

  // An honest word about the CONTROL socket, kept strictly separate from media state.
  // liveStatus was previously consumed in only two places — a disabled control and the chat
  // panel's connected dot — and closeReason was never read at all, so a viewer whose socket
  // the server had explicitly REFUSED ("Invalid or expired session", close 1008, confirmed
  // against production) sat on a normal-looking page with no explanation and no way to act.
  // Deliberately does NOT touch the player: chat/polls/Q&A being offline says nothing about
  // whether audio and video are arriving, and conflating them is what made a dead control
  // plane read as "the event is in preview".
  const controlNotice = controlPlaneNotice(liveStatus, liveCloseReason);

  // This viewer's own presence record, once the snapshot has named it — whether they're
  // currently invited on stage (mic live, real LiveKit publish grant; see
  // services/moderation.py participant.role/participant.stage and useLiveKitViewer.js).
  const isOnStage = Boolean(
    panel.you && panel.participants[panel.you.identity]?.on_stage
  );
  // Same "derive my own state from panel.you + panel.participants" pattern as isOnStage
  // above. `participant.hand` is an existing viewer-callable action (server/app/services/
  // moderation.py VIEWER_ACTIONS/_participant_hand) already displayed on the moderator
  // console's participants panel — this just gives viewers a button that calls it.
  const handRaised = Boolean(
    panel.you && panel.participants[panel.you.identity]?.hand
  );
  const toggleHand = useCallback(() => {
    sendLive("participant.hand", { raised: !handRaised });
  }, [sendLive, handRaised]);

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
  // Also polls while "degraded": that is the one state whose media_status can change under
  // the viewer without any socket frame (the sampler flips it server-side), and it is what
  // drives the "host's connection dropped" / recovered wording in the player. Safe now that
  // fetchWatch above preserves the LiveKit token, so a poll no longer reconnects the room.
  useInterval(fetchWatch, POLL_MS, Boolean(watch) && watch.status !== "live" && watch.status !== "ended");

  const event = watch ? watchToMockEvent(watch) : null;
  const live = event?.status === "Live";
  // Watching is 40 minutes of not touching the screen, which is 39 minutes past the point a
  // phone dims and locks. Tied to `live` rather than to the page: an upcoming or completed
  // event is a page to read, and holding a wake lock over one would just drain the battery.
  useKeepAwake(live);
  const ended = event?.status === "Completed";
  const identified = !!(user || regToken || linkToken);
  // "Leave Event" opens the feedback modal first (the live socket is still connected at
  // that point, so the modal's onSubmit can ride it) — the actual disconnect/token-clear/
  // redirect that used to fire immediately on click now happens in finishLeave, which the
  // modal calls via onDone whether the viewer submitted or skipped.
  // Which event we have already discarded a rejected credential for, so the check above
  // cannot loop.
  const [clearedFor, setClearedFor] = useState(null);
  // participant.state is the existing action for a client reporting its OWN media state
  // (services/moderation._participant_state), scoped server-side to ctx.identity — so this
  // can only ever describe this viewer, never anyone else. It had no sender at all until
  // now, which is why a speaker's self-mute never reached the host.
  //
  // useCallback, not an inline arrow: the hook keeps it in a dependency array, and a fresh
  // function every render would re-run the demote effect that depends on it.
  const reportMuted = useCallback(
    (muted) => sendPanel("participant.state", { muted }),
    [sendPanel]
  );

  const [showLeaveFeedback, setShowLeaveFeedback] = useState(false);
  const handleLeaveEvent = useCallback(() => {
    setShowLeaveFeedback(true);
  }, []);
  const finishLeave = useCallback(() => {
    setShowLeaveFeedback(false);
    disconnectLive();

    // "Forget me on this device" for this event, and only this event. Both stores, because
    // the credential lives in one or the other depending on the Remember me choice.
    try {
      localStorage.removeItem(`zk_reg_${eventId}`);
      sessionStorage.removeItem(`zk_reg_${eventId}`);
      localStorage.removeItem(`zk_link_${eventId}`);
      sessionStorage.removeItem(`zk_link_${eventId}`);
    } catch { /* storage unavailable */ }
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
  // Whether the form is needed is the SERVER's answer, not ours. `watch.registered` is
  // computed by GET /events/{id}/watch from the credential it just verified (routers/
  // events.py: `registered or not ev.registration_required`), so:
  //
  //   • a valid saved credential  -> registered true  -> straight in, no form
  //   • an expired//tampered/wrong-event one -> registered FALSE -> the form comes back
  //
  // The old condition asked whether a token STRING existed in this browser, which meant a
  // stale credential silently skipped the form and then stranded the viewer on a page that
  // would never hand out a stream token. A saved value that the server rejects is not a
  // registration, and it is now treated as none.
  // Note there is NO `!user` here. Holding a ZoikoStream login is not a registration for
  // this event: the server already admits the people for whom it genuinely is one (the
  // event's own org members, an invited guest, a valid access link) by answering
  // registered=true. Anyone else — including a signed-in member of some other organization —
  // is asked, which is what "the backend confirms it for THIS event" has to mean.
  const mustIdentify = Boolean(
    watch && watch.visibility !== "private" && !watch.registered
  );

  // A credential the server did not accept is dead weight; drop it so the next visit starts
  // clean rather than re-presenting something already known to be refused. Derived during
  // render (this repo treats set-state-in-effect as an error) and guarded so it runs once.
  //
  // `watchedReg === regToken` is the half that was missing, and it is why registering once
  // did not merely fail to admit the viewer — it THREW THE NEW TOKEN AWAY. On the render
  // right after registering, `regToken` is the token just issued while `watch` is still the
  // anonymous payload from before it existed, carrying registered=false. Judged on that
  // pairing the fresh credential looked refused, so it was deleted from storage and from
  // state, and the viewer had to register a second time to get in. A credential is only
  // stale if the server was actually SHOWN it and still said no.
  const staleCredential = Boolean(
    regToken && watch && !watch.registered && watchedReg === regToken
  );
  if (staleCredential && clearedFor !== eventId) {
    setClearedFor(eventId);
    try {
      localStorage.removeItem(`zk_reg_${eventId}`);
      sessionStorage.removeItem(`zk_reg_${eventId}`);
    } catch { /* storage unavailable */ }
    setRegTokenState(null);
  }
  // watch.expired means "the scheduled window lapsed before the host ever went live" —
  // it does NOT mean "there's nothing left to show". Ending a broadcast backfills
  // Event.end_time to that moment (services/broadcast.py `ev.end_time = ev.end_time or
  // now`), so `expired` flips true within milliseconds of any normal "End Event" click.
  // Checked here so a real ended-with-replay event is never mistaken for an event that
  // simply expired unwatched.
  const timeGated = Boolean(mustIdentify || (watch?.expired && !ended) || watch?.not_started);

  if (loading) {
    // The registration form cannot flash before validation: `watch` is null until the
    // response lands and mustIdentify requires it. This only names what the wait is FOR
    // when a saved credential is being checked, rather than showing a bare spinner.
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <div className="text-center">
          <Spinner />
          {regToken && (
            <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
              Checking your registration…
            </p>
          )}
        </div>
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

      {controlNotice && (
        <p
          role="status"
          className={
            controlNotice.tone === "error"
              ? "border-b border-rose-200 bg-rose-50 px-4 py-2 text-center text-[13px] font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300"
              : "border-b border-amber-200 bg-amber-50 px-4 py-2 text-center text-[13px] font-medium text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
          }
        >
          {controlNotice.text}
        </p>
      )}

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
                // The token is handed straight to fetchWatch rather than being read back out
                // of state on the next render — that round trip is what cost the viewer a
                // second click, and a second attendee record with it.
                onRegistered={(token, remember) => { setRegToken(token, remember); fetchWatch({ reg: token }); }}
              />
            ) : ended ? (
              <VideoPlayer event={event} viewers={viewers} watch={watch} />
            ) : watch.expired ? (
              <AccessWindowNotice variant="expired" />
            ) : watch.not_started ? (
              <AccessWindowNotice variant="not_started" startTime={watch.start_time} />
            ) : (
              <VideoPlayer
                event={event}
                viewers={viewers}
                watch={watch}
                onStage={isOnStage}
                unmuteRequest={panel.unmuteRequest}
                // participant.state is the existing action for a client reporting its OWN
                // media state (services/moderation._participant_state), scoped server-side
                // to ctx.identity — so this can only ever describe this viewer, never
                // anyone else. It had no sender until now, which is why a speaker's
                // self-mute never reached the host.
                onMuteChange={reportMuted}
                onAnswerUnmute={() =>
                  dispatchPanel({ channel: "session", type: "unmute.answered", data: {} })
                }
              >
                {/* lane="left" is the approved viewer treatment: reactions rise out of the
                    lower-left corner in a narrow stream instead of scattering across the
                    frame, so they never sit over whoever is speaking. Visual only — the
                    channel, the envelope and the reaction bar below are untouched, and the
                    Producer Console keeps its full-width scatter (StudioStage.jsx). */}
                {watch.reactions_enabled && (
                  <ReactionOverlay channel={reactionChannel} lane="left" className="z-30" />
                )}
              </VideoPlayer>
            )}
            {/* reactions_enabled is False only for a memorial-category event (doc Sec.
                11.3/19, non-waivable LE-AC-16) — computed server-side in routers/events.py's
                watch_event, since reactions have no persisted Event column of their own. */}
            {!timeGated && watch.reactions_enabled && (
              <ReactionBar
                onReact={(key) => sendLive("reaction.add", { key })}
                disabled={liveStatus !== "open"}
                handRaised={handRaised}
                onToggleHand={toggleHand}
                raiseHandVisible={Boolean(watch.raise_hand_enabled)}
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
              send={sendPanel}
              identified={identified}
              eventId={eventId}
              // Chat/Q&A identification on a non-registration event. Persisted as it always
              // was — the Remember me choice belongs to the registration form, and quietly
              // downgrading this one to session-only would be an unrequested change.
              onIdentified={(token) => setRegToken(token, true)}
              connected={liveStatus === "open"}
              alerts={alerts}
              onTabView={clearAlert}
              enabledTabs={{ chat: watch.chat_enabled, qa: watch.qa_enabled, polls: watch.polls_enabled }}
              slowModeSeconds={panel.slowModeSeconds}
            />
          )}

          <div className="min-w-0 lg:col-start-1 lg:row-start-2">
            <EventInfo event={event} endISO={event.endISO} viewers={live ? viewers : null} />
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