// client/src/components/speaker/SpeakerControlBar.jsx
// The speaker's pinned control deck: mic, camera, screen share, present, whiteboard, hand, leave.
//
// Deliberately much smaller than the host's ControlBar. A speaker has no broadcast lifecycle and
// no recording controls, and a button the server refuses is worse than no button — so every
// control here maps to something the speaker tier can actually do
// (services/moderation.SPEAKER_ACTIONS + their source-scoped publish grant).
//
// Where a control is BLOCKED by a grant rather than by the role, it renders disabled with the
// reason: "the host hasn't given you screen share" is useful; a share button that silently fails
// mid-talk is not.
import {
  FiMic, FiMicOff, FiVideo, FiVideoOff, FiMonitor, FiFileText, FiEdit2, FiLogOut,
  FiAlertTriangle, FiArrowUpCircle,
} from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";

function Control({ icon: Icon, label, active, danger, disabled, reason, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      title={disabled ? reason : label}
      aria-label={label}
      className={cx(
        "flex min-w-[4.25rem] flex-col items-center gap-1 rounded-xl px-3 py-2 text-[11px] font-medium transition",
        disabled
          ? "cursor-not-allowed text-slate-300 dark:text-slate-600"
          : danger
            ? "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
            : active
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300"
              : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
        focusRing
      )}
    >
      <Icon className="text-lg" aria-hidden="true" />
      <span className="whitespace-nowrap">{label}</span>
    </button>
  );
}

export default function SpeakerControlBar({
  onStage, sources = [], camera, mic, screenSharing, publishing, handRaised,
  presenting, whiteboardOpen,
  onToggleMic, onToggleCamera, onToggleScreen, onPresent, onWhiteboard, onHand, onLeave,
}) {
  const mayCamera = sources.includes("camera");
  const mayMic = sources.includes("microphone");
  const mayShare = sources.includes("screen_share");

  return (
    <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-1 border-t border-slate-200 bg-white/95 px-3 py-2 backdrop-blur sm:gap-2 sm:px-4 dark:border-slate-800 dark:bg-slate-900/95">
      {/* What the room can currently receive from you — the single most useful thing on this bar. */}
      <div className="mr-1 flex items-center gap-2">
        {onStage ? (
          <Badge tone={publishing ? "success" : "warning"} size="sm" dot>
            {publishing ? "On air" : "On stage"}
          </Badge>
        ) : (
          <Badge tone="neutral" size="sm">In the audience</Badge>
        )}
      </div>

      <Control
        icon={mic && mayMic ? FiMic : FiMicOff}
        label={mic ? "Mute" : "Unmute"}
        active={mic && mayMic}
        disabled={!mayMic}
        reason={onStage ? "A moderator has turned your microphone off" : "You're not on stage yet"}
        onClick={onToggleMic}
      />
      <Control
        icon={camera && mayCamera ? FiVideo : FiVideoOff}
        label={camera ? "Stop video" : "Start video"}
        active={camera && mayCamera}
        disabled={!mayCamera}
        reason={onStage ? "A moderator has turned your camera off" : "You're not on stage yet"}
        onClick={onToggleCamera}
      />
      <Control
        icon={FiMonitor}
        label={screenSharing ? "Stop sharing" : "Share screen"}
        active={screenSharing}
        disabled={!mayShare}
        reason={onStage
          ? "The host hasn't given you screen share for this event"
          : "You're not on stage yet"}
        onClick={onToggleScreen}
      />

      <span className="mx-1 hidden h-8 w-px bg-slate-200 sm:block dark:bg-slate-700" aria-hidden="true" />

      <Control icon={FiFileText} label="Present" active={presenting} onClick={onPresent} />
      <Control icon={FiEdit2} label="Whiteboard" active={whiteboardOpen} onClick={onWhiteboard} />
      <Control
        icon={FiArrowUpCircle}
        label={handRaised ? "Lower hand" : "Raise hand"}
        active={handRaised}
        onClick={onHand}
      />

      <div className="ml-auto flex items-center gap-1">
        {!onStage && (
          <span className="hidden items-center gap-1 text-[11px] text-slate-400 sm:flex">
            <FiAlertTriangle aria-hidden="true" /> Waiting for the host to bring you on
          </span>
        )}
        <Control icon={FiLogOut} label="Leave" danger onClick={onLeave} />
      </div>
    </div>
  );
}
