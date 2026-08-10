// client/src/components/host/StartMeetingPrompt.jsx
// Shown once, automatically, when the host lands on a console they're allowed to run.
// Confirming requests camera/mic access and arms the preview (Dashboard.jsx's existing
// togglePreview) — it's a shortcut to the camera prompt, not a shortcut past checking
// yourself: "Go Live" stays a separate, deliberate action.
import { FiVideo } from "react-icons/fi";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";

export default function StartMeetingPrompt({ open, eventTitle, onConfirm, onDismiss }) {
  return (
    <Modal
      open={open}
      onClose={onDismiss}
      
      title="Start the meeting?"
      size="sm"
      footer={
        <>
          <Button appearance="console" variant="ghost" size="sm" onClick={onDismiss}>
            Not yet
          </Button>
          <Button appearance="console" size="sm" leftIcon={FiVideo} onClick={onConfirm}>
            Start
          </Button>
        </>
      }
    >
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {eventTitle ? (
          <>
            You're about to run <strong className="text-slate-800 dark:text-slate-100">{eventTitle}</strong>.
          </>
        ) : (
          "You're about to run this event."
        )}{" "}
        Starting will ask for camera and microphone access so you can check how you look and sound before anyone
        else can see you — you'll still hit "Go Live" separately once you're ready.
      </p>
    </Modal>
  );
}
