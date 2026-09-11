import { useId, useState } from "react";
import { FiCheck, FiEye, FiEyeOff, FiChevronDown } from "react-icons/fi";
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
  // Auth fields carry the roomiest geometry (px-4 py-3.5, rounded-xl) and a wide, soft
  // focus halo rather than a hard 2px ring — the sign-in card is the one screen where the
  // control IS the interface.
  auth: "peer w-full rounded-xl border bg-white px-4 py-3.5 text-slate-800 outline-none transition duration-200 placeholder:text-slate-400 focus:ring-4 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500",
};
const AUTH_OK =
  "border-slate-200 hover:border-slate-300 focus:border-emerald-500 focus:ring-emerald-500/15 dark:border-slate-700 dark:hover:border-slate-600";
const AUTH_BAD = "border-rose-400 focus:border-rose-500 focus:ring-rose-500/15 dark:border-rose-500/60";
const LABEL = {
  form: "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300",
  console: "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300",
  auth: "mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-200",
};
// The form/console variants bake their border colour into one string, so an error can't
// recolour it without depending on utility order. A resting rose ring is unambiguous instead:
// nothing else paints a ring at rest, and the variant's own focus:ring still wins on focus.
const ERROR_RING = "ring-2 ring-rose-400/70 dark:ring-rose-500/60";
const controlCls = (variant, error) =>
  variant === "auth"
    ? cx(CONTROL.auth, error ? AUTH_BAD : AUTH_OK)
    : cx(CONTROL[variant] || CONTROL.form, error && ERROR_RING);

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

// `icon` renders a leading glyph inside the control (auth fields). Optional — without it
// the markup is exactly what it always was, so no existing caller changes shape.
export function Input({ variant = "form", error, icon: Icon, className = "", ...props }) {
  const input = (
    <input
      className={cx(controlCls(variant, error), Icon && "pl-11", className)}
      aria-invalid={error ? true : undefined}
      {...props}
    />
  );
  if (!Icon) return input;
  // Input first so the icon can be its CSS peer — it tints with the field's own focus
  // state. Visual order is unaffected: the icon is absolutely positioned.
  return (
    <div className="relative">
      {input}
      <Icon
        className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400 transition-colors duration-200 peer-focus:text-violet-500 dark:text-slate-500 dark:peer-focus:text-violet-400"
        aria-hidden="true"
      />
    </div>
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

// Labelled checkbox with a drawn box instead of the browser's. Still a real
// <input type="checkbox"> inside a <label>, so keyboard, space-to-toggle, form semantics
// and every existing checked/onChange handler behave exactly as before — only the paint
// is ours. The tick scales in; the box fills violet.
export function CheckField({ label, error, className = "", ...props }) {
  return (
    <div className={className}>
      <label className="group inline-flex cursor-pointer select-none items-start gap-3 text-sm text-slate-600 dark:text-slate-300">
        <span className="relative mt-px grid h-5 w-5 shrink-0 place-items-center">
          <input
            type="checkbox"
            className="peer h-5 w-5 cursor-pointer appearance-none rounded-[7px] border-2 border-slate-300 bg-white transition duration-200 checked:border-violet-600 checked:bg-violet-600 hover:border-violet-400 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-violet-500/25 checked:hover:bg-violet-500 dark:border-slate-600 dark:bg-slate-900 dark:checked:border-violet-500 dark:checked:bg-violet-500"
            {...props}
          />
          <FiCheck
            aria-hidden="true"
            strokeWidth={3}
            className="pointer-events-none absolute scale-50 text-[13px] text-white opacity-0 transition duration-200 peer-checked:scale-100 peer-checked:opacity-100 motion-reduce:transition-none"
          />
        </span>
        <span className="transition-colors duration-200 group-hover:text-slate-900 dark:group-hover:text-white">
          {label}
        </span>
      </label>
      <Note error={error} />
    </div>
  );
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

// `variant` defaults to "auth" so every existing caller (Login, ForgotPassword, invitation
// and org creation) renders byte-identically; Settings -> Security passes "form" so the
// change-password fields match the controls around them instead of importing the sign-in
// card's roomier geometry into a settings panel.
export function PasswordField({ label, error, hint, variant = "auth", ...props }) {
  const id = useId();
  const [show, setShow] = useState(false);
  return (
    <div>
      <Label variant={variant} htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input id={id} variant={variant} type={show ? "text" : "password"} error={error} className="pr-12" {...props} />
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          className="absolute right-2 top-1/2 grid h-8 w-8 -translate-y-1/2 place-items-center rounded-lg text-slate-400 transition duration-200 hover:bg-slate-100 hover:text-violet-600 active:scale-95 motion-reduce:transition-none motion-reduce:active:scale-100 dark:hover:bg-white/10 dark:hover:text-violet-400"
          aria-label={show ? "Hide password" : "Show password"}
          aria-pressed={show}
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
