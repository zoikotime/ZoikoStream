import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { FiEye, FiEyeOff, FiZap } from "react-icons/fi";
import api, { errMsg } from "../api";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roleHome";

const TABS = [
  ["login", "Login"],
  ["register", "Register"],
  ["reset", "Reset Password"],
];

// Shared field styling.
const field =
  "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-800 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";
const label = "mb-1.5 block text-sm font-semibold text-emerald-700";

function Label({ children }) {
  return <label className={label}>{children}</label>;
}

function PasswordInput({ value, onChange, placeholder = "", autoComplete }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        className={`${field} pr-11`}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        autoComplete={autoComplete}
        required
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-emerald-600"
        aria-label={show ? "Hide password" : "Show password"}
      >
        {show ? <FiEyeOff /> : <FiEye />}
      </button>
    </div>
  );
}

function SubmitButton({ loading, children }) {
  return (
    <button
      type="submit"
      disabled={loading}
      className="w-full rounded-xl bg-emerald-600 py-3.5 text-base font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:opacity-60"
    >
      {loading ? "Please wait…" : children}
    </button>
  );
}

export default function AuthPage() {
  const [tab, setTab] = useState("login");
  const [loading, setLoading] = useState(false);
  const { setSession } = useAuth();
  const navigate = useNavigate();

  // Login
  const [identifier, setIdentifier] = useState("");
  const [loginPw, setLoginPw] = useState("");
  const [remember, setRemember] = useState(true);

  // Register
  const [reg, setReg] = useState({
    full_name: "",
    organization_name: "",
    email: "",
    username: "",
    password: "",
  });
  const setRegField = (k) => (e) => setReg((r) => ({ ...r, [k]: e.target.value }));

  // Reset (2-step: request token, then set new password)
  const [resetEmail, setResetEmail] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [newPw, setNewPw] = useState("");
  const [resetStep, setResetStep] = useState("request");

  const enterApp = (data) => {
    setSession(data);
    navigate(roleHome(data.user.role), { replace: true });
  };

  const submitLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/login", {
        identifier,
        password: loginPw,
        remember,
      });
      enterApp(data);
      toast.success(`Welcome back, ${data.user.full_name}!`);
    } catch (err) {
      toast.error(errMsg(err, "Login failed"));
    } finally {
      setLoading(false);
    }
  };

  const submitRegister = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/register", reg);
      enterApp(data);
      toast.success("Account created!");
    } catch (err) {
      toast.error(errMsg(err, "Registration failed"));
    } finally {
      setLoading(false);
    }
  };

  const requestReset = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/forgot-password", { email: resetEmail });
      // ponytail: dev has no email service — backend returns the token so we can prefill it.
      if (data.dev_reset_token) setResetToken(data.dev_reset_token);
      setResetStep("apply");
      toast.success(data.message);
    } catch (err) {
      toast.error(errMsg(err, "Could not send reset link"));
    } finally {
      setLoading(false);
    }
  };

  const applyReset = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { data } = await api.post("/auth/reset-password", {
        token: resetToken,
        password: newPw,
      });
      toast.success(data.message);
      setTab("login");
      setResetStep("request");
      setNewPw("");
    } catch (err) {
      toast.error(errMsg(err, "Reset failed"));
    } finally {
      setLoading(false);
    }
  };

  const goForgot = () => {
    setTab("reset");
    setResetStep("request");
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel */}
      <div className="relative hidden flex-col justify-between bg-gradient-to-br from-emerald-600 to-teal-700 p-12 text-white lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-white/20">
            <FiZap className="text-xl" />
          </span>
          <span className="text-xl font-bold tracking-tight">ZoikoStream</span>
        </div>
        <div>
          <h2 className="text-4xl font-bold leading-tight">Stream your events to the world.</h2>
          <p className="mt-4 max-w-md text-emerald-50/90">
            Host, manage and analyze live events from one dashboard. Sign in to pick up where you
            left off.
          </p>
        </div>
        <p className="text-sm text-emerald-50/70">© 2026 Zoiko Group</p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center bg-slate-50 p-6">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
          {/* Tabs */}
          <div className="mb-8 flex gap-6 border-b border-slate-200">
            {TABS.map(([key, name]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`-mb-px border-b-2 pb-3 text-sm font-semibold transition ${
                  tab === key
                    ? "border-emerald-600 text-emerald-700"
                    : "border-transparent text-slate-400 hover:text-slate-600"
                }`}
              >
                {name}
              </button>
            ))}
          </div>

          {tab === "login" && (
            <form onSubmit={submitLogin} className="space-y-5">
              <div>
                <h1 className="text-3xl font-bold text-slate-900">Welcome back!</h1>
                <p className="mt-1 text-sm text-slate-500">
                  Enter your credentials to access your account.
                </p>
              </div>
              <div>
                <Label>Username or Email Address</Label>
                <input
                  className={field}
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  autoComplete="username"
                  required
                />
              </div>
              <div>
                <Label>Password</Label>
                <PasswordInput
                  value={loginPw}
                  onChange={(e) => setLoginPw(e.target.value)}
                  autoComplete="current-password"
                />
                <div className="mt-2 text-right">
                  <button
                    type="button"
                    onClick={goForgot}
                    className="text-sm font-semibold text-emerald-700 hover:underline"
                  >
                    Forgot Password?
                  </button>
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="h-4 w-4 rounded accent-emerald-600"
                />
                Remember for 30 days
              </label>
              <SubmitButton loading={loading}>Log In</SubmitButton>
              <p className="text-center text-sm text-slate-500">
                New to ZoikoStream?{" "}
                <button
                  type="button"
                  onClick={() => setTab("register")}
                  className="font-semibold text-emerald-700 hover:underline"
                >
                  Create Account
                </button>
              </p>
            </form>
          )}

          {tab === "register" && (
            <form onSubmit={submitRegister} className="space-y-4">
              <div>
                <h1 className="text-3xl font-bold text-slate-900">Create your account</h1>
                <p className="mt-1 text-sm text-slate-500">
                  Set up your organization and start streaming.
                </p>
              </div>
              <div>
                <Label>Full Name</Label>
                <input className={field} value={reg.full_name} onChange={setRegField("full_name")} required />
              </div>
              <div>
                <Label>Organization Name</Label>
                <input
                  className={field}
                  value={reg.organization_name}
                  onChange={setRegField("organization_name")}
                  required
                />
              </div>
              <div>
                <Label>Email Address</Label>
                <input type="email" className={field} value={reg.email} onChange={setRegField("email")} required />
              </div>
              <div>
                <Label>Username</Label>
                <input
                  className={field}
                  value={reg.username}
                  onChange={setRegField("username")}
                  minLength={3}
                  required
                />
              </div>
              <div>
                <Label>Password</Label>
                <PasswordInput
                  value={reg.password}
                  onChange={setRegField("password")}
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                />
              </div>
              <SubmitButton loading={loading}>Create Account</SubmitButton>
              <p className="text-center text-sm text-slate-500">
                Already have an account?{" "}
                <button
                  type="button"
                  onClick={() => setTab("login")}
                  className="font-semibold text-emerald-700 hover:underline"
                >
                  Log In
                </button>
              </p>
            </form>
          )}

          {tab === "reset" && (
            <div className="space-y-5">
              <div>
                <h1 className="text-3xl font-bold text-slate-900">Reset password</h1>
                <p className="mt-1 text-sm text-slate-500">
                  {resetStep === "request"
                    ? "Enter your email and we'll send a reset link."
                    : "Enter the reset token and your new password."}
                </p>
              </div>

              {resetStep === "request" ? (
                <form onSubmit={requestReset} className="space-y-5">
                  <div>
                    <Label>Email Address</Label>
                    <input
                      type="email"
                      className={field}
                      value={resetEmail}
                      onChange={(e) => setResetEmail(e.target.value)}
                      required
                    />
                  </div>
                  <SubmitButton loading={loading}>Send Reset Link</SubmitButton>
                </form>
              ) : (
                <form onSubmit={applyReset} className="space-y-5">
                  <div>
                    <Label>Reset Token</Label>
                    <input
                      className={field}
                      value={resetToken}
                      onChange={(e) => setResetToken(e.target.value)}
                      required
                    />
                  </div>
                  <div>
                    <Label>New Password</Label>
                    <PasswordInput
                      value={newPw}
                      onChange={(e) => setNewPw(e.target.value)}
                      placeholder="At least 8 characters"
                      autoComplete="new-password"
                    />
                  </div>
                  <SubmitButton loading={loading}>Update Password</SubmitButton>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
