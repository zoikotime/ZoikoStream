import { useEffect, useRef, useState } from "react";
import { FiAlertTriangle, FiLock } from "react-icons/fi";
import api, { errMsg } from "../../api";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { cx } from "../../ui/tokens";
import useElevation from "../../components/admin/useElevation";

// Confirmation for POST /admin/live-events/{id}/end — the one destructive control on the
// platform live monitor.
//
// It is a real dialog rather than window.confirm because the wording carries the risk: the
// operator has to be told WHICH event, and that the two cases look identical in the table
// but are not (a live broadcast loses its audience; a stale row was already dead and is only
// being filed). It also has to state which one it believes this is, and be honest that on an
// "Unknown" row it cannot tell.
//
// The elevation gate is shown, not hidden. The server refuses without a "broadcast"
// elevation regardless of what this renders (security.require_elevation), so the dialog's
// job is to make the refusal legible BEFORE the click and offer the way out of it — and then
// to surface the server's 403 anyway if the grant lapsed in between, because the countdown
// here is a mirror, not the authority.
export default function EndSessionDialog({ session, onClose, onDone }) {
  const open = Boolean(session);
  const { status, seconds, elevate, elevating } = useElevation("broadcast");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const confirmRef = useRef(null);

  const blocked = status !== "active";

  // Reset the previous row's error when a DIFFERENT row's dialog opens, at render time:
  // React's documented "adjust state when a prop changes" pattern, and the one the
  // set-state-in-effect rule exists to steer toward. Clearing it in the effect below would
  // paint the stale error for one frame before wiping it.
  const [errorFor, setErrorFor] = useState(session?.id);
  if (session?.id !== errorFor) {
    setErrorFor(session?.id);
    setError(null);
  }

  useEffect(() => {
    if (!open) return undefined;
    const id = requestAnimationFrame(() => confirmRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open, session?.id]);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post(`/admin/live-events/${session.id}/end`);
      // `ok: false` is not a failure — it means the session was already ended, which a
      // double click or a concurrent reconciliation pass makes perfectly normal.
      onDone?.(data?.already_ended ? "That session had already ended." : "Session ended.");
      onClose?.();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  // What the row's reconciled health says this actually is. "unknown" means LiveKit could
  // not be reached, so the console must not claim either way.
  const health = session?.health;
  const consequence =
    health === "ok"
      ? "This broadcast is live. Everyone watching will be disconnected immediately."
      : health === "unknown"
        ? "The media server could not be reached, so it is not known whether anyone is still connected. If the broadcast is live, everyone watching will be disconnected."
        : "This session is being closed out.";

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onClose}
      title="End this broadcast session?"
      size="sm"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          {blocked && status !== "unknown" ? (
            <Button
              ref={confirmRef}
              variant="primary"
              size="sm"
              loading={elevating}
              disabled={elevating}
              onClick={async () => {
                try {
                  const granted = await elevate("End a broadcast session from the live monitor");
                  if (!granted) {
                    setError(
                      "You already hold a different elevation, and the platform opens one at a time. " +
                      "End it from the rail (“End now”), then elevate for broadcast."
                    );
                  }
                } catch (e) {
                  setError(errMsg(e));
                }
              }}
            >
              Elevate access
            </Button>
          ) : (
            <Button
              ref={confirmRef}
              variant="danger"
              size="sm"
              loading={busy}
              disabled={busy || blocked}
              onClick={confirm}
            >
              End session
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex gap-3">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400">
            <FiAlertTriangle aria-hidden="true" />
          </span>
          <div className="min-w-0 text-sm text-slate-600 dark:text-slate-300">
            <p className="font-medium text-slate-900 dark:text-white">{session?.title}</p>
            {session?.organization && (
              <p className="text-xs text-slate-500 dark:text-slate-400">{session.organization}</p>
            )}
            <p className="mt-2">{consequence}</p>
            <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
              The session is closed through the normal broadcast lifecycle and the action is
              recorded in the audit log. It cannot be undone.
            </p>
          </div>
        </div>

        {blocked && (
          <div
            className={cx(
              "flex gap-2.5 rounded-lg px-3 py-2.5 text-xs",
              "bg-amber-50 text-amber-800 dark:bg-amber-500/[0.10] dark:text-amber-300"
            )}
          >
            <FiLock aria-hidden="true" className="mt-0.5 shrink-0" />
            <p>
              {status === "unknown"
                ? "Access state unknown — the console API is unreachable, so it cannot be confirmed that you hold the elevation this action needs."
                : status === "expired"
                  ? "Your elevation has expired. Elevate again to end a broadcast session."
                  : status === "wrong_scope"
                    ? "Your current elevation does not cover broadcast operations. Elevate for broadcast to continue."
                    : "Ending a broadcast session needs an active elevation."}
            </p>
          </div>
        )}

        {!blocked && seconds > 0 && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Elevated · {Math.floor(seconds / 60)}m {seconds % 60}s remaining.
          </p>
        )}

        {error && (
          <p className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
