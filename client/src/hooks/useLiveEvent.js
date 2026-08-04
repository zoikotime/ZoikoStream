import { useCallback, useMemo, useReducer } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../api";
import useApi from "./useApi";
import useEventStream from "./useEventStream";
import useInterval from "./useInterval";
import { notify } from "../ui/Toast";

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
  publishIdentity: null,
  livekitUrl: null,
  recovering: false,
  // Alert Center. Derived from the envelope stream rather than fetched, because every alert
  // the brief asks for is a TRANSITION ("host ended the event", "recording stopped") and a
  // snapshot of current state cannot express one. Cleared by a reconnect along with everything
  // else — the snapshot replaces state, which is what makes a dropped socket self-heal.
  alerts: [],
  // Private replies addressed to THIS connection (services/moderation._participant_notify).
  notices: [],
  // speaker console (services/speaker.py)
  canSpeak: false,
  // This connection's own identity, as the server knows it. Needed because presence records and
  // question assignments are keyed on it, and "is this mine" is a question the reducer has to
  // answer without reaching into auth state.
  myIdentity: null,
  assets: [],
  presentation: null,
  whiteboard: [],
  notes: "",
  assignedQuestions: [],
  stage: null,               // my own grant: on_stage, sources, camera/share allowed
  speaking: null,            // my speaking time + connection quality
  audience: null,            // the narrowed viewer counts every tier receives
  issues: [],                // technical issues raised by speakers (consoles only)
};

const TYPING_TTL = 4000;
const ALERT_LIMIT = 50;

const upsert = (list, item, key = "id") => {
  const i = list.findIndex((x) => x[key] === item[key]);
  if (i === -1) return [...list, item];
  const next = list.slice();
  next[i] = { ...next[i], ...item };
  return next;
};
const drop = (list, item, key = "id") => list.filter((x) => x[key] !== item[key]);

/** Keep the speaker's assigned-question list in step with one incoming question update.
 *  Assignment can change mid-event (a moderator re-routes a question), so this both ADDS a
 *  question newly assigned to me and REMOVES one handed to somebody else — a plain upsert would
 *  leave a stranger's question sitting in my worklist for the rest of the session. */
function assignedFor(state, q) {
  const mine = !!state.myIdentity && q.assigned_to === state.myIdentity;
  const present = state.assignedQuestions.some((x) => x.id === q.id);
  if (mine) {
    return present ? upsert(state.assignedQuestions, q) : [q, ...state.assignedQuestions];
  }
  return present ? drop(state.assignedQuestions, q) : state.assignedQuestions;
}

// ── alert derivation ──────────────────────────────────────────────────────────
// One place that turns envelopes into the Alert Center's rows. `prev` is the state BEFORE the
// envelope was applied, which is what makes "changed" detectable — without it a health tick
// every 15s would post the same alert forever.

const ALERT_TONES = { critical: "critical", warning: "warning", info: "info" };

function alertFor(env, prev) {
  const { channel, type, data } = env;
  const at = env.ts ? env.ts * 1000 : Date.now();
  const alert = (kind, tone, text) => ({ id: `${kind}-${env.ts}`, kind, tone: ALERT_TONES[tone], text, at });

  switch (`${channel}/${type}`) {
    case "broadcast/broadcast.update": {
      const was = prev.broadcast?.status;
      if (data.status === was) return null;
      if (data.status === "live") return alert("host_start", "info", "The host started the broadcast.");
      if (data.status === "ended") return alert("host_end", "critical", "The host ended the broadcast.");
      if (data.status === "paused") return alert("host_pause", "warning", "The host paused the broadcast.");
      return null;
    }
    case "recording/recording.update":
      return data.status === "stopped"
        ? alert("recording_stop", "warning", "Recording stopped.")
        : null;
    case "moderator/recording.status":
      // The webhook's view of the same thing. Only alert on the falling edge.
      return !data.recording && prev.event?.recording
        ? alert("recording_stop", "warning", "Recording stopped.")
        : null;
    case "moderator/room.status":
      return data.recovering
        ? alert("room_drop", "critical", "The media room dropped — waiting for the publisher to reconnect.")
        : null;
    case "participants/participant.leave": {
      const role = data.role || "viewer";
      return ["host", "speaker", "moderator"].includes(role)
        ? alert("speaker_left", "warning", `${data.name || "Someone"} (${role}) disconnected.`)
        : null;
    }
    case "chat/message.new":
      return (data.flags || []).length
        ? alert("chat_abuse", "warning", `Flagged message from ${data.name}: ${(data.flags || []).join(", ")}.`)
        : null;
    case "moderator/message.flagged":
      return alert("reported", "warning", `A viewer reported a message from ${data.name}.`);
    case "broadcast/broadcast.health":
    case "analytics/analytics.tick": {
      const health = type === "broadcast.health" ? data : data.health;
      const issues = (health?.issues || []).join(" · ");
      // Only when the issue SET changes, so a persistent problem is one row, not one per tick.
      if (!issues || issues === (prev.health?.issues || []).join(" · ")) return null;
      return alert("network", health.level === "down" ? "critical" : "warning", issues);
    }
    default:
      return null;
  }
}

