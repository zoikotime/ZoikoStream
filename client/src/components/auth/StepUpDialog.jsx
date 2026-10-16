// Password re-confirmation ("step-up") for one high-risk action.
//
// The server decides when an action needs it: it answers 403 {code: "STEP_UP_REQUIRED",
// purpose} and accepts the retry only with `X-Step-Up: <reference>`. A reference comes from
// POST /auth/step-up {password, purpose}; it is bound to that purpose, short-lived and
// single-use. This dialog only collects the password and exchanges it for a reference — the
// caller retries its own request (see useStepUp.jsx). It never stores the password: it lives
// in this component's state and is gone when the dialog closes.
//
// Accessible: a labelled dialog (title + description), focus moves to the password field on
// open and returns to whatever had it on close, Tab stays inside, Escape cancels, and a
// refusal is announced inline (role="alert") next to the field it is about.
import { useEffect, useId, useRef, useState } from "react";
import { FiShield } from "react-icons/fi";
import api, { errMsg } from "../../api";
import Overlay from "../../ui/Overlay";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Label } from "../../ui/forms";
import { cx } from "../../ui/tokens";

const COPY = {
  high_risk_role_grant: {
    title: "Confirm it's you",
    description:
      "Granting admin access gives full control of this organization. Enter your password to confirm you want to make this change.",
    confirmLabel: "Confirm and grant access",
  },
};
const FALLBACK = {
  title: "Confirm it's you",
  description: "This action needs you to confirm your password.",
  confirmLabel: "Confirm",
};

const WRONG_PASSWORD = "That password is incorrect. Try again.";
const TOO_MANY = "Too many attempts. Wait a few minutes, then try again.";

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export default function StepUpDialog({
  open = true,
  purpose,
  title,
  description,
  confirmLabel,
  onConfirmed,
  onCancel,
}) {
  const copy = COPY[purpose] || FALLBACK;
  const titleId = useId();
  const descId = useId();
  const inputId = useId();
  const errorId = useId();
  const panelRef = useRef(null);
  const inputRef = useRef(null);
  const alive = useRef(true);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // Focus in on open, back out on close (to the control that started the flow, if it is
  // still there and focusable).
  useEffect(() => {
    if (!open) return undefined;
    alive.current = true;
    const returnTo = document.activeElement;
    inputRef.current?.focus();
    return () => {
      alive.current = false;
      if (returnTo && returnTo.isConnected && typeof returnTo.focus === "function") returnTo.focus();
    };
  }, [open]);

  const cancel = () => onCancel?.();

  const submit = async (e) => {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/auth/step-up", { password, purpose });
      if (!alive.current) return;
      if (!data?.reference) {
        setError("Your password couldn't be confirmed. Try again.");
        return;
      }
      onConfirmed?.(data.reference);
    } catch (err) {
      if (!alive.current) return;
      const status = err?.response?.status;
      setError(
        status === 401 ? WRONG_PASSWORD
          : status === 429 ? TOO_MANY
            : errMsg(err, "Your password couldn't be confirmed. Try again.")
      );
      setPassword("");
      inputRef.current?.focus();
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  // Keep Tab inside the dialog while it is open (aria-modal alone does not move focus).
  const trapTab = (e) => {
    if (e.key !== "Tab" || !panelRef.current) return;
    const items = [...panelRef.current.querySelectorAll(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <Overlay open={open} onClose={cancel} className="grid place-items-center p-4">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        onKeyDown={trapTab}
        onClick={(e) => e.stopPropagation()}
        className={cx(
          "w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl",
          "motion-safe:animate-[zk-fade-in_.25s] dark:border-slate-800 dark:bg-slate-900"
        )}
      >
        <form onSubmit={submit} noValidate>
          <div className="flex items-start gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
            <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
              <FiShield aria-hidden="true" />
            </span>
            <div className="min-w-0">
              <h2 id={titleId} className="font-semibold text-slate-900 dark:text-white">{title || copy.title}</h2>
              <p id={descId} className="mt-1 text-sm text-slate-600 dark:text-slate-300">
                {description || copy.description}
              </p>
            </div>
          </div>

          <div className="px-5 py-4">
            <Label variant="console" htmlFor={inputId}>Your password</Label>
            <Input
              variant="console"
              ref={inputRef}
              id={inputId}
              type="password"
              name="current-password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); if (error) setError(null); }}
              disabled={busy}
              error={error || undefined}
              aria-describedby={error ? errorId : undefined}
            />
            {error && (
              <p id={errorId} role="alert" className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">
                {error}
              </p>
            )}
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 px-5 py-4 dark:border-slate-800">
            <Button variant="secondary" size="sm" onClick={cancel}>Cancel</Button>
            <Button type="submit" size="sm" loading={busy} disabled={!password || busy}>
              {confirmLabel || copy.confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </Overlay>
  );
}
