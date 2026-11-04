import { useId, useState } from "react";
import { FiInfo } from "react-icons/fi";
import { FcGoogle } from "react-icons/fc";
import { errMsg } from "../../api";
import { startGoogleSignIn } from "./googleAuth";

// "Continue with Google" — the login page's federated option (OpenID Connect + PKCE; the
// flow is described in googleAuth.js and server/app/services/google_oauth.py).
//
// Pressing it asks the API to start a sign-in and sends the browser to Google; the result is
// finished on /auth/google/callback (GoogleCallback.jsx). Until the server has its GOOGLE_*
// credentials, /auth/google/start answers 503 and the message below says so plainly — the
// button never pretends a sign-in happened.
export default function GoogleSignIn({ remember = false, from = null, className = "" }) {
  const noticeId = useId();
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState(null);

  const start = async () => {
    if (pending) return;
    setPending(true);
    setNotice(null);
    try {
      await startGoogleSignIn({ remember, from });
      // Navigating away: leave the spinner up rather than flash the idle button.
    } catch (error) {
      setNotice(errMsg(error, "Google sign-in couldn't start. Please try again."));
      setPending(false);
    }
  };

  return (
    <div className={className}>
      <button
        type="button"
        onClick={start}
        disabled={pending}
        aria-busy={pending || undefined}
        aria-describedby={notice ? noticeId : undefined}
        className="group flex h-[var(--auth-ctl)] w-full items-center justify-center gap-2 whitespace-nowrap rounded-xl border border-slate-300 bg-white px-3 text-[14px] font-semibold text-slate-800 shadow-[0_1px_2px_rgba(15,23,42,0.05)] transition duration-200 hover:border-slate-400 hover:bg-slate-50 hover:shadow-[0_6px_16px_-10px_rgba(15,23,42,0.35)] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-violet-500/25 disabled:cursor-wait disabled:opacity-80 motion-reduce:transition-none sm:gap-3 sm:px-4 sm:text-[15px] dark:border-white/15 dark:bg-white/[0.03] dark:text-slate-100 dark:hover:border-white/25 dark:hover:bg-white/[0.06]"
      >
        {pending ? (
          <span aria-hidden="true" className="zk-spin h-5 w-5 shrink-0 rounded-full border-2 border-slate-300 border-t-slate-700" />
        ) : (
          <FcGoogle aria-hidden="true" className="h-5 w-5 shrink-0" />
        )}
        {pending ? "Redirecting to Google…" : "Continue with Google"}
      </button>

      {/* Always mounted so screen readers announce the message when it appears. */}
      <div id={noticeId} role="status" aria-live="polite">
        {notice && (
          <p className="zk-fade-in mt-[var(--auth-s1)] flex items-start gap-2.5 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-[13px] text-violet-900 dark:border-violet-400/30 dark:bg-violet-500/10 dark:text-violet-100">
            <FiInfo aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>{notice}</span>
          </p>
        )}
      </div>
    </div>
  );
}
