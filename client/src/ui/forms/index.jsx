import { useId, useState } from "react";
import { FiEye, FiEyeOff, FiChevronDown } from "react-icons/fi";
import { cx } from "../tokens";
import Button from "../Button";

// ─────────────────────────────────────────────────────────────────────────────
// Shared form system. One home for every input/label/validation so pages stop
// re-declaring control class strings and toggle/checkbox markup.
//
// Three visual variants reproduce the app's existing field looks EXACTLY (no
// redesign): "form" (emerald, rounded-xl, shadow — org forms/registration),
// "console" (violet, rounded-lg — modals/console), "auth" (rounded-xl px-4 py-3
// with error-state borders — the login/signup pages).
// ─────────────────────────────────────────────────────────────────────────────
// Each variant had a focus state but no HOVER state, so a form read as flat until it was
// already focused. Hover is a border lift only — it must never look focused or filled.
const CONTROL = {
  form: "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-slate-600",
  console: "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:hover:border-slate-600",
  auth: "w-full rounded-xl border bg-white px-4 py-3 text-slate-800 outline-none transition placeholder:text-slate-400 focus:ring-2 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500",
};
const AUTH_OK = "border-slate-200 focus:border-emerald-500 focus:ring-emerald-500/20 dark:border-slate-700";
const AUTH_BAD = "border-rose-400 focus:border-rose-500 focus:ring-rose-500/20 dark:border-rose-500/60";
const LABEL = {
  form: "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300",
  console: "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300",
  auth: "mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-200",
};
const controlCls = (variant, error) =>
  variant === "auth" ? cx(CONTROL.auth, error ? AUTH_BAD : AUTH_OK) : CONTROL[variant] || CONTROL.form;

export function Label({ variant = "form", htmlFor, className = "", children }) {
  return (
    <label htmlFor={htmlFor} className={cx(LABEL[variant] || LABEL.form, className)}>
      {children}
    </label>
  );
}

// Inline error (priority) or hint text under a control.
export function Note({ error, hint }) {
  if (error) return <p className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>;
  if (hint) return <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{hint}</p>;
  return null;
}

export function Input({ variant = "form", error, className = "", ...props }) {
  return (
    <input className={cx(controlCls(variant, error), className)} aria-invalid={error ? true : undefined} {...props} />
  );
}

export function Textarea({ variant = "form", error, className = "", ...props }) {
  return (
    <textarea className={cx(controlCls(variant, error), className)} aria-invalid={error ? true : undefined} {...props} />
  );
}

// Native select with a built-in chevron. NOTE: `className` styles the WRAPPER (use it
// for width, e.g. "w-28") — the <select> is always w-full inside it so the chevron
// stays pinned to the field's right edge.
export function Select({ variant = "form", error, className = "", children, ...props }) {
  return (
    <div className={cx("relative", className)}>
      <select
        className={cx(controlCls(variant, error), "cursor-pointer appearance-none pr-9")}
        aria-invalid={error ? true : undefined}
        {...props}
      >
        {children}
      </select>
      <FiChevronDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
    </div>
  );
}

// Bare styled checkbox / radio — surrounding label markup stays with the caller so
// existing layouts are untouched; caller passes accent/rounded via className.
export function Checkbox({ className = "", ...props }) {
  return <input type="checkbox" className={cx("h-4 w-4", className)} {...props} />;
}
export function Radio({ className = "", ...props }) {
  return <input type="radio" className={cx("h-4 w-4", className)} {...props} />;
}

// Toggle switch. `label` renders the full-width labeled row (modals); omit for the
// bare switch (settings rows). `accent` picks the on-color.
export function Switch({ checked, onChange, label, accent = "emerald", className = "" }) {
  const track = cx(
    "relative h-5 w-9 shrink-0 rounded-full transition",
    checked ? (accent === "violet" ? "bg-violet-500" : "bg-emerald-500") : "bg-slate-300 dark:bg-slate-600"
  );
  const knob = cx(
    "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition",
    checked ? "left-[18px]" : "left-0.5"
  );
  if (label) {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          "flex w-full items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-700 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800",
          className
        )}
      >
        {label}
        <span className={track}>
          <span className={knob} />
        </span>
      </button>
    );
  }
  return (
    <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className={cx(track, className)}>
      <span className={knob} />
    </button>
  );
}

// Combined label + input + note (the auth-page convenience API). Defaults to the
// "auth" variant; pass variant="form"/"console" to reuse elsewhere.
export function Field({ label, error, hint, variant = "auth", className = "", ...props }) {
  const id = useId();
  return (
    <div>
      {label && <Label variant={variant} htmlFor={id}>{label}</Label>}
      <Input id={id} variant={variant} error={error} className={className} {...props} />
      <Note error={error} hint={hint} />
    </div>
  );
}

export function PasswordField({ label, error, hint, ...props }) {
  const id = useId();
  const [show, setShow] = useState(false);
  return (
    <div>
      <Label variant="auth" htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input id={id} variant="auth" type={show ? "text" : "password"} error={error} className="pr-11" {...props} />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 transition hover:text-emerald-600 dark:hover:text-emerald-400"
          aria-label={show ? "Hide password" : "Show password"}
        >
          {show ? <FiEyeOff /> : <FiEye />}
        </button>
      </div>
      <Note error={error} hint={hint} />
    </div>
  );
}

// Full-width submit built on the design-system Button, with a baked-in loading state.
export function SubmitButton({ loading, children, ...props }) {
  return (
    <Button type="submit" size="lg" disabled={loading} className="w-full" {...props}>
      {loading ? (
        <>
          <span className="zk-spin inline-block h-4 w-4 rounded-full border-2 border-white/40 border-t-white" />
          Please wait…
        </>
      ) : (
        children
      )}
    </Button>
  );
}
