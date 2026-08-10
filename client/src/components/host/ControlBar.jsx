// client/src/components/host/ControlBar.jsx
// Broadcast control deck. Media toggles, engagement shortcuts, recording transport and the
// broadcast lifecycle (preview → countdown → live → pause/resume → end), plus emergency stop.
//
// Two deliberate safety choices, because this deck can end a live event in one click:
//   * End and Emergency stop ask for confirmation inline (a second click on the same
//     button) rather than firing immediately.
//   * Everything that changes the broadcast is disabled without `canHost` — the server
//     enforces the same rule, so a moderator sees why instead of getting a silent rejection.
import { useEffect, useState } from "react";
import {
  FiMic, FiMicOff, FiVideo, FiVideoOff, FiMonitor, FiRefreshCw, FiUserPlus, FiUsers,
  FiMessageSquare, FiBarChart2, FiHelpCircle, FiRadio, FiPhoneOff, FiCircle,
  FiPause, FiPlay, FiSettings, FiAlertOctagon, FiSquare, FiEye,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import { COUNTDOWN_PRESETS } from "../../data/host";

const TONE = {
  neutral: "bg-slate-900 text-white dark:bg-white dark:text-slate-900",
  blue: "bg-blue-600 text-white",
  rose: "bg-rose-600 text-white",
  amber: "bg-amber-500 text-white",
};

function ControlButton({ icon: Icon, label, tone = "neutral", active = false, disabled = false, onClick, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title || label}
      aria-pressed={active}
      className={cx(
        "flex min-w-[68px] flex-col items-center gap-1 rounded-xl px-3 py-2 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40",
        active
          ? TONE[tone]
          : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
      )}
    >
      <Icon className="text-lg" />
      {label}
    </button>
  );
}

const Divider = () => <span className="mx-1 hidden h-10 w-px shrink-0 bg-slate-200 sm:block dark:bg-slate-800" />;

// Two-step confirm: the first click arms, the second commits, and it disarms itself after a
// few seconds so a stale armed button can't be hit by accident later.
function DangerButton({ icon: Icon, label, armedLabel, title, disabled, onConfirm, className = "" }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      type="button"
      disabled={disabled}
      title={armed ? "Click again to confirm" : title || label}
      aria-label={title || label || armedLabel}
      onClick={() => (armed ? (setArmed(false), onConfirm()) : setArmed(true))}
      className={cx(
        "inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition disabled:cursor-not-allowed disabled:opacity-40",
        armed ? "bg-rose-700 ring-2 ring-rose-300 dark:ring-rose-500/40" : "bg-rose-600 hover:bg-rose-500",
        className
      )}
    >
      <Icon /> {armed ? armedLabel : label}
    </button>
  );
}

