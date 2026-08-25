// client/src/pages/host/Dashboard.jsx
// Host / Producer console — broadcast control room. Route: /host/dashboard (optionally ?event=<id>).
// Standalone full-screen page (NOT the Organization Dashboard).
//
// Live state comes from hooks/useLiveEvent — the same socket and reducer the moderator
// console uses. Local camera/mic come from hooks/useMediaPreview (native getUserMedia).
//
// LAYOUT: three fixed bands (header / workspace / control deck) with the workspace split
// into a scrolling main column and a fixed-width panel rail. Only the main column and the
// panel body scroll — the header and the deck are always reachable, which is the whole
// point of a control room. `min-w-0` on every flex child is what keeps a long event title
// or a wide filmstrip from forcing the page to scroll sideways.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiRadio } from "react-icons/fi";
import useLiveEvent from "../../hooks/useLiveEvent";
import useMediaPreview from "../../hooks/useMediaPreview";
import useLiveKitPublish from "../../hooks/useLiveKitPublish";
import Skeleton from "../../ui/Skeleton";
import { cx } from "../../ui/tokens";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import { STUDIO } from "../../components/host/studio";
import HostHeader from "../../components/host/HostHeader";
import SummaryCards from "../../components/host/SummaryCards";
import StudioStage from "../../components/host/StudioStage";
import ControlBar from "../../components/host/ControlBar";
import HostPanel from "../../components/host/HostPanel";
import FeatureModal from "../../components/host/FeatureModal";
import StartMeetingPrompt from "../../components/host/StartMeetingPrompt";
export default function HostDashboard() {
  const navigate = useNavigate();
  const { state, resolved, loading, error, status, latency, attempt, send, sendGoLive } = useLiveEvent();

  // Local capture state. Deliberately NOT server state: whether this host's camera is on is
  // a property of this machine, not of the broadcast.
  const [previewOn, setPreviewOn] = useState(false);
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const [screenShare, setScreenShare] = useState(false);
  // The live capture itself. `screenTrack` (state, not just the ref) is what actually
  // drives publishing — useLiveKitPublish swaps it in for the camera video track while
  // sharing (see that hook). `screenShare` above stays purely a UI flag.
  const screenStreamRef = useRef(null);
  const screenVideoRef = useRef(null);
  const [screenTrack, setScreenTrack] = useState(null);
  // The shared tab/window's own sound (e.g. playing a video during the share) — separate
  // from the mic, which keeps publishing throughout a share. Not every capture has one:
  // Chrome only offers it for "Chrome Tab" (and "Entire Screen" on some platforms), never
  // for "Window", and other browsers may not support it at all.
  const [screenAudioTrack, setScreenAudioTrack] = useState(null);
  const [tab, setTab] = useState("participants");
  const [modal, setModal] = useState(null);

  // Media settings come from the server so they survive a refresh and every producer on the
  // event sees the same targets.
  const settings = useMemo(() => state.broadcast?.settings || {}, [state.broadcast]);
  const media = useMediaPreview({ enabled: previewOn, camera, mic, settings });

  const canHost = state.canHost;
  const live = state.broadcast?.status === "live";
  const ended = state.broadcast?.status === "ended";

  // Idle -> pending -> (live | goLiveError). Owned by hooks/useLiveEvent's reducer (state.
  // goLivePending) and driven entirely by dispatched actions — sendGoLive sets it the
  // moment the click fires, and it's cleared by whichever resolution actually arrives
  // (broadcast.update, host/broadcast.error, or its own internal timeout). See that hook
  // for why: this used to be tracked here with a ref + effect, which is exactly the
  // "adjust state when a prop changes" pattern React normally documents for this — but
  // this project's stricter hook lint (react-hooks/refs) forbids reading a ref during
  // render at all, so the resolution logic has to live where the real transitions already
  // are (the reducer), not be reconstructed from watching this component's own props.
  const goLivePending = state.goLivePending;

  // Ask the server to reserve the room the first time the host opens the preview, so the
  // check happens against real infrastructure rather than just locally.
  const togglePreview = () => {
    setPreviewOn((on) => {
      if (!on && canHost) send("broadcast.preview", {});
      return !on;
    });
  };

  // Publishes whatever the preview above is currently holding — camera/mic tracks, muted
  // state and all — into the LiveKit room once the broadcast is actually live. Gated on
  // media.active (not just previewOn) so publishing waits for getUserMedia to have really
  // resolved, rather than racing it.
  const { connected: isPublishing, reconnecting: isReconnecting, publishError } = useLiveKitPublish({
    enabled: live && media.active,
    url: state.livekitUrl,
    token: state.publishToken,
    streamRef: media.streamRef,
    screenTrack,
    screenAudioTrack,
    videoTrack: media.videoTrack,
  });

  // Once, the first time this host lands on a console they're allowed to run: ask whether
  // to start, rather than making them hunt for the "Preview" button. Confirming just arms
  // the preview above — "Go Live" is still a separate, deliberate click, so the host always
  // gets to check their camera/mic before anyone else can see them.
  const [showStartPrompt, setShowStartPrompt] = useState(false);
  const promptedRef = useRef(false);
  useEffect(() => {
    if (promptedRef.current || !canHost || previewOn || live || ended) return;
    promptedRef.current = true;
    setShowStartPrompt(true);
  }, [canHost, previewOn, live, ended]);

  // Screen share uses the native picker. getDisplayMedia is the whole feature — no library.
  // The captured track is kept (not discarded) so it can actually be published — see
  // screenTrack -> useLiveKitPublish above — and shown in the host's own preview below.
  const stopScreenShare = () => {
    screenStreamRef.current?.getTracks().forEach((t) => t.stop());
    screenStreamRef.current = null;
    if (screenVideoRef.current) screenVideoRef.current.srcObject = null;
    setScreenTrack(null);
    setScreenAudioTrack(null);
    setScreenShare(false);
  };

  const toggleScreen = async () => {
    if (screenShare) {
      stopScreenShare();
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) return;
    try {
      // audio: true asks for the shared tab/window's own sound — the browser only actually
      // grants it for capture types/platforms that support it (see screenAudioTrack above),
      // so getAudioTracks() below can still come back empty even on success.
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
      screenStreamRef.current = stream;
      if (screenVideoRef.current) screenVideoRef.current.srcObject = stream;
      setScreenTrack(stream.getVideoTracks()[0] || null);
      setScreenAudioTrack(stream.getAudioTracks()[0] || null);
      setScreenShare(true);
      // The browser's own "Stop sharing" button ends the track directly, bypassing our
      // button — mirror that back into state so the deck and preview stay honest.
      stream.getVideoTracks()[0]?.addEventListener("ended", stopScreenShare);
    } catch {
      setScreenShare(false);   // the host cancelled the picker
    }
  };

  // Belt-and-suspenders: release the capture if the host navigates away mid-share, so the
  // browser's "sharing this tab/screen" indicator doesn't outlive the console.
  useEffect(() => () => {
    screenStreamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  const clearCountdown = useCallback(() => {
    // Same action as the Go Live button (a shared countdown just fires it automatically at
    // zero), so it goes through the same sendGoLive — the pending state, dedupe guard, and
    // readiness-error surfacing all apply here too, not only to the manual click.
    if (canHost && state.broadcast?.status !== "live") sendGoLive();
  }, [canHost, state.broadcast?.status, sendGoLive]);

  // Producer keyboard shortcuts. Skipped while typing so chat input never fires a control.
  useEffect(() => {
    const onKey = (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const map = {
        d: () => setMic((v) => !v),                    // (d)eafen/mute self
        v: () => setCamera((v) => !v),                 // (v)ideo
        p: togglePreview,                              // (p)review
        r: () => canHost && send(state.recording ? "recording.stop" : "recording.start", {}),
        s: () => setModal("settings"),
      };
      if (map[e.key]) {
        e.preventDefault();
        map[e.key]();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canHost, state.recording]);

  if (loading) return <StudioSkeleton />;
  if (error || !resolved) {
    return (
      <div className={cx("flex min-h-screen items-center justify-center p-6", STUDIO.page)}>
        <div className={cx("w-full max-w-md", STUDIO.card)}>
          <EmptyState
            icon={FiRadio}
            title={error ? "Couldn't load your events" : "No event to broadcast"}
            description={
              error
                ? "The events API didn't respond. The console reconnects on its own once it's back."
                : "The studio attaches to your organization's live event. Publish an event and open it here, or use ?event=<id>."
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className={cx("flex min-h-screen flex-col lg:h-screen lg:overflow-hidden", STUDIO.page, STUDIO.body)}>
      <HostHeader
        event={state.event}
        broadcast={state.broadcast}
        analytics={state.analytics}
        health={state.health}
        recording={state.recording}
        connection={status}
        latency={latency}
        attempt={attempt}
        canHost={canHost}
        recovering={state.recovering}
        media={media}
      />

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <main className="flex min-w-0 flex-1 flex-col lg:min-h-0">
          {/* A flex COLUMN, not a spaced block: StudioStage's monitor is the flex-1 item
              that soaks up leftover height, so the workspace fits any viewport without the
              stage needing to guess at vh. overflow-y-auto stays as the safety valve for
              viewports too short even for the monitor's min-height floor. */}
          <div className="flex flex-1 flex-col gap-2 p-3 sm:px-4 sm:py-3 lg:min-h-0 lg:overflow-y-auto">
            <SummaryCards analytics={state.analytics} live={live} />
            <StudioStage
              onTogglePreview={togglePreview}
              broadcast={state.broadcast}
              recording={state.recording}
              analytics={state.analytics}
              media={media}
              camera={camera}
              mic={mic}
              screenShare={screenShare}
              screenVideoRef={screenVideoRef}
              countdownUntil={state.countdownUntil}
              onCountdownDone={clearCountdown}
              publishToken={state.publishToken}
              isPublishing={isPublishing}
              isReconnecting={isReconnecting}
              publishError={publishError}
            />
          </div>

          <ControlBar
            broadcast={state.broadcast}
            recording={state.recording}
            canHost={canHost}
            previewOn={previewOn}
            camera={camera}
            mic={mic}
            screenShare={screenShare}
            media={media}
            onTogglePreview={togglePreview}
            onToggleCamera={() => setCamera((v) => !v)}
            onFlipCamera={media.flipCamera}
            onToggleMic={() => setMic((v) => !v)}
            onToggleScreen={toggleScreen}
            goLivePending={goLivePending}
            goLiveError={state.goLiveError}
            onGoLive={() => {
              // A host who never clicked "Preview" still needs a live stream to publish —
              // arm it now so useLiveKitPublish has tracks to grab once `live` flips true.
              if (!previewOn) togglePreview();
              // Double-click / duplicate-send guarding lives in sendGoLive itself (state.
              // goLivePending), not here.
              sendGoLive();
            }}
            onPause={() => send("broadcast.pause", {})}
            onResume={() => send("broadcast.resume", {})}
            onEnd={() => {
              send("broadcast.end", {});
              // Release the camera/mic (this is also what stops LiveKit publishing,
              // since useLiveKitPublish above is gated on media.active) and drop any
              // active screen share, then leave — feedback is a viewer-only prompt
              // (see components/watch/EventWatch.jsx), the host doesn't get one.
              stopScreenShare();
              setPreviewOn(false);
              navigate("/");
            }}
            onEmergencyStop={() => send("broadcast.emergency_stop", {})}
            onCountdown={(seconds) => send("broadcast.countdown", { seconds })}
            onRecord={() => send("recording.start", {})}
            onPauseRecord={() =>
              send(state.recording?.status === "paused" ? "recording.resume" : "recording.pause", {})}
            onStopRecord={() => send("recording.stop", {})}
            onInvite={() => setModal("invite")}
            onSettings={() => setModal("settings")}
          />
        </main>

        {/* Panel rail: full width below lg (stacks under the stage), then a fixed column
            that widens with the viewport instead of stealing space from the monitor. */}
        <HostPanel
          tab={tab}
          setTab={setTab}
          state={state}
          canModerate={state.canModerate}
          send={send}
          eventId={resolved.id}
          className={cx(
            "min-h-[70vh] w-full shrink-0 border-t lg:min-h-0 lg:w-[340px] lg:border-l lg:border-t-0 xl:w-[380px] 2xl:w-[420px]",
            STUDIO.divider
          )}
        />
      </div>

      <FeatureModal
                modal={modal}
                onClose={() => setModal(null)}
                state={state}
                media={media}
                send={send}
                eventId={state.event?.id}
                eventVisibility={state.event?.visibility}
                onInvited={() => {
                  // Optional: refresh/reload live state if needed.
                  // The invitation itself is already handled by the API.
                }}
              />
      <StartMeetingPrompt
        open={showStartPrompt}
        eventTitle={state.event?.title}
        onConfirm={() => {
          setShowStartPrompt(false);
          if (!previewOn) togglePreview();
        }}
        onDismiss={() => setShowStartPrompt(false)}
      />

      {status === "unauthorized" && (
        <p
          role="alert"
          className="shrink-0 border-t border-rose-200 bg-rose-50 px-4 py-1.5 text-center text-[12px] font-medium text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300"
        >
          You&apos;re not signed in as a host of this event, so broadcast controls are disabled.
        </p>
      )}
    </div>
  );
}

// Mirrors the real layout's bands and radii so the page doesn't reflow when data lands.
function StudioSkeleton() {
  return (
    <div className={cx("flex min-h-screen flex-col", STUDIO.page)}>
      <div className={cx("shrink-0 border-b px-3 py-2 sm:px-4", STUDIO.chrome)}>
        <Skeleton className="h-9 w-full rounded-lg" />
      </div>
      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-3 p-3 sm:p-4">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6 xl:gap-3">
              {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-[92px] rounded-xl" />)}
            </div>
            <Skeleton className="aspect-video max-h-[54vh] w-full rounded-xl" />
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="aspect-video rounded-lg" />)}
            </div>
          </div>
          <div className={cx("shrink-0 border-t px-3 py-2 sm:px-4", STUDIO.chrome)}>
            <Skeleton className="h-14 w-full rounded-lg" />
          </div>
        </div>
        <Skeleton
          variant="block"
          className={cx("h-full min-h-[420px] shrink-0 border-t lg:w-[340px] lg:border-l lg:border-t-0 xl:w-[380px] 2xl:w-[420px]", STUDIO.divider)}
        />
      </div>
    </div>
  );
}
