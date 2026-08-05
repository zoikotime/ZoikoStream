import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton, Checkbox } from "../../ui/forms";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// One login page for every role. After login the (later) backend decides the role; for now
// fakeSession() infers it from the email so every dashboard is reachable from here.
export default function Login() {
  const { setSession } = useAuth();
  const navigate = useNavigate();

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
      navigate(roleHome(data.user.role) || "/", { replace: true });
    } catch (error) {
      notify.error(errMsg(error, "Unable to sign in right now."));
    } finally {
      setLoading(false);
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
