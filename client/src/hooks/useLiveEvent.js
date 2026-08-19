import { useCallback, useEffect, useMemo, useReducer } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../api";
import useApi from "./useApi";
import useEventStream from "./useEventStream";
import useInterval from "./useInterval";
import { notify } from "../ui/Toast";
import { playAlertChime, unlockAudio } from "../utils/sound";

// The whole data layer for a live event console — event resolution, the socket, and one
// reducer over the server's envelopes.
//
// BOTH consoles use this: the moderator console (audience management) and the host console
// (broadcast control) are two layouts over the same live state, so the reducer lives here
// once instead of in each page. Channels a given console doesn't render simply go unused.
//
// The server sends a full snapshot on every connect, so a reconnect REPLACES state rather
// than patching it — that's what makes a dropped connection self-heal instead of leaving
// panels quietly stale.

const EMPTY = {
  ready: false,
  event: null,
  speakers: [],
  canModerate: false,
  canHost: false,
  livekitEnforced: false,
  participants: [],
  // Backstage roster — every assigned speaker + their ContributorSession, if invited.
  // [{ user_id, name, session }], session is null until an invite exists.
  contributors: [],
  messages: [],
  questions: [],
  polls: [],
  announcements: [],
  activity: [],
  typing: {},              // identity -> { name, until }
  // host console
  broadcast: null,
  recording: null,
  recordings: [],
  analytics: null,
  health: null,
  countdownUntil: null,
  publishToken: null,
  livekitUrl: null,
  recovering: false,
};

const TYPING_TTL = 4000;

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
        event: data.event,
        speakers: data.speakers || [],
        canModerate: data.can_moderate,
        canHost: !!data.can_host,
        livekitEnforced: data.livekit_enforced,
        participants: data.participants || [],
        contributors: data.contributors || [],
        messages: data.messages || [],
        questions: data.questions || [],
        polls: data.polls || [],
        announcements: data.announcements || [],
        activity: data.activity || [],
        broadcast: data.broadcast || null,
        recording: data.recording || null,
        recordings: data.recordings || [],
        analytics: data.analytics || null,
        health: data.health || null,
        countdownUntil: data.countdown_until || null,
        publishToken: data.publish_token || null,
        livekitUrl: data.livekit_url || null,
      };
    case "moderator/recording.status":
      return { ...state, event: { ...state.event, recording: data.recording } };
    case "moderator/room.status":
      return {
        ...state,
        recovering: !!data.recovering,
        event: { ...state.event, status: data.live ? "live" : state.event?.status },
      };

    case "participants/participant.join":
    case "participants/participant.update":
      return { ...state, participants: upsert(state.participants, data, "identity") };
    case "participants/participant.leave":
      return { ...state, participants: drop(state.participants, data, "identity") };

    // Backstage state transition (waiting/connected/ready/on_standby/live/muted/
    // reconnecting/removed/failed) for one assigned speaker — see services/contributor.py.
    case "contributor/session.update":
      return {
        ...state,
        contributors: state.contributors.map((c) =>
          c.user_id === data.user_id ? { ...c, session: data } : c
        ),
      };

    case "chat/message.new":
      return { ...state, messages: [...state.messages, data] };
    case "chat/message.update":
      return { ...state, messages: upsert(state.messages, data) };
    case "chat/message.delete":
      return { ...state, messages: drop(state.messages, data) };
    case "chat/typing": {
      const typing = { ...state.typing };
      if (data.typing) typing[data.identity] = { name: data.name, until: Date.now() + TYPING_TTL };
      else delete typing[data.identity];
      return { ...state, typing };
    }
    case "local/typing.prune": {
      const now = Date.now();
      const live = Object.entries(state.typing).filter(([, v]) => v.until > now);
      // Same object when nothing expired, so this tick doesn't re-render the panel.
      if (live.length === Object.keys(state.typing).length) return state;
      return { ...state, typing: Object.fromEntries(live) };
    }

    case "qa/question.new":
      return { ...state, questions: [...state.questions, data] };
    case "qa/question.update":
      return { ...state, questions: upsert(state.questions, data) };
    case "qa/question.delete":
      return { ...state, questions: drop(state.questions, data) };

    case "poll/poll.new":
      return { ...state, polls: [data, ...state.polls] };
    case "poll/poll.update":
      return { ...state, polls: upsert(state.polls, data) };
    case "poll/poll.delete":
      return { ...state, polls: drop(state.polls, data) };

    case "announcement/announcement.new":
      return { ...state, announcements: [data, ...state.announcements] };
    case "announcement/announcement.delete":
      return { ...state, announcements: drop(state.announcements, data) };

    case "activity/activity.new":
      return { ...state, activity: [data, ...state.activity].slice(0, 300) };

    // ── host console ──────────────────────────────────────────────────────────
    case "broadcast/broadcast.update":
      return {
        ...state,
        broadcast: { ...state.broadcast, ...data },
        recovering: false,
        // Keep the event badge in step with the broadcast. A PAUSE leaves the event live
        // (it's still running, just held), but an END must stop showing LIVE.
        event: {
          ...state.event,
          status: data.status === "live" ? "live"
            : data.status === "ended" ? "ended"
              : state.event?.status,
        },
      };
    case "broadcast/broadcast.preview":
      return {
        ...state,
        broadcast: { ...state.broadcast, status: "preview", settings: data.settings },
        publishToken: data.publish_token || state.publishToken,
        livekitUrl: data.livekit_url || state.livekitUrl,
      };
    case "broadcast/settings.update":
      return { ...state, broadcast: { ...state.broadcast, settings: data.settings } };
    case "broadcast/broadcast.countdown":
      return { ...state, countdownUntil: data.until };
    case "broadcast/broadcast.health":
      return { ...state, health: data, recovering: !!data.recovering };

    case "recording/recording.update":
      return {
        ...state,
        // A stopped recording leaves the active slot but stays in the log.
        recording: data.status === "stopped" ? null : data,
        recordings: upsert(state.recordings, data),
      };

    case "analytics/analytics.tick":
      return {
        ...state,
        analytics: { ...state.analytics, ...data },
        health: data.health || state.health,
      };

    default:
      return state;
  }
}

