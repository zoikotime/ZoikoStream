import { useCallback, useEffect, useMemo, useReducer } from "react";
import { useSearchParams } from "react-router-dom";
import useEventStream from "./useEventStream";
import useInterval from "./useInterval";
import useReactionChannel from "./useReactionChannel";
import { notify } from "../ui/Toast";
import { playAlertChime, unlockAudio } from "../utils/sound";

// The whole data layer for a live event console — event resolution, the socket, and one
// reducer over the server's envelopes.
//
// BOTH live surfaces use this: the host console (broadcast control AND audience
// management, which the retired moderator console used to split out) and the viewer watch
// page are layouts over the same live state, so the reducer lives here once instead of in
// each page. Channels a given surface doesn't render simply go unused.
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
  // The most recent reason the server refused a broadcast lifecycle action (go-live's
  // commercial/capacity/contributor readiness gate — see services/broadcast.py::_golive_gate).
  // Null once a broadcast.update lands, so a later successful transition clears a stale
  // rejection instead of leaving it displayed forever.
  goLiveError: null,
  // Idle -> pending -> (live, via broadcast.update | rejected, via goLiveError). Set by
  // sendGoLive (below) the moment the click fires, cleared by whichever resolution actually
  // arrives — driven entirely by dispatched actions, never by an effect watching this state.
  goLivePending: false,
};

const TYPING_TTL = 4000;

// The console attaches to the event in ?event=<id>, and to nothing else.
//
// There used to be a `pickBroadcastable` helper here that chose an event out of the
// organization's list when the URL carried none. It is deleted rather than left unused,
// because it encoded the defect: /host/dashboard was the host ACCOUNT role's landing page,
// so every host-persona login opened the Producer Console, the helper attached it to
// whichever org event ranked highest, and the backend correctly refused broadcast control on
// an event that person was never assigned to — "No host assigned", "View only".
//
// The fix is upstream: an account role is not an event assignment, so host/moderator/speaker
// now land on the organization dashboard, and enter a console only by choosing an event.
// See auth/destination.js.

const upsert = (list, item, key = "id") => {
  const i = list.findIndex((x) => x[key] === item[key]);
  if (i === -1) return [...list, item];
  const next = list.slice();
  next[i] = { ...next[i], ...item };
  return next;
};
const drop = (list, item, key = "id") => list.filter((x) => x[key] !== item[key]);

// Exported alongside EMPTY for the regression tests, so the live-state transitions that
// decide whether a host can go live are assertable without standing up a socket.
export { EMPTY as INITIAL_LIVE_STATE };

