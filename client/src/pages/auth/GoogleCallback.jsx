import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { FiAlertCircle } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { resolvePostLogin } from "../../auth/destination";
import { notify } from "../../ui/Toast";
import AuthCard from "./AuthCard";
import { authLink } from "./authStyles";
import { clearPendingSignIn, peekPendingSignIn, safeReturnPath } from "./googleAuth";

// /auth/google/callback — where Google returns the browser (GOOGLE_REDIRECT_URI).
//
// 1. Read ?code&state (or ?error) and immediately drop them from the address bar.
// 2. The returned state must equal the one THIS tab stored when the sign-in started
//    (googleAuth.js). Anything else — no stored attempt, a different state, an expired one —
//    stops here without contacting the API: that is what a forged or replayed callback is.
// 3. POST /auth/google/callback; the server verifies the state signature, the PKCE verifier
//    and Google's ID token, then applies the same account rules as password sign-in.
// 4. On success: the same setSession + resolvePostLogin as Login.jsx.
//
// The authorization code is single-use, so the exchange runs once even under StrictMode's
// double effects (`started` ref).

const GOOGLE_ERRORS = {
  access_denied: "Google sign-in was cancelled.",
};

// Decide, without side effects, whether this callback may be exchanged at all. `linking` is
// read from what THIS tab stored, so the copy matches what the person was doing even when the
// callback itself is refused.
function checkCallback(params) {
  const pending = peekPendingSignIn();
  const linking = pending?.mode === "link";
  const googleError = params.get("error");
  if (googleError) {
    return {
      linking,
      pending,
      failure: GOOGLE_ERRORS[googleError] || "Google couldn't complete the sign-in. Please try again.",
    };
  }
  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state || !pending || pending.state !== state) {
    return { linking, pending, failure: "This Google sign-in expired or was started in another tab. Please try again." };
  }
  return { linking, code, state, pending };
}

export default function GoogleCallback() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setSession } = useAuth();
  const started = useRef(false);
  // Evaluated once, on mount, from the URL Google returned.
  const [check] = useState(() => checkCallback(params));
  const [failure, setFailure] = useState(check.failure || null);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    // Keep the one-time code out of history and out of any Referer, and consume the attempt.
    window.history.replaceState(null, "", window.location.pathname);
    clearPendingSignIn();
    if (check.failure) return;

    const { code, state, pending, linking } = check;
    (async () => {
      try {
        if (linking) {
          // Linking to the account that is ALREADY signed in: the request carries its own
          // session, and the server only accepts a state minted for that session.
          await api.post("/auth/google/link/complete", { code, state, code_verifier: pending.codeVerifier });
          notify.success("Google sign-in is now linked to your account.");
          navigate(safeReturnPath(pending.returnTo), { replace: true });
          return;
        }
        const { data } = await api.post("/auth/google/callback", {
          code,
          state,
          code_verifier: pending.codeVerifier,
          remember: pending.remember,
        });
        const account = await setSession(data, { remember: pending.remember });
        if (!account) {
          setFailure("Signed in, but the session could not be confirmed. Please try again.");
          return;
        }
        notify.success(`Welcome back, ${account.full_name}!`);
        const { to, reason } = await resolvePostLogin(account, pending.from);
        if (reason) notify.error(reason);
        navigate(to, { replace: true });
      } catch (error) {
        setFailure(errMsg(error, "Google sign-in failed. Please try again."));
      }
    })();
  }, [check, navigate, setSession]);

  const back = check.linking
    ? { to: safeReturnPath(check.pending?.returnTo), label: "Back to security settings" }
    : { to: "/login", label: "Back to login" };

  if (failure) {
    return (
      <AuthCard title={check.linking ? "Google account wasn't linked" : "Google sign-in didn't finish"}>
        <p
          role="alert"
          className="mt-[var(--auth-s2)] flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200"
        >
          <FiAlertCircle aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>{failure}</span>
        </p>
        <p className="mt-[var(--auth-s2)] text-center text-[15px]">
          <Link to={back.to} replace className={authLink}>
            {back.label}
          </Link>
        </p>
      </AuthCard>
    );
  }

  const progress = check.linking ? "Linking your Google account" : "Signing you in";
  return (
    <AuthCard
      title={`${progress}…`}
      subtitle={check.linking ? "Finishing the link to your Google account." : "Finishing your Google sign-in."}
    >
      <div className="mt-[var(--auth-s3)] flex justify-center" role="status" aria-label={progress}>
        <span aria-hidden="true" className="zk-spin h-8 w-8 rounded-full border-[3px] border-violet-200 border-t-violet-600" />
      </div>
    </AuthCard>
  );
}
