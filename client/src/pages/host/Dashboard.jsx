// client/src/pages/host/Dashboard.jsx
// Host / Producer console — broadcast control room. Route: /host/dashboard (optionally ?event=<id>).
// Standalone full-screen page (NOT the Organization Dashboard).
//
// Live state comes from hooks/useLiveEvent — the same socket and reducer the moderator
// console uses. Local camera/mic come from hooks/useMediaPreview (native getUserMedia).
// The layout is unchanged: header, KPI row + stage above a pinned control deck, right sidebar.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FiRadio } from "react-icons/fi";
import useLiveEvent from "../../hooks/useLiveEvent";
import useMediaPreview from "../../hooks/useMediaPreview";
import useLiveKitPublish from "../../hooks/useLiveKitPublish";
import Skeleton from "../../ui/Skeleton";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import HostHeader from "../../components/host/HostHeader";
import SummaryCards from "../../components/host/SummaryCards";
import StudioStage from "../../components/host/StudioStage";
import ControlBar from "../../components/host/ControlBar";
import HostPanel from "../../components/host/HostPanel";
import FeatureModal from "../../components/host/FeatureModal";
import StartMeetingPrompt from "../../components/host/StartMeetingPrompt";

export default function HostDashboard() {
  const { state, resolved, loading, error, status, latency, attempt, send } = useLiveEvent();

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
  const [tab, setTab] = useState("participants");
  const [modal, setModal] = useState(null);

  // Media settings come from the server so they survive a refresh and every producer on the
  // event sees the same targets.
  const settings = useMemo(() => state.broadcast?.settings || {}, [state.broadcast]);
  const media = useMediaPreview({ enabled: previewOn, camera, mic, settings });

  const canHost = state.canHost;
  const live = state.broadcast?.status === "live";
  const ended = state.broadcast?.status === "ended";

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
    setScreenShare(false);
  };

  const toggleScreen = async () => {
    if (screenShare) {
      stopScreenShare();
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) return;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      screenStreamRef.current = stream;
      if (screenVideoRef.current) screenVideoRef.current.srcObject = stream;
      setScreenTrack(stream.getVideoTracks()[0] || null);
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
    if (canHost && state.broadcast?.status !== "live") send("broadcast.golive", {});
  }, [canHost, state.broadcast?.status, send]);

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
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6 dark:bg-slate-950">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
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
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
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
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-6 p-4 sm:p-6 lg:overflow-auto">
            <SummaryCards analytics={state.analytics} live={live} />
            <StudioStage
              broadcast={state.broadcast}
              recording={state.recording}
              analytics={state.analytics}
              participants={state.participants}
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
            onGoLive={() => {
              // A host who never clicked "Preview" still needs a live stream to publish —
              // arm it now so useLiveKitPublish has tracks to grab once `live` flips true.
              if (!previewOn) togglePreview();
              send("broadcast.golive", {});
            }}
            onPause={() => send("broadcast.pause", {})}
            onResume={() => send("broadcast.resume", {})}
            onEnd={() => send("broadcast.end", {})}
            onEmergencyStop={() => send("broadcast.emergency_stop", {})}
            onCountdown={(seconds) => send("broadcast.countdown", { seconds })}
            onRecord={() => send("recording.start", {})}
            onPauseRecord={() =>
              send(state.recording?.status === "paused" ? "recording.resume" : "recording.pause", {})}
            onStopRecord={() => send("recording.stop", {})}
            onChat={() => setTab("chat")}
            onParticipants={() => setTab("participants")}
            onInvite={() => setModal("invite")}
            onPolls={() => setTab("polls")}
            onQA={() => setTab("qa")}
            onSettings={() => setModal("settings")}
          />
        </main>

        <HostPanel
          tab={tab}
          setTab={setTab}
          state={state}
          canModerate={state.canModerate}
          send={send}
          className="min-h-[70vh] w-full border-t border-slate-200 lg:min-h-0 lg:w-[380px] lg:border-l lg:border-t-0 dark:border-slate-800"
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
        <p role="alert" className="border-t border-rose-200 bg-rose-50 px-6 py-2 text-center text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          You're not signed in as a host of this event, so broadcast controls are disabled.
        </p>
      )}
    </div>
  );
}

function StudioSkeleton() {
  return (
    <div className="flex min-h-screen flex-col gap-4 bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
      <Skeleton variant="title" className="w-full" />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
      </div>
      <div className="flex flex-1 flex-col gap-4 lg:flex-row">
        <div className="flex-1 space-y-4">
          <Skeleton className="aspect-video w-full rounded-2xl" />
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            {[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="aspect-video rounded-xl" />)}
          </div>
        </div>
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[380px]" />
      </div>
    </div>
  );
}
