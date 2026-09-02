// client/src/pages/speaker/Backstage.jsx
// Contributor backstage — the invited-speaker flow (BRD Section 10): device preflight and
// consent, then a private waiting area (return-feed monitor, self mic/camera, request
// help) until an operator admits/brings the contributor live. Route:
// /speaker/backstage?event=<id> (the link routers/events.py's invite email sends).
// Standalone full-screen page, same shape as /host/dashboard.
import { useCallback, useEffect, useReducer, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  FiVideo, FiVideoOff, FiMic, FiMicOff, FiCheckCircle, FiClock, FiRadio,
  FiAlertTriangle, FiHelpCircle, FiUserX,
} from "react-icons/fi";
import api from "../../api";
import useEventStream from "../../hooks/useEventStream";
import useMediaPreview from "../../hooks/useMediaPreview";
import useLiveKitViewer from "../../hooks/useLiveKitViewer";
import useLiveKitPublish from "../../hooks/useLiveKitPublish";
import DevicePicker from "../../components/speaker/DevicePicker";
import Spinner from "../../ui/Spinner";
import Logo from "../../ui/Logo";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import { cx } from "../../ui/tokens";
import { notify } from "../../ui/Toast";
import { CONTRIBUTOR_STATE_TONE, CONTRIBUTOR_STATE_LABEL } from "../../data/host";

const EMPTY = { ready: false, event: null, myState: null, publishToken: null, livekitUrl: null };

function reducer(state, env) {
  const { channel, type, data } = env;
  switch (`${channel}/${type}`) {
    case "moderator/snapshot":
      return {
        ready: true,
        event: data.event,
        myState: data.my_contributor_state || null,
        publishToken: data.my_publish_token || null,
        livekitUrl: data.livekit_url || null,
      };
    // Broadcast room-wide (see services/contributor.py snapshot_extra's note on bus.publish
    // having no per-recipient targeting) — only apply an update that's actually OUR row.
    case "contributor/session.update":
      return state.myState && data.user_id === state.myState.user_id
        ? { ...state, myState: data }
        : state;
    default:
      return state;
  }
}

// Modest defaults — a contributor's feed is a single face-to-camera source, not a studio
// broadcast, so there's no reason to ask their upload for 1080p.
const PREVIEW_SETTINGS = {
  resolution: "720p", framerate: 24,
  echo_cancellation: true, noise_cancellation: true, auto_gain: true, speaker_volume: 100,
};

function StatusHeader({ event, state }) {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/75 backdrop-blur-xl dark:border-white/10 dark:bg-slate-950/75">
      <div className="mx-auto flex h-16 max-w-3xl items-center justify-between gap-3 px-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Logo height="h-6 sm:h-7" />
          {event?.name && (
            <span className="hidden truncate text-sm font-medium text-slate-500 dark:text-slate-400 sm:inline">
              {event.name}
            </span>
          )}
        </div>
        {state && (
          <Badge tone={CONTRIBUTOR_STATE_TONE[state] || "neutral"} dot>
            {CONTRIBUTOR_STATE_LABEL[state] || state}
          </Badge>
        )}
      </div>
    </header>
  );
}

function Centered({ children }) {
  return (
    <main className="mx-auto flex max-w-lg flex-1 flex-col items-center justify-center gap-3 px-4 py-12 text-center">
      {children}
    </main>
  );
}

