// client/src/pages/speaker/Console.jsx
// SPEAKER / PANELLIST live session console. Route: /speaker/live?event=<id>.
//
// The fourth console over the same socket and the same reducer (hooks/useLiveEvent). What is new
// here is not plumbing — it is that a speaker can finally PUBLISH: the server now mints a
// source-scoped token for an on-stage speaker (services/speaker.snapshot_extra), which is the gap
// that made this console impossible before.
//
// The grant is the thing to understand: `state.stage.sources` is what the room will accept from
// this person right now — camera and microphone once they are on stage, screen share only if a
// host granted it. Controls follow the grant rather than the role, so nothing on the bar silently
// fails mid-talk.
import { useCallback, useEffect, useMemo, useState } from "react";
import { FiMic, FiMessageSquare, FiHelpCircle, FiBarChart2, FiFileText, FiEdit3, FiActivity } from "react-icons/fi";
import api from "../../api";
import { notify } from "../../ui/Toast";
import useLiveEvent from "../../hooks/useLiveEvent";
import useMediaPreview from "../../hooks/useMediaPreview";
import usePublisher from "../../hooks/usePublisher";
import useInterval from "../../hooks/useInterval";
import useStageFeeds from "../../hooks/useStageFeeds";
import Skeleton from "../../ui/Skeleton";
import { cx } from "../../ui/tokens";
import EmptyState from "../../components/organization/OrganizationEmptyState";
import VideoGrid from "../../components/host/VideoGrid";
import { ChatTab } from "../../components/moderation/ChatQAPanel";
import SpeakerHeader from "../../components/speaker/SpeakerHeader";
import SpeakerControlBar from "../../components/speaker/SpeakerControlBar";
import SpeakerQA from "../../components/speaker/SpeakerQA";
import PollVote from "../../components/moderation/PollVote";
import SpeakerNotes from "../../components/speaker/SpeakerNotes";
import PresentationPanel from "../../components/speaker/PresentationPanel";
import Presenter from "../../components/speaker/Presenter";
import Whiteboard from "../../components/speaker/Whiteboard";
import TechnicalHealth from "../../components/speaker/TechnicalHealth";

const TABS = [
  { key: "qa", label: "Q&A", icon: FiHelpCircle },
  { key: "chat", label: "Chat", icon: FiMessageSquare },
  { key: "slides", label: "Slides", icon: FiFileText },
  { key: "notes", label: "Notes", icon: FiEdit3 },
  { key: "polls", label: "Polls", icon: FiBarChart2 },
  { key: "tech", label: "Status", icon: FiActivity },
];

