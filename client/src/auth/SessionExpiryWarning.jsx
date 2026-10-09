import { useEffect, useState } from "react";
import Modal from "../ui/Modal";
import Button from "../ui/Button";

// Shown WARNING_LEAD_MS before the server's idle deadline (useSessionKeeper). "Stay signed in"
// asks the server to record activity; the session continues only if the server agrees — a
// session already ended elsewhere cannot be revived from here.
const remaining = (expiresAt) => Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export default function SessionExpiryWarning({ expiresAt, onStay, onSignOut }) {
  const [secondsLeft, setSecondsLeft] = useState(() => remaining(expiresAt));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const tick = setInterval(() => setSecondsLeft(remaining(expiresAt)), 1000);
    return () => clearInterval(tick);
  }, [expiresAt]);

  const stay = async () => {
    setBusy(true);
    try {
      await onStay();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      // Dismissing is an answer too — someone just used the keyboard or mouse.
      onClose={stay}
      title="Your session will expire soon due to inactivity."
      size="sm"
      footer={(
        <div className="flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onSignOut}>Sign out</Button>
          <Button size="sm" onClick={stay} disabled={busy}>
            {busy ? "Checking…" : "Stay signed in"}
          </Button>
        </div>
      )}
    >
      <p className="text-sm text-slate-600 dark:text-slate-300" role="timer" aria-live="polite">
        For your security you will be signed out in{" "}
        <span className="font-semibold tabular-nums" data-testid="session-countdown">{mmss(secondsLeft)}</span>{" "}
        unless you stay signed in.
      </p>
    </Modal>
  );
}
