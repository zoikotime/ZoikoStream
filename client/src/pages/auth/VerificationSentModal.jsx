import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { SubmitButton } from "../../ui/forms";

// Shown over the signup form the moment an account is created (ZST-EC-001 IDN-001).
//
// A modal rather than a toast on purpose: registration no longer signs anyone in, so the
// person is stranded unless they understand that a verification step exists. A toast is
// dismissible, easy to miss and gone on the next render — the wrong instrument for the one
// message the whole flow depends on.
//
// `alertdialog` rather than `dialog`: this interrupts to convey a state the user must act
// on, and it is deliberately not dismissible by backdrop click or Escape. Closing it would
// return them to a filled-in form for an account that already exists, whose resubmission
// can only ever 409.
//
// No "Open your inbox" action. Deep-linking a webmail provider means guessing it from the
// address, and guessing wrong sends people to a mailbox that is not theirs.
export default function VerificationSentModal({ maskedEmail, expiresInMinutes, onResend, resending }) {
  const navigate = useNavigate();
  const dialogRef = useRef(null);

  // Focus the dialog itself rather than a child button: SubmitButton forwards props through
  // two component layers, and relying on a ref surviving that is a silent-failure risk.
  // Focusing the labelled container is the standard pattern and announces the title.
  useEffect(() => {
    dialogRef.current?.focus();
    const onKeyDown = (e) => {
      if (e.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll(
        'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm"
      // The backdrop is inert: no onClick handler, so a stray click cannot dismiss this.
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="verify-sent-title"
        aria-describedby="verify-sent-body"
        tabIndex={-1}
        className="zk-fade-in w-full max-w-md rounded-[20px] bg-white p-8 shadow-2xl outline-none dark:bg-slate-900"
      >
        <div
          className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-emerald-50 dark:bg-emerald-500/10"
          aria-hidden="true"
        >
          <svg className="h-7 w-7 text-emerald-600 dark:text-emerald-400" fill="none" viewBox="0 0 24 24"
               stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round"
                  d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </div>

        <h2
          id="verify-sent-title"
          className="mt-5 text-center text-2xl font-bold tracking-tight text-slate-900 dark:text-white"
        >
          Verify your email
        </h2>

        <p id="verify-sent-body" className="mt-3 text-center text-sm leading-relaxed text-slate-600 dark:text-slate-300">
          Welcome to Zoiko Stream. We&rsquo;ve sent a verification email to{" "}
          <span className="font-semibold text-slate-900 dark:text-white">{maskedEmail}</span>. Please
          check your inbox and verify your email before signing in.
        </p>

        {expiresInMinutes ? (
          <p className="mt-3 text-center text-xs text-slate-500 dark:text-slate-400">
            The link expires in {expiresInMinutes} minutes.
          </p>
        ) : null}

        <div className="mt-7 space-y-3">
          <SubmitButton type="button" onClick={() => navigate("/login")}>
            Go to Sign In
          </SubmitButton>

          <button
            type="button"
            onClick={onResend}
            disabled={resending}
            className="w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:opacity-60 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            {resending ? "Sending…" : "Resend verification email"}
          </button>
        </div>

        <p className="mt-5 text-center text-xs text-slate-500 dark:text-slate-400">
          Didn&rsquo;t receive the email? Check your spam folder, then resend.
        </p>
      </div>
    </div>
  );
}