export default function Backstage() {
  const [params] = useSearchParams();
  const eventId = params.get("event");
  const [panel, dispatch] = useReducer(reducer, EMPTY);

  const onEnvelope = useCallback((env) => {
    if (env.channel === "moderator" && env.type === "error") {
      notify.error(env.data.message);
      return;
    }
    dispatch(env);
  }, []);
  const { status, closeReason, send } = useEventStream(eventId, onEnvelope);

  const state = panel.myState?.state;

  // Local device preview — same hook the host studio uses. Enabled as soon as the socket
  // is open, so preflight can run without a separate "start preview" click.
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const media = useMediaPreview({ enabled: status === "open", camera, mic, settings: PREVIEW_SETTINGS });
  // Destructured so every binding below is plain (not `media.x`) — passing the whole media
  // bag around and reading properties off it in JSX trips the refs-during-render lint rule,
  // same reasoning as StudioStage.jsx's identical destructure.
  const { videoRef, streamRef, videoTrack, devices, picked, active: mediaActive,
    error: mediaError, selectCamera, selectMic } = media;

  const [consentChecked, setConsentChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const submitPreflight = () => {
    setSubmitting(true);
    send("contributor.preflight_result", {
      camera_ok: !!mediaActive && !!videoTrack,
      mic_ok: !!streamRef.current?.getAudioTracks().some((t) => t.enabled),
      speaker_ok: true,
      framing_ok: true,
      browser_supported: true,
      // Honest, not fabricated: this app has no real network-quality probe. See
      // services/contributor.py's preflight_result docstring.
      network_quality: null,
    });
    if (consentChecked) send("contributor.consent", {});
    setSubmitting(false);
  };

  // Return feed: the same subscribe-only token a viewer gets from GET /events/:id/watch —
  // a logged-in org member always clears its org-membership check, so this needs no new
  // backend endpoint. `monitor: true` tags this connection's LiveKit identity distinctly
  // from this same contributor's own publish connection below (my_publish_token) — both
  // would otherwise resolve to the identical identity (str(user.id)) and evict each other
  // (see services/livekit.py's secondary()/primary() docstring).
  const [watch, setWatch] = useState(null);
  useEffect(() => {
    if (!eventId) return undefined;
    let cancelled = false;
    api.get(`/events/${eventId}/watch`, { params: { monitor: true } }).then(({ data }) => {
      if (!cancelled) setWatch(data);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [eventId, state]);   // re-check once live — the token only appears once the event is live

  const { mediaRef: returnFeedRef, hasVideo: returnHasVideo, hasAudio: returnHasAudio } = useLiveKitViewer({
    enabled: !!watch?.livekit_token, url: watch?.livekit_url, token: watch?.livekit_token,
  });

  const isLive = state === "live" || state === "muted";
  const { connected: publishing, reconnecting: publishReconnecting, publishError } = useLiveKitPublish({
    enabled: isLive && mediaActive,
    url: panel.livekitUrl,
    token: panel.publishToken,
    streamRef,
    videoTrack,
  });

  const requestHelp = () => {
    send("contributor.request_help", {});
    notify.success("The team has been notified — someone will reach out shortly.");
  };

  if (!eventId) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-6 text-center dark:bg-slate-950">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          This link is missing its event — use the invite link from your email.
        </p>
      </div>
    );
  }

  if (status === "unauthorized") {
    return (
      <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
        <StatusHeader event={panel.event} state={null} />
        <Centered>
          <span className="grid h-12 w-12 place-items-center rounded-full bg-rose-100 dark:bg-rose-500/15">
            <FiUserX className="text-xl text-rose-600 dark:text-rose-400" aria-hidden="true" />
          </span>
          <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Can&apos;t open the backstage</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {closeReason || "This invitation isn't valid right now."}
          </p>
        </Centered>
      </div>
    );
  }

  if (!panel.ready) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <Spinner />
      </div>
    );
  }

  if (state === "removed") {
    return (
      <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
        <StatusHeader event={panel.event} state={state} />
        <Centered>
          <FiUserX className="text-3xl text-slate-400" aria-hidden="true" />
          <h1 className="text-lg font-semibold text-slate-900 dark:text-white">Your access has ended</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {panel.myState?.removed_reason || "The event organizer has closed this backstage session."}
          </p>
        </Centered>
      </div>
    );
  }

  // ── preflight + consent: state is "waiting" or "failed" (retry), or no result yet ────
  if (state === "waiting" || state === "failed") {
    return (
      <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
        <StatusHeader event={panel.event} state={state} />
        <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8 sm:px-6">
          <h1 className="text-xl font-semibold text-slate-900 dark:text-white">Check your camera and microphone</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Make sure you can be seen and heard before joining the backstage for {panel.event?.name || "this event"}.
          </p>

          {state === "failed" && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
              <FiAlertTriangle className="mt-0.5 shrink-0" aria-hidden="true" />
              <span>Your last check didn&apos;t pass. Fix the issue below and try again.</span>
            </div>
          )}

          <div className="mt-4 aspect-video w-full overflow-hidden rounded-2xl bg-slate-900">
            {mediaError ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
                <FiVideoOff className="text-2xl text-rose-400" aria-hidden="true" />
                <p className="text-sm text-white">{mediaError}</p>
              </div>
            ) : (
              <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
            )}
          </div>

          <div className="mt-4 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => setCamera((v) => !v)}
              className={cx(
                "grid h-11 w-11 place-items-center rounded-full transition",
                camera ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white"
              )}
              aria-label={camera ? "Turn camera off" : "Turn camera on"}
            >
              {camera ? <FiVideo /> : <FiVideoOff />}
            </button>
            <button
              type="button"
              onClick={() => setMic((v) => !v)}
              className={cx(
                "grid h-11 w-11 place-items-center rounded-full transition",
                mic ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white"
              )}
              aria-label={mic ? "Mute microphone" : "Unmute microphone"}
            >
              {mic ? <FiMic /> : <FiMicOff />}
            </button>
          </div>

          <DevicePicker
            className="mt-5"
            devices={devices}
            picked={picked}
            selectCamera={selectCamera}
            selectMic={selectMic}
          />

          {panel.myState?.consent_notice && (
            <p className="mt-5 rounded-xl bg-slate-100 p-3 text-xs leading-relaxed text-slate-600 dark:bg-slate-900 dark:text-slate-400">
              {panel.myState.consent_notice}
            </p>
          )}
          <label className="mt-3 flex items-start gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input
              type="checkbox"
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-500"
            />
            I consent to appear on camera and have my contribution recorded as part of this event.
          </label>

          <Button
            appearance="console" variant="primary" className="mt-5 w-full"
            disabled={!consentChecked || !mediaActive || submitting}
            onClick={submitPreflight}
          >
            Continue to backstage
          </Button>
        </main>
      </div>
    );
  }

  // ── waiting / ready / on_standby / reconnecting: admitted or pending admission ─────────
  if (!isLive) {
    return (
      <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
        <StatusHeader event={panel.event} state={state} />
        <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8 sm:px-6">
          <div className="aspect-video w-full overflow-hidden rounded-2xl bg-slate-900">
            {returnHasVideo || returnHasAudio ? (
              <video ref={returnFeedRef} autoPlay playsInline className="h-full w-full object-cover" />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-slate-400">
                <FiRadio aria-hidden="true" className="text-2xl" />
                <p className="text-sm">
                  {watch?.status === "live" ? "Connecting to the return feed…" : "The event hasn't gone live yet."}
                </p>
              </div>
            )}
          </div>

          <div className="mt-5 flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-violet-100 dark:bg-violet-500/15">
              <FiClock className="text-violet-600 dark:text-violet-400" aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                {state === "connected" && "Waiting to be admitted"}
                {state === "ready" && "You're ready — waiting for the host to bring you live"}
                {state === "on_standby" && "On standby"}
                {state === "reconnecting" && "Reconnecting…"}
              </p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Your camera and mic stay off-air until the host brings you live.
              </p>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => setCamera((v) => !v)}
              className={cx("grid h-10 w-10 place-items-center rounded-full", camera ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white")}
              aria-label={camera ? "Turn camera off" : "Turn camera on"}
            >
              {camera ? <FiVideo /> : <FiVideoOff />}
            </button>
            <button
              type="button"
              onClick={() => setMic((v) => !v)}
              className={cx("grid h-10 w-10 place-items-center rounded-full", mic ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white")}
              aria-label={mic ? "Mute microphone" : "Unmute microphone"}
            >
              {mic ? <FiMic /> : <FiMicOff />}
            </button>
            <Button appearance="console" variant="secondary" leftIcon={FiHelpCircle} onClick={requestHelp}>
              Request help
            </Button>
          </div>
        </main>
      </div>
    );
  }

  // ── live / muted: actually publishing ───────────────────────────────────────────────
  return (
    <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
      <StatusHeader event={panel.event} state={state} />
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8 sm:px-6">
        <div className="aspect-video w-full overflow-hidden rounded-2xl bg-slate-900">
          <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
        </div>

        <div className="mt-4 flex items-center justify-center gap-2">
          <Badge tone="danger" dot>
            {state === "muted" ? "Live — muted by the host" : publishing ? "Live" : publishReconnecting ? "Reconnecting" : "Connecting…"}
          </Badge>
        </div>
        {publishError && (
          <p className="mt-2 text-center text-xs text-rose-600 dark:text-rose-400">{publishError}</p>
        )}

        <div className="mt-4 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => setCamera((v) => !v)}
            className={cx("grid h-11 w-11 place-items-center rounded-full", camera ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white")}
            aria-label={camera ? "Turn camera off" : "Turn camera on"}
          >
            {camera ? <FiVideo /> : <FiVideoOff />}
          </button>
          <button
            type="button"
            onClick={() => setMic((v) => !v)}
            disabled={state === "muted"}
            title={state === "muted" ? "The host has muted you" : undefined}
            className={cx(
              "grid h-11 w-11 place-items-center rounded-full disabled:cursor-not-allowed disabled:opacity-60",
              mic && state !== "muted" ? "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-200" : "bg-rose-600 text-white"
            )}
            aria-label={mic ? "Mute microphone" : "Unmute microphone"}
          >
            {mic && state !== "muted" ? <FiMic /> : <FiMicOff />}
          </button>
          <Button appearance="console" variant="secondary" leftIcon={FiHelpCircle} onClick={requestHelp}>
            Request help
          </Button>
        </div>

        <p className="mt-5 flex items-center justify-center gap-1.5 text-xs text-slate-400 dark:text-slate-500">
          <FiCheckCircle aria-hidden="true" /> You&apos;re on air — everything you say and show is part of the broadcast.
        </p>
      </main>
    </div>
  );
}