export function reducer(state, env) {
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
        // Sticky across reconnects. snapshot_extra mints a FRESH publish JWT on every
        // socket accept, and hooks/useLiveKitPublish.js keys its connect effect on the
        // token — so replacing it on every reconnect tore down and rebuilt the host's
        // LiveKit room (and republished the camera) for a socket blip that had nothing to do
        // with media. The token we already hold is still valid; keep it and let a genuinely
        // new one in only when we have none.
        publishToken: state.publishToken || data.publish_token || null,
        livekitUrl: state.livekitUrl || data.livekit_url || null,
      };
    // A broadcast lifecycle action the server refused outright — e.g. go-live's readiness
    // gate (services/broadcast.py::_golive_gate / commercial_readiness_blocked). Published
    // as its own channel/type (not the generic "moderator/error" a rejected chat/mod action
    // gets) because it carries a stable `code` alongside the human-readable `error` string,
    // for a caller that wants to branch on the reason rather than just display it.
    //
    // THE BUG THIS FIXES: this envelope reached the client already (the server has sent it
    // since the readiness gate was added), but nothing here or in the reducer matched
    // "host"/"broadcast.error", so it fell through to `default: return state` — a blocked
    // Go Live produced no toast, no state change, and no visible error at all. The console
    // just sat on "preview" with no explanation.
    case "host/broadcast.error":
      return { ...state, goLiveError: { message: data.error, code: data.code || null }, goLivePending: false };

    // Local, client-only actions — never sent over the wire (see sendGoLive below). Kept in
    // the same reducer/switch as everything else rather than a second little state machine,
    // since "pending" is just another field of the same live-event state.
    case "local/golive.start":
      // Idempotent: a second dispatch while already pending is a no-op, not a second
      // "attempt" — this is what stops a double-click from being treated as two requests.
      return state.goLivePending ? state : { ...state, goLivePending: true, goLiveError: null };
    case "local/golive.timeout":
      // Neither a broadcast.update nor a host/broadcast.error ever arrived (a dropped frame,
      // a server that never got the message) — don't leave the control stuck disabled
      // forever. Only fires if still pending; a real resolution that landed first wins.
      return state.goLivePending
        ? { ...state, goLivePending: false, goLiveError: state.goLiveError || {
            message: "Didn't hear back from the server — try Go Live again.", code: "timeout" } }
        : state;

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
        // Any real lifecycle transition supersedes a previous rejection (see
        // "host/broadcast.error" above) — a stale "cannot go live" must not keep showing
        // once the broadcast has actually moved. Also resolves a pending Go Live click,
        // whatever status it landed on (a pause/resume broadcast.update while some other
        // pending state was somehow still set clears it too — that's fine, it just means
        // the broadcast moved by other means).
        goLiveError: null,
        goLivePending: false,
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
        broadcast: {
          ...state.broadcast,
          // The server's own status, and never a demotion of a broadcast that is on air.
          // This used to hard-code "preview", so arming a preview mid-broadcast flipped
          // `live` false here — tearing down the publisher (hooks/useLiveKitPublish.js gates
          // on it) and turning the transport control back into "Go Live" while the event was
          // still live. services/broadcast.py::_preview no longer sends a demotion, and this
          // refuses to apply one regardless, so neither side alone can drop a live broadcast.
          status: state.broadcast?.status === "live" || state.broadcast?.status === "paused"
            ? state.broadcast.status
            : (data.status || "preview"),
          settings: data.settings,
        },
        publishToken: data.publish_token || state.publishToken,
        livekitUrl: data.livekit_url || state.livekitUrl,
      };
    case "broadcast/settings.update":
      return { ...state, broadcast: { ...state.broadcast, settings: data.settings } };
    case "broadcast/broadcast.countdown":
      return { ...state, countdownUntil: data.until };
    case "broadcast/broadcast.health":
      return {
        ...state,
        health: data,
        recovering: !!data.recovering,
        // event_status is the persisted signal from services/broadcast.py's
        // mark_degraded/mark_recovered (the audit fix: a producer disconnect used to only
        // ever reach this bus event, never Postgres, so a refresh/reconnect would silently
        // forget it). Kept in `event.status` alongside the existing "live"/"ended" writes
        // from broadcast.update below, so every consumer of event.status sees one
        // consistent value instead of two competing sources.
        event: data.event_status
          ? { ...state.event, status: data.event_status }
          : state.event,
      };

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
  // Audience reactions. Deliberately NOT part of `state`: a reaction is an ephemeral visual
  // event, and putting it through the reducer would re-render the entire Producer Console
  // (monitor, deck, KPI row, panel rail) once per tap in the audience. The channel's
  // identity never changes, so emitting is invisible to React — only the mounted
  // components/live/ReactionOverlay.jsx re-renders. See hooks/useReactionChannel.js.
  const reactions = useReactionChannel();

  // Which event: ?event=<id>, and ONLY that.
  //
  // This used to fall back to picking a broadcastable event out of the organization's list
  // when the URL carried none. That substitution is what turned a bad post-login redirect
  // into the reported bug: a host-persona account with no assignment landed on
  // /host/dashboard, the console silently attached to somebody else's event, and the
  // backend — correctly — answered can_host:false, leaving "No host assigned" / "View only"
  // on a broadcast the user had nothing to do with.
  //
  // A console with no event is not a destination. Callers route to the assignment picker
  // instead, so the event id is always something a person chose. The route
  // guard (EventConsoleRoute) also refuses to mount this page without one, so in practice
  // `eventParam` is present by the time the hook runs — this is the second line of defence,
  // not the first.
  // No request: the event id is in the URL or there is no event. `loading` and `error` stay
  // in the hook's shape because Dashboard.jsx branches on them, but resolution itself can no
  // longer fail or take time.
  const loading = false;
  const error = null;
  const resolved = useMemo(
    () => (eventParam ? { id: eventParam } : null),
    [eventParam]
  );

  const onEnvelope = useCallback((env) => {
    if (env.channel === "moderator" && env.type === "error") {
      notify.error(env.data.message);
      // A rejection of the go-live action itself resolves the pending click, exactly like
      // the readiness gate's "host"/"broadcast.error" does. The generic error frame is what
      // the server sends when the action is refused BEFORE the handler runs — the rate
      // limiter (routers/live.py's SlidingWindow) and the HOST_ONLY permission check both
      // land here — and it used to `return` before dispatching, so goLivePending stayed true:
      // the button sat disabled on "Starting…" for the full 12s timeout and then reported
      // "Didn't hear back from the server", which was not what happened. Re-routed as the
      // same broadcast.error the reducer already knows how to resolve, carrying the server's
      // real message instead of a fabricated one.
      if (env.data?.action === "broadcast.golive") {
        dispatch({
          channel: "host", type: "broadcast.error",
          data: { error: env.data.message, code: "action_rejected" },
        });
      }
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
    // A broadcast lifecycle action the server refused (go-live's readiness gate is the only
    // producer of this today). The reducer also stores it on `goLiveError` so a console can
    // show it inline next to the control that failed, not just as a toast that scrolls away.
    if (env.channel === "host" && env.type === "broadcast.error") {
      notify.error(env.data?.error || "This event cannot go live yet.");
    }
    // Live sound + toast alert for EVERY viewer-initiated action — chat, Q&A, and poll
    // votes — so the host console doesn't have to keep every tab open to notice
    // audience activity. `actor_role` (server/app/services/moderation.py _actor_role) is
    // populated by the server on every chat/qa/poll envelope; it's only ever "viewer" here
    // since an operator's own actions are never notified back to themselves.
    //
    // THE BUG THIS FIXES: these checks used to compare against `env.data.actor_role`
    // before the server ever sent that field, so they silently never matched — no toast,
    // and (for chat/polls) no sound either. The chime for Q&A used to fire unconditionally
    // on every question.new instead of being tied to who asked, which happened to work by
    // accident for the common case but would also have chimed for a host's own question.
    // A viewer reaction (server/app/services/moderation.py::_reaction_add). THE host-side
    // half of the requirement: every tap in the audience has to become a visible floating
    // emoji on this console in real time, wherever the viewer's socket landed — the
    // envelope reaches us through the Redis-backed event bus, so a viewer on one Cloud Run
    // instance and a host on another still meet here.
    //
    // Emitted, never dispatched, and never accumulated: there is no reaction state to go
    // stale, so a reconnect (which replays the full snapshot) has no old reactions to
    // replay — the server does not send any, and this holds nothing.
    //
    // No identity is read off the payload because the server does not put one there: the
    // console shows WHAT was sent, never who sent it.
    if (env.channel === "reactions" && env.type === "reaction.burst") {
      reactions.emit(env.data);
    }
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
  }, [reactions]);

  const stream = useEventStream(resolved?.id, onEnvelope);

  // The one call sites should use to go live, instead of `send("broadcast.golive", {})`
  // directly — it owns the pending/dedupe bookkeeping (state.goLivePending) so a caller
  // (e.g. the Go Live button) never needs its own effect or ref to track "is this in
  // flight". Guards against a double-click firing a second `broadcast.golive` over the
  // wire, and — if the socket isn't even open right now — synthesizes the same
  // "host"/"broadcast.error" envelope a server-side rejection would produce, through the
  // same onEnvelope path (so it gets the same toast and the same stored goLiveError),
  // rather than a second, parallel notion of "why did this fail".
  const sendGoLive = useCallback(() => {
    if (state.goLivePending) return;
    dispatch({ channel: "local", type: "golive.start" });
    const sent = stream.send("broadcast.golive", {});
    if (!sent) {
      onEnvelope({
        channel: "host", type: "broadcast.error",
        data: { error: "Not connected to the server right now — reconnecting. Try again once the connection is back.",
                code: "socket_not_open" },
      });
      return;
    }
    // Not a React effect — a plain timer armed by this event handler, cleaned up by the
    // reducer itself (the "local/golive.timeout" case is a no-op once already resolved), so
    // there's nothing to cancel on unmount.
    setTimeout(() => dispatch({ channel: "local", type: "golive.timeout" }), 12000);
  }, [state.goLivePending, stream, onEnvelope]);

  // Warm up the notification chime's AudioContext on this console's first click/keypress,
  // rather than waiting for one to happen to land inside playQuestionAlert's own call —
  // see utils/sound.js for why that race silently ate the sound before.
  useEffect(() => {
    unlockAudio();
  }, []);

  // Expire stale typing indicators. Only ticks while somebody is typing.
  useInterval(() => dispatch({ channel: "local", type: "typing.prune" }),
    1000, Object.keys(state.typing).length > 0);

  return { state, resolved, loading, error, ...stream, sendGoLive, reactions };
}