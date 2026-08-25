import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errCode, errMsg } from "../../api";
import { SubmitButton } from "../../ui/forms";

// ZST-EC-001 IDN-001 — landing page for the emailed verification link.
//
// The server is authoritative: this page holds no verification state of its own, it only
// renders the outcome the API returned. Nothing here can mark an account verified, and a
// success view is never shown unless /auth/verify-email answered 200.
const STATES = {
  verifying: {
    title: "Verifying your email",
    body: "One moment while we confirm your address.",
  },
  success: {
    title: "Email verified successfully",
    body: "Your Zoiko Stream account is verified. You can now sign in with your email and password.",
  },
  expired: {
    title: "This link has expired",
    body: "Verification links are short-lived for your security. Request a new one below.",
  },
  already_used: {
    title: "This link has already been used",
    body: "Your address may already be verified. Try signing in, or request a new link.",
  },
  invalid: {
    title: "This link is not valid",
    body: "It may have been replaced by a newer email. Check your inbox for the most recent message, or request a new link.",
  },
  missing: {
    title: "No verification token",
    body: "Open the link from your verification email, or request a new one below.",
  },
};

const CODE_TO_STATE = {
  TOKEN_EXPIRED: "expired",
  TOKEN_ALREADY_USED: "already_used",
  TOKEN_INVALID: "invalid",
};

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token");

  const [state, setState] = useState(token ? "verifying" : "missing");
  const [email, setEmail] = useState("");
  const [resending, setResending] = useState(false);
  // React 18+ StrictMode mounts effects twice in development. Verification is single-use,
  // so a second call would consume-then-report "already used" and the user would see a
  // failure for a link that actually worked.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;
    api
      .post("/auth/verify-email", { token })
      .then(() => setState("success"))
      .catch((error) => setState(CODE_TO_STATE[errCode(error)] || "invalid"));
  }, [token]);

  const resend = async (e) => {
    e.preventDefault();
    if (!email.trim()) {
      notify.error("Enter the email address you registered with.");
      return;
    }
    setResending(true);
    try {
      const { data } = await api.post("/auth/resend-verification", { email: email.trim() });
      notify.success(data?.message || "If that address needs verification, a new link has been sent.");
    } catch (error) {
      notify.error(errMsg(error, "Could not send a new link right now."));
    } finally {
      setResending(false);
    }
  };

  const copy = STATES[state];
  const canResend = state === "expired" || state === "invalid" || state === "missing" || state === "already_used";

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">{copy.title}</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">{copy.body}</p>

      {state === "verifying" && (
        <div className="mt-8 flex items-center gap-3 text-sm text-slate-500 dark:text-slate-400">
          <span
            className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-emerald-600 motion-reduce:animate-none"
            aria-hidden="true"
          />
          <span role="status">Checking your verification link…</span>
        </div>
      )}

      {state === "success" && (
        <div className="mt-8 space-y-4">
          <SubmitButton type="button" onClick={() => navigate("/login", { replace: true })}>
            Continue to Sign In
          </SubmitButton>
        </div>
      )}

      {canResend && (
        <form onSubmit={resend} noValidate className="mt-8 space-y-4">
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-200" htmlFor="verify-email">
            Email address
          </label>
          <input
            id="verify-email"
            type="email"
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          />
          <SubmitButton loading={resending}>Resend verification email</SubmitButton>
        </form>
      )}

      <p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
        <Link to="/login" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
          Back to sign in
        </Link>
      </p>
    </Card>
  );
}