export default function SpeakerConsole() {
  const { state, resolved, loading, error, status, latency, attempt, send } = useLiveEvent();

  // Local capture intent. Not server state: whether THIS speaker's camera is on is a property of
  // this machine, and the server already knows what it is allowed to receive.
  const [camera, setCamera] = useState(true);
  const [mic, setMic] = useState(true);
  const [tab, setTab] = useState("qa");
  const [board, setBoard] = useState(false);
  const [preview, setPreview] = useState(null);     // an asset being previewed, not presented
  // Presenter mode is DERIVED, not synced from an effect: it is open when this speaker opened it
  // or when they became the presenter — minus the presentation they explicitly closed. Mirroring
  // `iAmPresenting` into state through an effect is a cascading render, and the rule that flags it
  // is right: this is a computation, not a subscription.
  const [presenterOpen, setPresenterOpen] = useState(false);
  const [dismissed, setDismissed] = useState(null); // asset id whose presenter view was closed
  // Uploads and deletes go over REST, so their result is fetched rather than broadcast. Written
  // only from an async callback; null means "nothing fetched yet, use the snapshot".
  const [fetched, setFetched] = useState(null);

  const stage = state.stage || { on_stage: false, sources: [] };
  const sources = stage.sources || [];
  const settings = useMemo(() => state.broadcast?.settings || {}, [state.broadcast]);
  // Publish only when the server says this speaker may send something. Without a grant there is
  // nothing to connect for, and connecting anyway would put an idle publisher in the room.
  const publishWanted = sources.length > 0 && !!state.publishToken;

  // `hold` stops useMediaPreview calling track.stop() on tracks LiveKit is publishing — stop() is
  // irreversible and fires no 'ended' event, so the room would freeze on the last frame.
  const media = useMediaPreview({
    enabled: stage.on_stage, camera, mic, settings, hold: publishWanted,
  });

  const publisher = usePublisher({
    enabled: publishWanted,
    token: state.publishToken,
    url: state.livekitUrl,
    media,
    camera: camera && sources.includes("camera"),
    mic: mic && sources.includes("microphone"),
    onError: (m) => notify.error(m),
  });

  // Report the encoder's real figures back over the socket on the publisher's own cadence. The
  // server clamps every value; nothing here is trusted as-is.
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

  // Tell the server when this speaker's own mic goes up or down, so the stage roster and the
  // moderator console show the truth rather than the last thing a moderator set.
  useEffect(() => {
    if (stage.on_stage) send("participant.state", { muted: !mic });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mic, stage.on_stage]);

  // Hoisted out of the dep array: an optional-chained expression as a dependency defeats the
  // React Compiler's memoization analysis.
  const eventId = resolved?.id;
  const reloadAssets = useCallback(async () => {
    if (!eventId) return;
    try {
      const { data } = await api.get(`/speaker/events/${eventId}/assets`);
      setFetched(data);
    } catch {
      /* the snapshot copy stays; a failed refresh must not empty the list */
    }
  }, [eventId]);

  // The asset list, derived rather than mirrored. The snapshot seeds it, a REST refresh replaces
  // it, and approval decisions — which DO broadcast, on the presentation channel — win on the
  // fields they carry. No effect, so nothing cascades.
  const assets = useMemo(() => {
    const base = fetched ?? state.assets ?? [];
    const live = new Map((state.assets ?? []).map((a) => [a.id, a]));
    return base.map((a) => ({ ...a, ...(live.get(a.id) || {}) }));
  }, [fetched, state.assets]);

  // Same stage roster the host console builds, from the same rules.
  const feeds = useStageFeeds({
    participants: state.participants,
    publisher: { ...publisher, identity: state.publishIdentity },
    stream: media.stream,
    previewActive: media.active,
    camera: camera && sources.includes("camera"),
    mic: mic && sources.includes("microphone"),
  });

  const presentation = state.presentation;
  const liveAsset = presentation ? assets.find((a) => a.id === presentation.asset_id) : null;
  const iAmPresenting = presentation?.presenter_identity === state.myIdentity;
  const me = useMemo(
    () => state.participants.find((p) => p.identity === state.myIdentity) || null,
    [state.participants, state.myIdentity]
  );

  // Presenter mode opens automatically when this speaker becomes the presenter — they pressed
  // Present, so putting them in front of their own slides is what they asked for. Derived, so
  // closing it stays closed until a DIFFERENT deck goes up.
  const presenting = presenterOpen
    || (iAmPresenting && !!presentation && dismissed !== presentation.asset_id);
  const closePresenter = () => {
    setPresenterOpen(false);
    setPreview(null);
    if (presentation?.asset_id) setDismissed(presentation.asset_id);
  };

  const toggleScreen = async () => {
    if (publisher.screenSharing) {
      await publisher.stopScreenShare();
      return;
    }
    if (!sources.includes("screen_share")) {
      notify.info("The host hasn't given you screen share on this event.");
      return;
    }
    if (!publisher.publishing) {
      notify.info("You need to be on stage before you can share.");
      return;
    }
    await publisher.startScreenShare();
  };

  const onPresent = () => {
    if (liveAsset && (iAmPresenting || state.canModerate)) {
      setPresenterOpen(true);
      return;
    }
    setTab("slides");
    notify.info("Pick an approved file in the Slides tab to present it.");
  };

  // Speaker shortcuts. Skipped while typing, so a chat message never fires a control.
  useEffect(() => {
    const onKey = (e) => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const map = {
        m: () => setMic((v) => !v),
        v: () => setCamera((v) => !v),
        w: () => setBoard((v) => !v),
        h: () => send("participant.hand", { raised: !me?.hand }),
      };
      if (map[e.key]) {
        e.preventDefault();
        map[e.key]();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.hand]);

  if (loading) return <ConsoleSkeleton />;
  if (error || !resolved) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6 dark:bg-slate-950">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
          <EmptyState
            icon={FiMic}
            title={error ? "Couldn't load your sessions" : "No session to join"}
            description={
              error
                ? "The events API didn't respond. The console reconnects on its own once it's back."
                : "Open a session from your dashboard, or add ?event=<id> to this URL."
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800 lg:h-screen lg:overflow-hidden dark:bg-slate-950 dark:text-slate-200">
      <SpeakerHeader
        event={state.event}
        audience={state.audience}
        stage={stage}
        speaking={state.speaking}
        publisher={publisher}
        connection={status}
        latency={latency}
        attempt={attempt}
        canSpeak={state.canSpeak}
      />

      <div className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-4 p-4 sm:p-6 lg:overflow-auto">
            {/* Stage. The host's VideoGrid unchanged — the same tiles, the same speaking rings,
                the same screen-share promotion. A speaker sees the stage, not a producer's
                filmstrip, so the layout is fixed to gallery rather than following the host's
                broadcast composition. */}
            <div className="overflow-hidden rounded-2xl ring-1 ring-slate-200 dark:ring-slate-800">
              <VideoGrid
                feeds={feeds}
                layout={presentation ? "presentation" : "gallery"}
                pinnedIdentity={presentation?.presenter_identity || ""}
              />
            </div>

            {board && (
              <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <Whiteboard
                  objects={state.whiteboard}
                  identity={state.myIdentity}
                  canDraw={state.canSpeak}
                  send={send}
                  className="h-[26rem]"
                />
              </div>
            )}

            {/* Who is presenting, for a panellist who is not driving. */}
            {presentation && !iAmPresenting && (
              <p className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
                <span className="font-semibold">{presentation.presenter_name}</span> is presenting{" "}
                <span className="font-semibold">{presentation.filename}</span>
                {presentation.pages ? ` — slide ${presentation.slide} of ${presentation.pages}` : ""}
                {liveAsset && (
                  <button
                    type="button"
                    onClick={() => setPresenterOpen(true)}
                    className="ml-2 rounded font-semibold text-emerald-600 hover:underline dark:text-emerald-400"
                  >
                    Follow along
                  </button>
                )}
              </p>
            )}
          </div>

          <SpeakerControlBar
            onStage={stage.on_stage}
            sources={sources}
            camera={camera}
            mic={mic}
            screenSharing={publisher.screenSharing}
            publishing={publisher.publishing}
            handRaised={!!me?.hand}
            presenting={presenting}
            whiteboardOpen={board}
            onToggleMic={() => setMic((v) => !v)}
            onToggleCamera={() => setCamera((v) => !v)}
            onToggleScreen={toggleScreen}
            onPresent={onPresent}
            onWhiteboard={() => setBoard((v) => !v)}
            onHand={() => send("participant.hand", { raised: !me?.hand })}
            onLeave={() => { window.location.assign("/speaker/dashboard"); }}
          />
        </main>

        <aside className="flex w-full flex-col border-t border-slate-200 lg:min-h-0 lg:w-[400px] lg:border-l lg:border-t-0 dark:border-slate-800">
          <div role="tablist" className="flex shrink-0 overflow-x-auto border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
            {TABS.map((t) => {
              const badge = t.key === "qa"
                ? state.assignedQuestions.filter((q) => q.status !== "answered" && q.status !== "dismissed").length
                : t.key === "polls"
                  ? state.polls.filter((p) => p.status === "live").length
                  : 0;
              return (
                <button
                  key={t.key}
                  role="tab"
                  aria-selected={tab === t.key}
                  onClick={() => setTab(t.key)}
                  title={t.label}
                  className={cx(
                    "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-3 text-xs font-medium transition",
                    tab === t.key
                      ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                      : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
                  )}
                >
                  <t.icon className="text-base" aria-hidden="true" />
                  <span className="hidden sm:inline lg:hidden xl:inline">{t.label}</span>
                  {badge > 0 && (
                    <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-500/15 dark:text-amber-400">
                      {badge}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-white dark:bg-slate-900">
            {tab === "qa" && (
              <SpeakerQA
                className="min-h-0 flex-1 !rounded-none !border-0 !shadow-none"
                questions={state.assignedQuestions}
                send={send}
              />
            )}
            {/* The moderator console's chat, unchanged. A speaker gets the reply box and the
                report button; the moderation actions render only for can_moderate. */}
            {tab === "chat" && (
              <ChatTab
                messages={state.messages.filter((m) => m.status !== "deleted")}
                typing={state.typing}
                canModerate={state.canModerate}
                send={send}
              />
            )}
            {tab === "slides" && (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <PresentationPanel
                  className="!rounded-none !border-0 !shadow-none"
                  eventId={resolved.id}
                  assets={assets}
                  presentation={presentation}
                  myIdentity={state.myIdentity}
                  canModerate={state.canModerate}
                  canPresent={state.canSpeak}
                  onUploaded={reloadAssets}
                  onPreview={(a) => { setPreview(a); setPresenterOpen(true); }}
                  send={send}
                />
              </div>
            )}
            {tab === "notes" && (
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <SpeakerNotes notes={state.notes} canEdit={state.canSpeak} send={send} />
              </div>
            )}
            {tab === "polls" && (
              <div className="min-h-0 flex-1 overflow-y-auto">
                <PollVote className="!rounded-none !border-0 !shadow-none" polls={state.polls} send={send} />
              </div>
            )}
            {tab === "tech" && (
              <div className="min-h-0 flex-1 overflow-y-auto p-3">
                <TechnicalHealth
                  settings={settings}
                  stats={publisher.stats}
                  quality={me?.quality}
                  speakingSeconds={state.speaking?.seconds}
                  send={send}
                />
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* Presenter mode. `preview` wins over the live presentation so a speaker can read through
          their own deck while somebody else is on screen. */}
      <Presenter
        open={presenting}
        asset={preview || liveAsset}
        eventId={resolved.id}
        slide={preview ? undefined : presentation?.slide}
        pages={(preview || liveAsset)?.pages}
        canControl={!preview && (iAmPresenting || state.canModerate)}
        notes={state.notes}
        onSlide={(n) => send("presentation.slide", { slide: n })}
        onClose={closePresenter}
      />

      {status === "unauthorized" && (
        <p role="alert" className="border-t border-rose-200 bg-rose-50 px-6 py-2 text-center text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
          The server refused this connection. Check you're signed in and assigned to this session.
        </p>
      )}
      {status === "open" && !state.canSpeak && (
        <p role="status" className="border-t border-amber-200 bg-amber-50 px-6 py-2 text-center text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
          You aren't assigned as a speaker on this event, so the stage controls are disabled.
        </p>
      )}
    </div>
  );
}

function ConsoleSkeleton() {
  return (
    <div className="flex min-h-screen flex-col gap-4 bg-slate-50 p-4 sm:p-6 dark:bg-slate-950">
      <Skeleton variant="title" className="w-full" />
      <div className="flex flex-1 flex-col gap-4 lg:flex-row">
        <div className="flex-1 space-y-4">
          <Skeleton className="aspect-video w-full rounded-2xl" />
          <Skeleton className="h-14 w-full rounded-xl" />
        </div>
        <Skeleton variant="block" className="h-full min-h-[420px] lg:w-[400px]" />
      </div>
    </div>
  );
}
