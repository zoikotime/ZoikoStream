import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { SESSION_END_MESSAGES, clearSessionEndReason, peekSessionEndReason } from "../../auth/sessionStore";
import { FiCheck, FiLock, FiMail } from "react-icons/fi";
import { notify } from "../../ui/Toast";
import { cx } from "../../ui/tokens";
import api, { errCode, errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { resolvePostLogin } from "../../auth/destination";
import { Field, PasswordField, CheckField } from "../../ui/forms";
import AuthCard from "./AuthCard";
import AuthSubmit from "./AuthSubmit";
import GoogleSignIn from "./GoogleSignIn";
import { IS_NATIVE } from "../../platform";
import { authLink } from "./authStyles";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// One toast slot for "this sign-in failed". A refused or unreachable sign-in fails in
// milliseconds, so Enter-then-click, or simply trying again, used to stack identical red
// toasts; under one id each new failure REPLACES the one showing (ui/Toast.js).
const SIGN_IN_TOAST = "auth:sign-in";

// One login page for every role. After login the (later) backend decides the role; for now
// fakeSession() infers it from the email so every dashboard is reachable from here.
export default function Login() {
  const { setSession } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Set by RoleRoute/ProtectedRoute when signing in was forced by hitting a gated URL
  // directly (e.g. an assignment-notification link to /moderator/dashboard?event=<id>
  // while signed out) — land back on THAT page, not the bare role dashboard.
  const from = location.state?.from;

  // Read without consuming during render (StrictMode renders twice), cleared once mounted so
  // a later visit to /login does not repeat it. Shown only when a session actually ended, and
  // dismissed as soon as the user starts a new sign-in — it explains the form, it is not a
  // standing part of it.
  const [endedReason, setEndedReason] = useState(() => peekSessionEndReason());
  useEffect(() => {
    clearSessionEndReason();
  }, []);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // "Remember me": keep this sign-in across browser restarts (on), or only for this browser
  // session (off). Either way the session still ends after inactivity or at its maximum
  // length — remembering never makes it last longer.
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});
  // ZST-EC-001 IDN-001: correct credentials on an unverified account grant no session.
  // The API answers 403 with code EMAIL_VERIFICATION_REQUIRED and we render a resend
  // action instead of a generic failure toast.
  const [needsVerification, setNeedsVerification] = useState(null); // { email }
  const [resending, setResending] = useState(false);
  // Success is shown only once the SERVER has confirmed the session (setSession resolved an
  // account) — never optimistically on the login response alone.
  const [signedIn, setSignedIn] = useState(false);
  // One short shake of the form on any rejected attempt (validation or server).
  const [shake, setShake] = useState(false);
  // Whether a sign-in request is in flight. A ref, not the `loading` state: the button only
  // turns disabled after React re-renders, and a second submit (Enter then a click, a double
  // tap) can arrive before that. Read and written synchronously, so it cannot.
  const inFlight = useRef(false);

  const submit = async (e) => {
    e.preventDefault();
    if (inFlight.current) return;
    setEndedReason(null);
    const next = {};
    if (!isEmail(email)) next.email = "Enter a valid work email.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    if (Object.keys(next).length) {
      setShake(true);
      return;
    }

    inFlight.current = true;
    setLoading(true);
    setNeedsVerification(null);
    try {
      const { data } = await api.post("/auth/login", {
        identifier: email.trim(),
        password,
        remember,
      });
      // setSession stores the token and CONFIRMS it with GET /auth/me before returning, so
      // the redirect below is driven by the server's answer rather than by the login
      // payload we happen to be holding. Awaiting it also means the destination route never
      // renders while auth state is still resolving.
      const account = await setSession(data, { remember });
      if (!account) {
        notify.error("Signed in, but the session could not be confirmed. Please try again.", { id: SIGN_IN_TOAST });
        setShake(true);
        return;
      }
      setSignedIn(true);
      notify.dismiss?.(SIGN_IN_TOAST);            // an earlier attempt's failure is now stale
      notify.success(`Welcome back, ${account.full_name}!`);
      // One authoritative destination. `from` is a REQUEST, not a permission: a saved
      // event-console deep-link is honoured only after the backend confirms the assignment
      // for that exact event, and anything it refuses falls back to the account's own home
      // with a reason rather than dropping the user somewhere unexplained.
      const { to, reason } = await resolvePostLogin(account, from);
      if (reason) notify.error(reason);
      navigate(to, { replace: true });
    } catch (error) {
      setSignedIn(false);
      if (errCode(error) === "EMAIL_VERIFICATION_REQUIRED") {
        setNeedsVerification({ email: error?.response?.data?.detail?.email });
        return;
      }
      notify.error(errMsg(error, "Unable to sign in right now."), { id: SIGN_IN_TOAST });
      setShake(true);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  };

  const resendVerification = async () => {
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

  return (
    <AuthCard tabs title="Welcome back!" subtitle="Sign in to your ZoikoStream account.">
      {/* Why the previous session ended, as the server reported it (idle, maximum length,
          signed out elsewhere). Shown once, never as a generic error. */}
      {endedReason && (
        <p
          role="status"
          data-testid="session-ended"
          className="mt-[calc(14*var(--u))] rounded-[var(--auth-radius)] border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200"
        >
          {SESSION_END_MESSAGES[endedReason] || SESSION_END_MESSAGES.reauth}
        </p>
      )}

      {/* Google first, then email + password (GoogleSignIn.jsx, googleAuth.js). Not in the
          Android app: Google refuses OAuth inside embedded WebViews (disallowed_useragent),
          so a button there could never succeed — the form stands alone. */}
      {!IS_NATIVE && (
        <>
          <GoogleSignIn remember={remember} from={from} className="mt-[calc(17*var(--u))]" />
          <div className="mt-[calc(19*var(--u))] flex items-center gap-[calc(16*var(--u))] text-[length:max(13.5px,calc(15.5*var(--u)))] leading-[1.4] text-[#5f6684] dark:text-slate-400">
            <span aria-hidden="true" className="h-px flex-1 bg-[#c9cddb] dark:bg-white/10" />
            or sign in with email
            <span aria-hidden="true" className="h-px flex-1 bg-[#c9cddb] dark:bg-white/10" />
          </div>
        </>
      )}

      <form
        onSubmit={submit}
        noValidate
        onAnimationEnd={(e) => e.animationName === "zk-shake" && setShake(false)}
        className={cx(IS_NATIVE ? "mt-[var(--auth-s3)]" : "mt-[calc(14*var(--u))]", "space-y-[calc(8*var(--u))]", shake && "zk-shake")}
      >
        <Field
          icon={FiMail}
          label="Work email address"
          type="email"
          autoComplete="email"
          placeholder="name@company.com"
          className="h-[var(--auth-ctl)] py-0! pl-[calc(62*var(--u))]!"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setEndedReason(null);
          }}
          error={errors.email}
        />

        <div>
          <PasswordField
            icon={FiLock}
            label="Password"
            autoComplete="current-password"
            placeholder="Enter your password"
            className="h-[var(--auth-ctl)] py-0! pl-[calc(62*var(--u))]! pr-12"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setEndedReason(null);
            }}
            error={errors.password}
          />
          <div className="mt-[calc(11*var(--u))] flex items-center justify-between gap-4">
            {/* Not "for 30 days": remembering only keeps the sign-in across browser
                restarts. The server's idle and maximum-length limits still apply. */}
            <CheckField
              className="zk-auth-check"
              label="Remember me"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            {/* inline-flex items-center: in the Android app the 44px touch-target floor
                (index.css) makes this link taller than its text — keep the text centred so
                it stays level with Remember me. No effect on web, where it is text-height. */}
            <Link to="/forgot-password" className={cx("inline-flex items-center text-[length:var(--auth-text)] font-medium! text-[#5b2fcf]! dark:text-violet-300!", authLink)}>
              Forgot password?
            </Link>
          </div>
        </div>

        {needsVerification && (
          <div
            role="alert"
            className="rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10"
          >
            <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">
              Verify your email to continue.
            </p>
            <p className="mt-1 text-sm text-amber-800 dark:text-amber-300/90">
              We need to confirm{" "}
              {needsVerification.email ? (
                <span className="font-medium">{needsVerification.email}</span>
              ) : (
                "your address"
              )}{" "}
              before you can sign in.
            </p>
            <button
              type="button"
              onClick={resendVerification}
              disabled={resending}
              className="mt-3 text-sm font-semibold text-amber-900 underline hover:no-underline disabled:opacity-60 dark:text-amber-200"
            >
              {resending ? "Sending…" : "Resend verification email"}
            </button>
          </div>
        )}

        <AuthSubmit loading={loading && !signedIn} disabled={signedIn} className="flex! !mt-[calc(17*var(--u))]">
          {signedIn ? (
            <>
              <FiCheck aria-hidden="true" className="text-lg" /> Signed in
            </>
          ) : (
            "Log In"
          )}
        </AuthSubmit>

        {/* Spoken progress for screen readers; failures are announced by the toast. */}
        <p role="status" className="sr-only">
          {signedIn ? "Signed in. Redirecting…" : loading ? "Signing in…" : ""}
        </p>
      </form>

      <p className="mt-[calc(22*var(--u))] text-center text-[length:max(15px,calc(17.5*var(--u)))] leading-[1.5] text-[#4b5070] dark:text-slate-400">
        New to ZoikoStream?
        <Link to="/signup" className={cx("ml-[calc(18*var(--u))] text-[length:max(15.5px,calc(19*var(--u)))] font-bold! text-[#7c2eb5]! dark:text-fuchsia-300!", authLink)}>
          Register
        </Link>
      </p>
    </AuthCard>
  );
}
