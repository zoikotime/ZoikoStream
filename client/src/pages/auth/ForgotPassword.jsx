import { useState } from "react";
import { Link } from "react-router-dom";
import { FiArrowLeft, FiMail, FiCheckCircle, FiLock } from "react-icons/fi";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { Field, PasswordField, SubmitButton } from "../../ui/forms";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// Steps: email (request code) -> reset (enter code + new password) -> done.
// /auth/reset-password re-validates the code, so there is no separate verify call here.
//
// This page used to state the challenge policy itself: it asked for a "4-digit code",
// capped the input at 4, and promised a 10-minute expiry. The backend mints SIX digits
// (crud/recovery.CODE_DIGITS) and keeps them for FIFTEEN minutes (RECOVERY_TTL_MINUTES),
// so the real emailed code could not physically be typed in. /auth/forgot-password now
// reports both values and they are used below; the constants here are only the fallback
// for a server that predates that field, and they match the backend as it stands.
const DEFAULT_CODE_LENGTH = 6;
const DEFAULT_TTL_MINUTES = 15;

// Digits only, so a pasted "071487 " or "071 487" still lands as 071487. Kept as a STRING
// throughout: the server stores sha256 of the string it mailed, so Number("071487") would
// hash as "71487" and never match. Nothing in this file parses it.
const sanitizeCode = (raw, length) => String(raw ?? "").replace(/\D/g, "").slice(0, length);

export default function ForgotPassword() {
  const [step, setStep] = useState("email"); // email | reset | done
  const [loading, setLoading] = useState(false);
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [codeLength, setCodeLength] = useState(DEFAULT_CODE_LENGTH);
  const [ttlMinutes, setTtlMinutes] = useState(DEFAULT_TTL_MINUTES);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState({});

  const sendCode = async (e) => {
    e.preventDefault();
    if (!isEmail(email)) {
      setErrors({ email: "Enter a valid work email." });
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      const { data } = await api.post("/auth/forgot-password", { email: email.trim() });
      // Only accept sane values; a malformed field must not make the input unusable.
      if (Number.isInteger(data?.code_length) && data.code_length > 0) setCodeLength(data.code_length);
      if (Number.isInteger(data?.expires_in_minutes) && data.expires_in_minutes > 0) {
        setTtlMinutes(data.expires_in_minutes);
      }
      setStep("reset");
    } catch (error) {
      notify.error(errMsg(error, "We couldn't send the reset code right now."));
    } finally {
      setLoading(false);
    }
  };

  const resetPassword = async (e) => {
    e.preventDefault();
    const errs = {};
    const code = sanitizeCode(otp, codeLength);
    if (!code) errs.otp = "Enter the verification code.";
    else if (code.length < codeLength) errs.otp = `Enter the ${codeLength}-digit code.`;
    if (password.length < 8) errs.password = "Use at least 8 characters.";
    if (confirm !== password) errs.confirm = "Passwords don't match.";
    if (Object.keys(errs).length) {
      setErrors(errs);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      await api.post("/auth/reset-password", { email: email.trim(), otp: code, password });
      setStep("done");
    } catch (error) {
      notify.error(errMsg(error, "That code is invalid or expired. Request a new one."));
    } finally {
      setLoading(false);
    }
  };

  if (step === "done") {
    return (
      <Card padding="xl">
        <span className="grid h-12 w-12 place-items-center rounded-2xl bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400">
          <FiCheckCircle className="text-2xl" />
        </span>
        <h1 className="mt-5 text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Password updated</h1>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
          Your password has been changed. You can now log in with your new password.
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

  if (step === "reset") {
    return (
      <Card padding="xl">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Enter reset code</h1>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
          We sent a {codeLength}-digit code to <span className="font-semibold text-slate-800 dark:text-slate-100">{email.trim()}</span>.
          Enter it below with your new password. The code expires in {ttlMinutes} minutes.
        </p>

        <form onSubmit={resetPassword} noValidate className="mt-8 space-y-5">
          <Field
            label={`${codeLength}-digit code`}
            inputMode="numeric"
            // pattern for the numeric keypad on mobile. Deliberately NO maxLength: that
            // attribute truncates the RAW value before onChange runs, so pasting a code
            // copied out of an email with its spaces (" 071 487 ") was cut to six raw
            // characters and sanitized down to "0714" — two real digits silently lost.
            // sanitizeCode is the single authority on length, and it counts digits.
            pattern="[0-9]*"
            autoComplete="one-time-code"
            placeholder={"0".repeat(codeLength)}
            value={otp}
            onChange={(e) => setOtp(sanitizeCode(e.target.value, codeLength))}
            error={errors.otp}
          />
          <PasswordField
            label="New password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
          />
          <PasswordField
            label="Confirm new password"
            autoComplete="new-password"
            placeholder="Re-enter your new password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={errors.confirm}
          />
          <SubmitButton loading={loading}>
            <FiLock className="text-base" /> Reset Password
          </SubmitButton>
        </form>

        <button
          type="button"
          onClick={() => { setStep("email"); setOtp(""); setPassword(""); setConfirm(""); setErrors({}); }}
          className="mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-emerald-700 hover:underline dark:text-emerald-400"
        >
          <FiArrowLeft /> Use a different email
        </button>
      </Card>
    );
  }

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Reset Password</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        Enter your work email and we'll send you a {DEFAULT_CODE_LENGTH}-digit code to reset your password.
      </p>

      <form onSubmit={sendCode} noValidate className="mt-8 space-y-5">
        <Field
          label="Work Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
        />
        <SubmitButton loading={loading}>
          <FiMail className="text-base" /> Send Reset Code
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