export default function ControlBar({
  broadcast, recording, canHost, previewOn, camera, mic, screenShare, media,
  onTogglePreview, onToggleCamera, onFlipCamera, onToggleMic, onToggleScreen,
  onGoLive, onPause, onResume, onEnd, onEmergencyStop, onCountdown,
  onRecord, onPauseRecord, onStopRecord,
  onChat, onParticipants, onInvite, onPolls, onQA, onSettings,
}) {
  const status = broadcast?.status || "preview";
  const live = status === "live";
  const paused = status === "paused";
  const ended = status === "ended";
  const rec = recording?.status;

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      {/* Media — local capture. Enabled regardless of host rights: checking your own
          camera is not a broadcast action. */}
      <ControlButton
        icon={FiEye}
        label={previewOn ? "Preview on" : "Preview"}
        tone="blue"
        active={previewOn}
        onClick={onTogglePreview}
        title={previewOn ? "Stop the local camera preview" : "Preview your camera and mic before going live"}
      />
      <ControlButton
        icon={mic ? FiMic : FiMicOff}
        label={mic ? "Mic" : "Unmute"}
        tone="rose"
        active={!mic}
        disabled={!previewOn}
        onClick={onToggleMic}
        title={previewOn ? (mic ? "Mute your microphone" : "Unmute") : "Start the preview first"}
      />
      <ControlButton
        icon={camera ? FiVideo : FiVideoOff}
        label={camera ? "Camera" : "Cam off"}
        tone="rose"
        active={!camera}
        disabled={!previewOn}
        onClick={onToggleCamera}
        title={previewOn ? (camera ? "Turn your camera off" : "Turn your camera on") : "Start the preview first"}
      />
      <ControlButton
        icon={FiRefreshCw}
        label="Flip"
        tone="blue"
        disabled={!previewOn || !camera || (media?.devices?.cameras?.length || 0) < 2}
        onClick={onFlipCamera}
        title={
          !previewOn
            ? "Start the preview first"
            : !camera
              ? "Turn the camera on first"
              : "Switch between available cameras"
        }
      />
      <ControlButton
        icon={FiMonitor}
        label="Share"
        tone="blue"
        active={screenShare}
        onClick={onToggleScreen}
        title={broadcast?.settings?.allow_screen_share === false
          ? "Screen sharing is disabled for this event"
          : "Share your screen"}
        disabled={broadcast?.settings?.allow_screen_share === false}
      />

      <Divider />

      {/* Engagement — opens the relevant panel/modal; available to anyone in the console. */}
      <ControlButton icon={FiUserPlus} label="Invite" onClick={onInvite} />
      <ControlButton icon={FiUsers} label="People" onClick={onParticipants} />
      <ControlButton icon={FiMessageSquare} label="Chat" onClick={onChat} />
      <ControlButton icon={FiBarChart2} label="Polls" onClick={onPolls} />
      <ControlButton icon={FiHelpCircle} label="Q&A" onClick={onQA} />
      <ControlButton icon={FiSettings} label="Settings" onClick={onSettings} title="Broadcast, chat and media settings" />

      <Divider />

      {/* Recording transport. Host-only, and the stop button only appears while rolling. */}
      <ControlButton
        icon={FiCircle}
        label={rec === "recording" ? "Recording" : rec === "paused" ? "Paused" : "Record"}
        tone="rose"
        active={!!rec}
        disabled={!canHost || ended}
        onClick={rec ? undefined : onRecord}
        title={!canHost ? "Only the host can control recording"
          : rec ? "Recording in progress — use pause or stop" : "Start recording"}
      />
      {rec && (
        <>
          <ControlButton
            icon={rec === "paused" ? FiPlay : FiPause}
            label={rec === "paused" ? "Resume" : "Pause"}
            tone="amber"
            disabled={!canHost}
            onClick={onPauseRecord}
            title={rec === "paused" ? "Resume recording" : "Pause recording"}
          />
          <ControlButton
            icon={FiSquare}
            label="Stop"
            tone="rose"
            disabled={!canHost}
            onClick={onStopRecord}
            title="Stop and finalise the recording"
          />
        </>
      )}

      {/* Broadcast lifecycle — pushed right. */}
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {!live && !paused && !ended && (
          <label className="hidden items-center gap-1.5 text-xs text-slate-500 sm:flex dark:text-slate-400">
            <span className="sr-only">Countdown before going live</span>
            <select
              onChange={(e) => e.target.value && onCountdown(Number(e.target.value))}
              defaultValue=""
              disabled={!canHost}
              title="Show a shared countdown to every console before going live"
              className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-600 outline-none disabled:opacity-40 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            >
              <option value="">Countdown…</option>
              {COUNTDOWN_PRESETS.map((s) => <option key={s} value={s}>{s}s</option>)}
            </select>
          </label>
        )}

        {paused ? (
          <button
            onClick={onResume}
            disabled={!canHost}
            className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm shadow-emerald-600/20 transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <FiPlay /> Resume
          </button>
        ) : (
          <button
            onClick={live ? onPause : onGoLive}
            disabled={!canHost || ended}
            title={!canHost ? "Only the event host can start or pause the broadcast" : undefined}
            className={cx(
              "inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-40",
              live
                ? "bg-amber-500 text-white shadow-sm hover:bg-amber-400"
                : "bg-emerald-600 text-white shadow-sm shadow-emerald-600/20 hover:bg-emerald-500"
            )}
          >
            {live ? <><FiPause /> Pause</> : <><FiRadio /> {ended ? "Ended" : "Go Live"}</>}
          </button>
        )}

        <DangerButton
          icon={FiPhoneOff}
          label="End Event"
          armedLabel="Confirm end"
          title="End the broadcast and stop any recording"
          disabled={!canHost || ended || (!live && !paused)}
          onConfirm={onEnd}
        />
        <DangerButton
          icon={FiAlertOctagon}
          label=""
          armedLabel="Confirm stop"
          title="Emergency stop — end the broadcast, stop recording and disconnect everyone from the room"
          disabled={!canHost || ended}
          onConfirm={onEmergencyStop}
          className="!px-3"
        />
      </div>

      {media?.error && (
        <p role="status" className="w-full text-xs text-rose-600 dark:text-rose-400">{media.error}</p>
      )}
    </div>
  );
}
