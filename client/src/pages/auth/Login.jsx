import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { FiArrowRight, FiLock, FiMail } from "react-icons/fi";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton, CheckField } from "../../ui/forms";
import AuthTabs from "./AuthTabs";

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

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!isEmail(email)) next.email = "Enter a valid work email.";
    if (!password) next.password = "Enter your password.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setLoading(true);
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
      notify.error(errMsg(error, "Unable to sign in right now."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="zk-fade-in space-y-4">
      <Card
        padding="xl"
        className="rounded-[20px] shadow-xl shadow-slate-900/5 transition-shadow duration-300 hover:shadow-2xl hover:shadow-violet-900/10 dark:shadow-black/30"
      >
        <AuthTabs />

        <h1 className="text-[26px] font-bold tracking-tight text-slate-900 dark:text-white">Welcome back</h1>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">Sign in to your ZoikoStream account</p>

        <form onSubmit={submit} noValidate className="mt-6 space-y-4">
          <Field
            icon={FiMail}
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
              icon={FiLock}
              label="Password"
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              error={errors.password}
            />
            <div className="mt-4 flex items-center justify-between gap-4">
              <CheckField
                label="Remember Me"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              <Link
                to="/forgot-password"
                className="rounded-lg text-sm font-semibold text-emerald-700 underline-offset-4 transition-colors duration-200 hover:text-fuchsia-600 hover:underline dark:text-emerald-400 dark:hover:text-fuchsia-400"
              >
                Forgot Password?
              </Link>
            </div>
          </div>

          <SubmitButton loading={loading} variant="gradient">
            Sign In
          </SubmitButton>
        </form>

      </Card>

      <p className="text-center text-sm text-slate-500 dark:text-slate-400">
        New to ZoikoStream?{" "}
        <Link
          to="/signup"
          className="group inline-flex items-center gap-1.5 rounded-lg font-semibold text-emerald-700 underline-offset-4 transition-colors duration-200 hover:text-fuchsia-600 hover:underline dark:text-emerald-400 dark:hover:text-fuchsia-400"
        >
          Create an organization
          <FiArrowRight
            aria-hidden="true"
            className="transition-transform duration-200 group-hover:translate-x-1 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
          />
        </Link>
      </p>

      {/* ponytail: Terms / Privacy are emphasised text, not links — neither page exists yet,
          and a dead <a> is worse than none. Wrap them in <Link> when the routes land. */}
      <div className="flex items-start gap-3 rounded-2xl border border-slate-200/70 bg-slate-100/60 p-3.5 text-[11.5px] leading-relaxed text-slate-500 dark:border-white/10 dark:bg-white/5 dark:text-slate-400">
        <FiLock className="mt-px shrink-0 text-sm text-slate-400 dark:text-slate-500" aria-hidden="true" />
        <p>
          We take security seriously. Your data is encrypted and never shared. By signing in, you
          agree to our <span className="font-semibold text-emerald-700 dark:text-emerald-400">Terms of Service</span> and{" "}
          <span className="font-semibold text-emerald-700 dark:text-emerald-400">Privacy Policy</span>.
        </p>
      </div>
    </div>
  );
}