// Lobby growth is its own check: it reads two numbers off the analytics tick rather than a
// health verdict, and the moderator needs it even when everything else is fine.
function lobbyAlert(env, prev) {
  if (env.channel !== "analytics" || env.type !== "analytics.tick") return null;
  const now = env.data.waiting ?? 0;
  const before = prev.analytics?.waiting ?? 0;
  if (now <= before || now < 3) return null;
  return {
    id: `lobby-${env.ts}`, kind: "lobby", tone: "warning", at: env.ts * 1000,
    text: `${now} people are waiting in the lobby.`,
  };
}

function apply(state, env) {
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
        publishIdentity: data.publish_identity || null,
        livekitUrl: data.livekit_url || null,
        // Speaker block. A moderator-tier snapshot carries these too (a host presents their own
        // slides), so this is not an else-branch — the same reducer serves all three consoles.
        canSpeak: !!data.can_speak,
        myIdentity: data.identity || null,
        assets: data.assets || [],
        presentation: data.presentation || null,
        whiteboard: data.whiteboard || [],
        notes: data.notes || "",
        assignedQuestions: data.assigned_questions || [],
        stage: data.stage || null,
        speaking: data.speaking || null,
        // The narrowed audience block the speaker/attendee projections send. A moderator gets the
        // full `analytics` object instead, so this is derived from it for them — one field the
        // header can read regardless of tier.
        audience: data.audience || (data.analytics
          ? { viewers: data.analytics.viewers ?? 0, participants: data.analytics.participants ?? 0 }
          : null),
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
    case "participants/participant.notice":
      // Only ever delivered to the addressee — routers/live.py drops it on every other socket.
      return { ...state, notices: [{ ...data, at: env.ts * 1000 }, ...state.notices].slice(0, 20) };

    // A report result, delivered on the console-only channel so an attendee can't read the
    // room's moderation state back off their own screen (services/moderation._chat_report).
    case "moderator/message.flagged":
      return { ...state, messages: upsert(state.messages, data) };

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
      return {
        ...state,
        questions: upsert(state.questions, data),
        // A speaker's own worklist is a VIEW of the same rows, so answering from either panel
        // updates both. A question assigned to me for the first time joins the list; one
        // reassigned away from me leaves it — the server sends assigned_to on every update.
        assignedQuestions: assignedFor(state, data),
      };
    case "qa/question.delete":
      return {
        ...state,
        questions: drop(state.questions, data),
        assignedQuestions: drop(state.assignedQuestions, data),
      };

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
        // The speaker tier receives this envelope narrowed to four counters; keeping `audience`
        // in step means the speaker header does not need to know which tier it is on.
        audience: { ...state.audience, ...data },
      };

    // ── speaker console ───────────────────────────────────────────────────────
    case "presentation/presentation.update":
      // An empty asset_id means "nothing on screen" — kept as an object rather than nulled so a
      // component can render the stopped state without a separate flag.
      return { ...state, presentation: data.asset_id ? data : null };
    case "presentation/asset.update":
      return { ...state, assets: upsert(state.assets, data) };
    case "whiteboard/board.add":
      return { ...state, whiteboard: [...state.whiteboard, data] };
    case "whiteboard/board.remove":
      return { ...state, whiteboard: drop(state.whiteboard, data) };
    case "whiteboard/board.reset":
      return { ...state, whiteboard: data.objects || [] };
    // A speaker's technical issue. Console-only: `moderator` is not in the attendee or speaker
    // channel allow-lists, so the audience never sees "my microphone is broken".
    case "moderator/speaker.issue":
      return { ...state, issues: [data, ...state.issues].slice(0, 30) };

    default:
      return state;
  }
}

// The exported reducer: apply the envelope, then append whatever alerts it implied. Split this
// way so `apply` stays a plain state machine and the alert rules read as rules.
function reducer(state, env) {
  const next = apply(state, env);
  const derived = [alertFor(env, state), lobbyAlert(env, state)].filter(Boolean);
  if (!derived.length) return next;
  return { ...next, alerts: [...derived, ...next.alerts].slice(0, ALERT_LIMIT) };
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
    // A private reply only ever reaches its addressee, so anything arriving here is for us.
    if (env.type === "participant.notice") {
      notify.info(`${env.data.from_name}: ${env.data.text}`);
    }
    dispatch(env);
  }, []);

  const stream = useEventStream(resolved?.id, onEnvelope);

  // Expire stale typing indicators. Only ticks while somebody is typing.
  useInterval(() => dispatch({ channel: "local", type: "typing.prune" }),
    1000, Object.keys(state.typing).length > 0);

  return { state, resolved, loading, error, ...stream };
}