export default function useLiveEvent() {
  const [params] = useSearchParams();
  const eventParam = params.get("event");
  const [state, dispatch] = useReducer(reducer, EMPTY);

  // Which event: ?event=<id>, else this org's currently-live event via the EXISTING events
  // API. An explicit id needs no request, and deriving it means changing the URL re-attaches
  // the socket (a fetch-once hook would not).
  const { data: liveEvent, loading, error } = useApi(() =>
    eventParam
      ? Promise.resolve(null)
      : api
          .get("/events", { params: { status: "live", page_size: 1 } })
          .then((r) => r.data.items[0] || null)
  );
  const resolved = useMemo(
    () => (eventParam ? { id: eventParam } : liveEvent),
    [eventParam, liveEvent]
  );

  const onEnvelope = useCallback((env) => {
    if (env.channel === "moderator" && env.type === "error") {
      notify.error(env.data.message);
      return;
    }
    if (env.channel === "recording" && env.type === "recording.error") {
      notify.error(env.data.message);
      return;
    }
    // A control the server accepted but LiveKit couldn't apply. Saying so beats letting the
    // console imply a participant was muted or a file is being written.
    if (env.type === "action.result" && env.data.enforced === false) {
      notify.info("Recorded — LiveKit isn't connected, so it wasn't enforced on the stream.");
    }
    // Live sound + toast alert for EVERY viewer-initiated action — chat, Q&A, and poll
    // votes — so the host/moderator console doesn't have to keep every tab open to notice
    // audience activity. `actor_role` (server/app/services/moderation.py _actor_role) is
    // populated by the server on every chat/qa/poll envelope; it's only ever "viewer" here
    // since a host's/moderator's own actions are never notified back to themselves.
    //
    // THE BUG THIS FIXES: these checks used to compare against `env.data.actor_role`
    // before the server ever sent that field, so they silently never matched — no toast,
    // and (for chat/polls) no sound either. The chime for Q&A used to fire unconditionally
    // on every question.new instead of being tied to who asked, which happened to work by
    // accident for the common case but would also have chimed for a host's own question.
    const isViewer = env.data?.actor_role === "viewer";
    if (env.channel === "chat" && env.type === "message.new" && isViewer) {
      playAlertChime();
      notify.alert(`${env.data.name}: ${env.data.text}`);
    }
    if (env.channel === "qa" && env.type === "question.new" && isViewer) {
      playAlertChime();
      notify.alert(`New question from ${env.data.name}`);
    }
    if (env.channel === "poll" && env.type === "poll.update" && isViewer) {
      playAlertChime();
      notify.alert("New vote on your poll");
    }
    dispatch(env);
  }, []);

  const stream = useEventStream(resolved?.id, onEnvelope);

  // Warm up the notification chime's AudioContext on this console's first click/keypress,
  // rather than waiting for one to happen to land inside playQuestionAlert's own call —
  // see utils/sound.js for why that race silently ate the sound before.
  useEffect(() => {
    unlockAudio();
  }, []);

  // Expire stale typing indicators. Only ticks while somebody is typing.
  useInterval(() => dispatch({ channel: "local", type: "typing.prune" }),
    1000, Object.keys(state.typing).length > 0);

  return { state, resolved, loading, error, ...stream };
}