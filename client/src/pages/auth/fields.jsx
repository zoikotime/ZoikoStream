import { useId, useState } from "react";
import { FiEye, FiEyeOff } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Button from "../../ui/Button";

// Shared, accessible auth form controls. Dark-mode + proper label/id association + inline
// validation, all in one place so the four auth pages stay consistent.
const base =
  "w-full rounded-xl border bg-white px-4 py-3 text-slate-800 outline-none transition placeholder:text-slate-400 focus:ring-2 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500";
const okBorder = "border-slate-200 focus:border-emerald-500 focus:ring-emerald-500/20 dark:border-slate-700";
const badBorder = "border-rose-400 focus:border-rose-500 focus:ring-rose-500/20 dark:border-rose-500/60";

const labelCls = "mb-1.5 block text-sm font-semibold text-slate-700 dark:text-slate-200";

function Note({ error, hint }) {
  if (error) return <p className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>;
  if (hint) return <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{hint}</p>;
  return null;
}

export function Field({ label, error, hint, className = "", ...props }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className={labelCls}>{label}</label>
      <input
        id={id}
        className={cx(base, error ? badBorder : okBorder, className)}
        aria-invalid={error ? true : undefined}
        {...props}
      />
      <Note error={error} hint={hint} />
    </div>
  );
}

export function PasswordField({ label, error, hint, ...props }) {
  const id = useId();
  const [show, setShow] = useState(false);
  return (
    <div>
      <label htmlFor={id} className={labelCls}>{label}</label>
      <div className="relative">
        <input
          id={id}
          type={show ? "text" : "password"}
          className={cx(base, "pr-11", error ? badBorder : okBorder)}
          aria-invalid={error ? true : undefined}
          {...props}
        />
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
