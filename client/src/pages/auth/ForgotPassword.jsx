import { useState } from "react";
import { Link } from "react-router-dom";
import { FiArrowLeft, FiMail, FiCheckCircle, FiAlertCircle } from "react-icons/fi";
import Card from "../../ui/Card";
import { Field, SubmitButton } from "./fields";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// States: idle (form) | loading | success | error. All dummy — no email is actually sent.
export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("idle"); // idle | loading | success | error
  const [emailError, setEmailError] = useState("");

  const submit = (e) => {
    e.preventDefault();
    if (!isEmail(email)) {
      setEmailError("Enter a valid work email.");
      return;
    }
    setEmailError("");
    setStatus("loading");
    // ponytail: dummy send. `fail@…` forces the error state so it's demoable without a backend.
    setTimeout(() => {
      setStatus(email.trim().toLowerCase().startsWith("fail@") ? "error" : "success");
    }, 800);
  };

  if (status === "success") {
    return (
      <Card padding="xl">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400">
          <FiCheckCircle className="text-2xl" />
        </span>
        <h1 className="mt-5 text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Check your email</h1>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
          Password reset instructions have been sent to your email.
        </p>
        <p className="mt-4 rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
          Sent to <span className="font-semibold text-slate-800 dark:text-slate-100">{email.trim()}</span>. Didn't get it?
          Check spam or{" "}
          <button
            type="button"
            onClick={() => setStatus("idle")}
            className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
          >
            try another email
          </button>.
        </p>
        <Link
          to="/login"
          className="mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
        >
          <FiArrowLeft /> Back to Login
        </Link>
      </Card>
    );
  }

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Reset Password</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        Enter your work email and we'll send you a secure password reset link.
      </p>

      {status === "error" && (
        <div className="mt-6 flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-400">
          <FiAlertCircle className="mt-0.5 shrink-0" />
          <span>We couldn't send the reset link. Please try again in a moment.</span>
        </div>
      )}

      <form onSubmit={submit} noValidate className="mt-8 space-y-5">
        <Field
          label="Work Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={emailError}
        />
        <SubmitButton loading={status === "loading"}>
          <FiMail className="text-base" /> Send Reset Link
        </SubmitButton>
      </form>

      <Link
        to="/login"
        className="mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
      >
        <FiArrowLeft /> Back to Login
      </Link>
    </Card>
  );
}
