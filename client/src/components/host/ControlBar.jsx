// client/src/components/host/ControlBar.jsx
// Broadcast control deck (Teams Town Hall style). All main controls live here:
// mic, camera, screen share, invite, participants, chat, polls, Q&A, recording,
// go live and end event. Pure presentational — parent owns the state/handlers.
import {
  FiMic, FiMicOff, FiVideo, FiVideoOff, FiMonitor, FiUserPlus, FiUsers,
  FiMessageSquare, FiBarChart2, FiHelpCircle, FiRadio, FiPhoneOff, FiCircle,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";

const TONE = {
  neutral: "bg-slate-900 text-white dark:bg-white dark:text-slate-900",
  blue: "bg-blue-600 text-white",
  rose: "bg-rose-600 text-white",
};

// One control-deck button: stacked icon + label, filled when active.
function ControlButton({ icon: Icon, label, tone = "neutral", active = false, disabled = false, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
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

export default function ControlBar({
  live, camera, mic, screenShare, recording, connecting = false,
  onGoLive, onEnd, onToggleCamera, onToggleMic, onToggleScreen, onToggleRecording,
  onChat, onParticipants, onInvite, onPolls, onQA,
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-white px-4 py-3 sm:px-6 dark:border-slate-800 dark:bg-slate-900">
      {/* Media */}
      <ControlButton icon={mic ? FiMic : FiMicOff} label={mic ? "Mic" : "Unmute"} tone="rose" active={!mic} onClick={onToggleMic} />
      <ControlButton icon={camera ? FiVideo : FiVideoOff} label={camera ? "Camera" : "Cam off"} tone="rose" active={!camera} onClick={onToggleCamera} />
      <ControlButton icon={FiMonitor} label="Share" tone="blue" active={screenShare} onClick={onToggleScreen} />

      <Divider />

      {/* Engagement */}
      <ControlButton icon={FiUserPlus} label="Invite" onClick={onInvite} />
      <ControlButton icon={FiUsers} label="People" onClick={onParticipants} />
      <ControlButton icon={FiMessageSquare} label="Chat" onClick={onChat} />
      <ControlButton icon={FiBarChart2} label="Polls" onClick={onPolls} />
      <ControlButton icon={FiHelpCircle} label="Q&A" onClick={onQA} />

      <Divider />

      {/* Recording status — can't start until the event is actually live */}
      <ControlButton
        icon={FiCircle}
        label={recording ? "Recording" : "Record"}
        tone="rose"
        active={recording}
        disabled={!live}
        onClick={onToggleRecording}
      />

      {/* Broadcast — pushed to the right */}
      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={onGoLive}
          disabled={live || connecting}
          className={cx(
            "inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed",
            live
              ? "cursor-default bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
              : connecting
                ? "bg-emerald-600/60 text-white"
                : "bg-emerald-600 text-white shadow-sm shadow-emerald-600/20 hover:bg-emerald-500"
          )}
        >
          <FiRadio /> {live ? "You're Live" : connecting ? "Connecting…" : "Go Live"}
        </button>
        <button
          onClick={onEnd}
          disabled={!live}
          className="inline-flex items-center gap-2 rounded-xl bg-rose-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-rose-500 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <FiPhoneOff /> End Event
        </button>
      </div>
    </div>
  );
}
