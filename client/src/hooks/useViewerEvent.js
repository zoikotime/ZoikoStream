import { useCallback, useReducer, useRef } from "react";
import api from "../api";
import useApi from "./useApi";
import useEventStream from "./useEventStream";
import { notify } from "../ui/Toast";

// The whole data layer for the attendee watch page. Same shape as useLiveEvent (the consoles'
// hook), but over the VIEWER projection of the socket rather than the console one.
//
// Two sources, deliberately split:
//   * GET /events/:id/viewer — everything that doesn't change while the page is open
//     (event, organizer, security posture, registration state, support links). One cacheable
//     read, no polling.
//   * the existing live socket — everything that does (chat, Q&A, polls, announcements,
//     reactions, viewer count, live status, countdown).
//
// The server decides what an attendee may receive (services/moderation.viewer_snapshot and
// viewer_envelope), so this reducer handles only the projected envelopes. Anything the console
// gets and an attendee doesn't never arrives here to be ignored — which is why there is no
// participants roster, no analytics block and no activity feed below.

const EMPTY = {
  ready: false,
  identity: null,      // my own id, so my messages/questions/reactions are distinguishable
  viewers: null,       // null = not reported yet; 0 is a real answer and must render as 0
  status: null,        // event lifecycle status as the socket last reported it
  recording: false,
  countdownUntil: null,
  recovering: false,   // media room dropped, publisher expected back
  // Participation. Every one of these already flowed to attendees on the socket; the page simply
  // had no interface for them.
  messages: [],
  questions: [],
  polls: [],
  announcements: [],
  speakers: [],
  features: {},        // chat/qa/polls/raise_hand switches, as the host set them
  reactions: {},       // running totals per emoji
  resources: [],       // files shared with the audience
  canChat: true,
};

const upsert = (list, item, key = "id") => {
  const i = list.findIndex((x) => x[key] === item[key]);
  if (i === -1) return [...list, item];
  const next = list.slice();
  next[i] = { ...next[i], ...item };
  return next;
};
const drop = (list, item, key = "id") => list.filter((x) => x[key] !== item[key]);

function reducer(state, env) {
  const { channel, type, data } = env;
  switch (`${channel}/${type}`) {
    case "moderator/snapshot":
      return {
        ...EMPTY,
        ready: true,
        identity: data.identity || null,
        viewers: data.audience?.viewers ?? null,
        status: data.stream?.status ?? data.event?.status ?? null,
        recording: !!data.event?.recording,
        countdownUntil: data.countdown_until || null,
        messages: (data.messages || []).filter((m) => m.status !== "deleted"),
        questions: data.questions || [],
        polls: data.polls || [],
        announcements: data.announcements || [],
        speakers: data.speakers || [],
        features: data.event?.features || {},
        reactions: data.reactions || {},
        resources: data.resources || [],
      };
    case "analytics/analytics.tick":
      return { ...state, viewers: data.viewers ?? state.viewers };
    case "moderator/room.status":
      return {
        ...state,
        recovering: !!data.recovering,
        status: data.live ? "live" : state.status,
      };
    case "moderator/recording.status":
      return { ...state, recording: !!data.recording };
    case "broadcast/broadcast.update":
      return { ...state, recovering: false, status: data.status || state.status };
    case "broadcast/broadcast.countdown":
      return { ...state, countdownUntil: data.until };

    // ── chat ────────────────────────────────────────────────────────────────
    // A `pending` message is one the automatic filters held for review. It is broadcast to the
    // room anyway (the server does not withhold it), so the attendee page shows it — hiding
    // other people's held messages would make the room look quieter than it is, and hiding the
    // AUTHOR's own would make them think their message vanished.
    case "chat/message.new":
      return { ...state, messages: [...state.messages, data] };
    case "chat/message.update":
      return { ...state, messages: upsert(state.messages, data) };
    case "chat/message.delete":
      return { ...state, messages: drop(state.messages, data) };

    // ── Q&A ─────────────────────────────────────────────────────────────────
    case "qa/question.new":
      return { ...state, questions: [...state.questions, data] };
    case "qa/question.update":
      return { ...state, questions: upsert(state.questions, data) };
    case "qa/question.delete":
      return { ...state, questions: drop(state.questions, data) };

    // ── polls ───────────────────────────────────────────────────────────────
    case "poll/poll.new":
      return { ...state, polls: [data, ...state.polls] };
    case "poll/poll.update":
      return { ...state, polls: upsert(state.polls, data) };
    case "poll/poll.delete":
      return { ...state, polls: drop(state.polls, data) };

    // ── announcements ───────────────────────────────────────────────────────
    case "announcement/announcement.new":
      return { ...state, announcements: [data, ...state.announcements] };
    case "announcement/announcement.delete":
      return { ...state, announcements: drop(state.announcements, data) };

    // ── reactions ───────────────────────────────────────────────────────────
    // Only the TOTALS live in state. The floating animation is handled by the component from the
    // same envelope, because a list of in-flight reactions in reducer state would re-render the
    // whole page for every tap in a 10,000-person room.
    case "reaction/reaction.new":
      return { ...state, reactions: data.totals || state.reactions };

    // ── shared resources ────────────────────────────────────────────────────
    case "presentation/resource.update":
      return {
        ...state,
        resources: data.shared
          ? upsert(state.resources, data.resource)
          : drop(state.resources, data.resource),
      };

    // The host's live feature switches (chat off, slow mode, reactions off). Attendees receive
    // `broadcast.update` narrowed to a status, so the settings themselves never arrive — what
    // DOES arrive is a rejection when they try. `canChat` records that so the composer can
    // explain itself instead of failing silently on every keystroke.
    case "local/chat.blocked":
      return { ...state, canChat: false, chatBlockedReason: data.message };
    case "local/chat.allowed":
      return { ...state, canChat: true, chatBlockedReason: null };

    default:
      return state;
  }
}

