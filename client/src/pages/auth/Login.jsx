import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errCode, errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton, Checkbox } from "../../ui/forms";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

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

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});
  // ZST-EC-001 IDN-001: correct credentials on an unverified account grant no session.
  // The API answers 403 with code EMAIL_VERIFICATION_REQUIRED and we render a resend
  // action instead of a generic failure toast.
  const [needsVerification, setNeedsVerification] = useState(null); // { email }
  const [resending, setResending] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!isEmail(email)) next.email = "Enter a valid work email.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setLoading(true);
    setNeedsVerification(null);
    try {
      const { data } = await api.post("/auth/login", {
        identifier: email.trim(),
        password,
        remember,
      });
      setSession(data);
      notify.success(`Welcome back, ${data.user.full_name}!`);
      const dest = from ? `${from.pathname}${from.search || ""}` : roleHome(data.user.role) || "/";
      navigate(dest, { replace: true });
    } catch (error) {
      if (errCode(error) === "EMAIL_VERIFICATION_REQUIRED") {
        setNeedsVerification({ email: error?.response?.data?.detail?.email });
        return;
      }
      notify.error(errMsg(error, "Unable to sign in right now."));
    } finally {
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
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Welcome Back</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">Sign in to continue to ZoikoStream.</p>

      <form onSubmit={submit} noValidate className="mt-8 space-y-5">
        <Field
          label="Work Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
        />

        <div>
          <PasswordField
            label="Password"
            autoComplete="current-password"
            placeholder="Enter your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
          />
          <div className="mt-3 flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <Checkbox
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="rounded accent-emerald-600"
              />
              Remember Me
            </label>
            <Link
              to="/forgot-password"
              className="text-sm font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
            >
              Forgot Password?
            </Link>
          </div>
        </div>

        {needsVerification && (
          <div
            role="alert"
            className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-500/40 dark:bg-amber-500/10"
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

        <SubmitButton loading={loading}>Sign In</SubmitButton>

        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          New Organization?{" "}
          <Link to="/signup" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
            Create Organization
          </Link>
        </p>
      </form>

      <p className="mt-6 text-center text-xs text-slate-400 dark:text-slate-500">
        By signing in you agree to the Terms and Privacy Policy.
      </p>
    </Card>
  );
}
