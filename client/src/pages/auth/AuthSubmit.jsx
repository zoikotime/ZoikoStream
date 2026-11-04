import { cx } from "../../ui/tokens";

// The auth pages' primary button: full width, violet → pink, with a built-in loading state.
// Violet → pink at rest (reference design); the gradient is painted at 2× width and slides on
// hover toward pink → violet. Every stop keeps white text at ≥ 4.5:1.
export default function AuthSubmit({ loading = false, disabled = false, className = "", children, ...props }) {
  return (
    <button
      type="submit"
      disabled={loading || disabled}
      aria-busy={loading || undefined}
      className={cx(
        "group inline-flex h-[var(--auth-ctl,52px)] w-full items-center justify-center gap-2 rounded-xl px-6 text-base font-semibold text-white",
        "bg-gradient-to-r from-violet-600 via-pink-600 to-violet-600 bg-[length:200%_auto] bg-[position:0%_50%]",
        "shadow-[0_14px_30px_-12px_rgba(147,51,234,0.65)] transition-all duration-300",
        "hover:bg-[position:100%_50%] hover:shadow-[0_18px_38px_-12px_rgba(219,39,119,0.6)] active:scale-[0.99]",
        "focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-violet-500/35",
        "disabled:cursor-not-allowed disabled:opacity-70 motion-reduce:transition-none motion-reduce:active:scale-100",
        className
      )}
      {...props}
    >
      {loading ? (
        <>
          <span aria-hidden="true" className="zk-spin inline-block h-4 w-4 rounded-full border-2 border-white/40 border-t-white" />
          Please wait…
        </>
      ) : (
        children
      )}
    </button>
  );
}