export default function useViewerEvent(eventId) {
  const [live, dispatch] = useReducer(reducer, EMPTY);
  // In-flight reactions for the floating animation. A ref + a subscriber callback rather than
  // reducer state: at 10k attendees this fires many times a second, and every one would otherwise
  // re-render the entire page.
  const reactionSubs = useRef(new Set());

  const { data: landing, loading, error, reload } = useApi(() =>
    eventId
      ? api.get(`/events/${eventId}/viewer`).then((r) => r.data)
      : Promise.resolve(null)
  );

  const onEnvelope = useCallback((env) => {
    // A moderator's private reply to this viewer — the answer to their raised hand. Narrowed to
    // one recipient server-side (routers/live.py), so anything that arrives here is ours.
    if (env.type === "participant.notice") {
      notify.info(`${env.data.from_name}: ${env.data.text}`);
      return;
    }
    // An action the server refused. The common one is chat being turned off mid-event, which the
    // composer needs to reflect rather than swallow.
    if (env.channel === "moderator" && env.type === "error") {
      const message = env.data?.message || "That didn't go through.";
      if (/chat|slow mode|emoji|member/i.test(message)) {
        dispatch({ channel: "local", type: "chat.blocked", data: { message } });
      }
      notify.error(message);
      return;
    }
    if (env.channel === "reaction" && env.type === "reaction.new") {
      // Fan to the animator first, then let the reducer record the totals.
      reactionSubs.current.forEach((fn) => fn(env.data));
    }
    dispatch(env);
  }, []);

  const stream = useEventStream(eventId, onEnvelope);

  /** Subscribe to raw reaction events for the floating animation. Returns an unsubscribe. */
  const onReaction = useCallback((fn) => {
    reactionSubs.current.add(fn);
    return () => reactionSubs.current.delete(fn);
  }, []);

  // The socket is the fresher source once connected, but it only exists while the page is
  // open — before its first frame the landing read is all there is. Resolving that here
  // keeps every consumer from writing the same `live.status ?? landing.event.status` dance.
  const status = live.status || landing?.event?.status || null;

  return {
    landing,
    loading,
    error,
    reload,
    live: { ...live, status, isLive: status === "live" },
    connection: { status: stream.status, latency: stream.latency, attempt: stream.attempt },
    send: stream.send,
    onReaction,
  };
}
