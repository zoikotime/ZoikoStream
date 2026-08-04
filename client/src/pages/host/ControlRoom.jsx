// client/src/pages/host/ControlRoom.jsx
// Host / Producer LIVE CONTROL ROOM. Route: /host/live (optionally ?event=<id>).
// Standalone full-screen page. /host/dashboard is the host's LANDING page (assigned events);
// this is the broadcast surface they open from it.
//
// Live state comes from hooks/useLiveEvent — the same socket and reducer the moderator
// console uses. Local camera/mic come from hooks/useMediaPreview (native getUserMedia).
// The layout is unchanged: header, KPI row + stage above a pinned control deck, right sidebar.
import { useCallback, useEffect, useMemo, useState } from "react";
import { FiRadio } from "react-icons/fi";
import { notify } from "../../ui/Toast";
import useLiveEvent from "../../hooks/useLiveEvent";
import useMediaPreview from "../../hooks/useMediaPreview";
import usePublisher from "../../hooks/usePublisher";
import useInterval from "../../hooks/useInterval";
import LayoutControls from "../../components/host/LayoutControls";
import PreLiveCheck from "../../components/host/PreLiveCheck";
import Skeleton from "../../ui/Skeleton";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import HostHeader from "../../components/host/HostHeader";
import SummaryCards from "../../components/host/SummaryCards";
import StudioStage from "../../components/host/StudioStage";
import ControlBar from "../../components/host/ControlBar";
import HostPanel from "../../components/host/HostPanel";
import FeatureModal from "../../components/host/FeatureModal";

export default function HostControlRoom() {
  const { state, resolved, loading, error, status, latency, attempt, send } = useLiveEvent();

  // Local capture state. Deliberately NOT server state: whether this host's camera is on is
  // a property of this machine, not of the broadcast.
  const [previewOn, setPreviewOn] = useState(false);
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const [tab, setTab] = useState("participants");
  const [modal, setModal] = useState(null);
  const [checkOpen, setCheckOpen] = useState(false);

  // Media settings come from the server so they survive a refresh and every producer on the
  // event sees the same targets.
  const settings = useMemo(() => state.broadcast?.settings || {}, [state.broadcast]);
  const canHost = state.canHost;
  const live = state.broadcast?.status === "live";
  const publishingWanted = canHost && (live || state.broadcast?.status === "paused");

  // `hold` stops useMediaPreview from calling track.stop() while LiveKit is publishing those
  // exact tracks — stop() is irreversible and fires no 'ended' event, so the publisher would
  // keep a dead sender and viewers would freeze on the last frame.
  const media = useMediaPreview({ enabled: previewOn, camera, mic, settings, hold: publishingWanted });

  // THE publisher. Enabled only once the broadcast is actually on air: a preview is a local
  // check and must not put media into the room.
  const publisher = usePublisher({
    enabled: publishingWanted && !!state.publishToken,
    token: state.publishToken,
    url: state.livekitUrl,
    media,
    camera,
    mic,
    identity: state.publishIdentity,
    onError: (m) => notify.error(m),
  });

  // Report the encoder's REAL figures back over the existing socket, on the publisher's own
  // cadence (10s, not the 2s sampling rate) so one host cannot eat the 30-actions/10s budget.
  // The server clamps every value; nothing here is trusted as-is.
  useInterval(() => {
    const st = publisher.stats;
    if (!publisher.publishing || st?.bitrateKbps == null) return;
    send("participant.state", {
      bitrate_kbps: st.bitrateKbps,
      packet_loss: st.packetLoss ?? 0,
      rtt_ms: st.rttMs ?? 0,
      fps: st.fps ?? 0,
    });
  }, publisher.reportIntervalMs, publisher.publishing);

  // Layout + pin are BROADCAST SETTINGS, so every console converges on the same composition
  // and it survives a refresh.
  const layout = settings.layout || "grid";
  const pinnedIdentity = settings.pinned_identity || "";
  const setLayout = (next) => canHost && send("broadcast.settings", { settings: { layout: next } });
  const setPinned = (identity) =>
    canHost && send("broadcast.settings", { settings: { pinned_identity: identity || "" } });

  // Ask the server to reserve the room the first time the host opens the preview, so the
  // check happens against real infrastructure rather than just locally.
  const togglePreview = () => {
    setPreviewOn((on) => {
      if (!on && canHost) send("broadcast.preview", {});
      return !on;
    });
  };

  // Screen share is PUBLISHED as a second track alongside the camera (usePublisher), so the
  // presentation layout can show the share large with the camera inset. The old version called
  // getDisplayMedia and threw the stream away, leaking a capture handle on every toggle and
  // never sending a pixel to anyone.
  const toggleScreen = async () => {
    if (publisher.screenSharing) {
      await publisher.stopScreenShare();
      return;
    }
    if (!publisher.publishing) {
      notify.info("Go live first — a screen share is published to the room.");
      return;
    }
    await publisher.startScreenShare();
  };

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
            {canHost && (
              <LayoutControls
                layout={layout}
                pinnedIdentity={pinnedIdentity}
                participants={state.participants}
                publisher={publisher}
                onLayout={setLayout}
                onPin={setPinned}
              />
            )}
            <StudioStage
              broadcast={state.broadcast}
              recording={state.recording}
              analytics={state.analytics}
              participants={state.participants}
              media={media}
              camera={camera}
              mic={mic}
              screenShare={publisher.screenSharing}
              countdownUntil={state.countdownUntil}
              onCountdownDone={clearCountdown}
              publisher={{ ...publisher, identity: state.publishIdentity }}
              layout={layout}
              pinnedIdentity={pinnedIdentity}
              onPin={canHost ? setPinned : undefined}
            />
          </div>

          <ControlBar
            broadcast={state.broadcast}
            recording={state.recording}
            canHost={canHost}
            previewOn={previewOn}
            camera={camera}
            mic={mic}
            screenShare={publisher.screenSharing}
            media={media}
            onTogglePreview={togglePreview}
            onToggleCamera={() => setCamera((v) => !v)}
            onToggleMic={() => setMic((v) => !v)}
            onToggleScreen={toggleScreen}
            onGoLive={() => setCheckOpen(true)}
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

      <PreLiveCheck
        open={checkOpen}
        onClose={() => setCheckOpen(false)}
        settings={settings}
        canHost={canHost}
        onGoLive={() => send("broadcast.golive", {})}
      />

      <FeatureModal modal={modal} onClose={() => setModal(null)} state={state} media={media} send={send} />

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
