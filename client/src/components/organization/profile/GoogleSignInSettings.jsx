import { useEffect, useState } from "react";
import { FcGoogle } from "react-icons/fc";
import api, { errMsg } from "../../../api";
import { ConsoleButton as Button } from "../../../ui/Button";
import { notify } from "../../../ui/Toast";
import useStepUp, { isStepUpRequired } from "../../auth/useStepUp";
import { startGoogleLink } from "../../../pages/auth/googleAuth";

// Settings > Security: link or unlink the Google account this ZoikoStream account signs in with.
//
// Google sign-in works ONLY for a linked Google account — the server never matches an account
// by email (server/app/models/user_identity.py). Linking needs a fresh password confirmation:
// the server answers 403 STEP_UP_REQUIRED to a plain request, and this asks for the password
// (useStepUp) and retries with the one-time reference before sending the browser to Google.
// Unlinking needs no confirmation: removing a way in is always allowed, and is emailed.
//
// The server decides availability (configured, and a role that may use Google — never
// super_admin); this only reflects GET /auth/google/identity.

const fmt = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};

export default function GoogleSignInSettings() {
  const stepUp = useStepUp();
  const [status, setStatus] = useState(null); // null = loading; { error } on failure
  const [busy, setBusy] = useState(false);
  const [confirmUnlink, setConfirmUnlink] = useState(false);

  useEffect(() => {
    let alive = true; // a late answer must not update a page that has moved on
    api
      .get("/auth/google/identity")
      .then(({ data }) => alive && setStatus(data || { error: true }))
      .catch(() => alive && setStatus({ error: true }));
    return () => {
      alive = false;
    };
  }, []);

  const link = async () => {
    if (busy) return;
    setBusy(true);
    let leaving = false; // navigating to Google: keep the button busy rather than flash it idle
    try {
      try {
        await startGoogleLink();
        leaving = true;
      } catch (error) {
        if (!isStepUpRequired(error)) throw error;
        const reference = await stepUp.request({ purpose: "google_link" });
        if (!reference) return; // cancelled
        await startGoogleLink({ stepUpReference: reference });
        leaving = true;
      }
    } catch (error) {
      notify.error(errMsg(error, "Google linking couldn't start. Please try again."));
    } finally {
      if (!leaving) setBusy(false);
    }
  };

  const unlink = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const { data } = await api.delete("/auth/google/identity");
      setStatus(data);
      setConfirmUnlink(false);
      notify.success("Google sign-in removed from your account.");
    } catch (error) {
      notify.error(errMsg(error, "Couldn't remove Google sign-in. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (status === null) {
    body = <p className="text-sm text-slate-500 dark:text-slate-400">Checking Google sign-in…</p>;
  } else if (status.error) {
    body = (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Google sign-in status isn't available right now.
      </p>
    );
  } else if (status.linked) {
    const linkedAt = fmt(status.linked_at);
    const lastUsed = fmt(status.last_used_at);
    body = (
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <FcGoogle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0" />
          <div className="text-sm">
            <p className="font-medium text-slate-900 dark:text-white">Linked to {status.email}</p>
            <p className="text-slate-500 dark:text-slate-400">
              {linkedAt ? `Linked ${linkedAt}` : "Linked"}
              {" · "}
              {lastUsed ? `Last used ${lastUsed}` : "Not used to sign in yet"}
            </p>
          </div>
        </div>
        {confirmUnlink ? (
          <div className="flex items-center gap-2" role="group" aria-label="Confirm removing Google sign-in">
            <span className="text-sm text-slate-600 dark:text-slate-300">Remove Google sign-in?</span>
            <Button variant="danger" size="sm" loading={busy} onClick={unlink}>
              Remove
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmUnlink(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <Button variant="secondary" size="sm" onClick={() => setConfirmUnlink(true)}>
            Unlink
          </Button>
        )}
      </div>
    );
  } else if (!status.available) {
    body = (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        Google sign-in isn't available for this account. Sign in with your email and password.
      </p>
    );
  } else {
    body = (
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-xl text-sm text-slate-600 dark:text-slate-300">
          Sign in with the Google account for your work email. You'll confirm your password, then choose the
          account at Google. Available for Gmail and Google Workspace addresses.
        </p>
        <Button variant="secondary" size="sm" loading={busy} leftIcon={FcGoogle} onClick={link}>
          Link Google account
        </Button>
      </div>
    );
  }

  return (
    <div aria-live="polite">
      {body}
      {stepUp.dialog}
    </div>
  );
}
