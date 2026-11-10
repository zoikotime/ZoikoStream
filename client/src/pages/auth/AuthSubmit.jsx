import { cx } from "../../ui/tokens";

// The auth pages' primary button: full width, indigo-violet → magenta as in the approved
// design, with a built-in loading state. The gradient is painted at 2× width and slides on
// hover toward magenta → violet. Every stop keeps white text at ≥ 4.5:1 (#433ed6 7.2:1,
// #d22aa6 4.5:1 — the design's #eb28b5 end is 3.8:1, so the end stop is a shade deeper).
export default function AuthSubmit({ loading = false, disabled = false, className = "", children, ...props }) {
  return (
    <button
      type="submit"
      disabled={loading || disabled}
      aria-busy={loading || undefined}
      className={cx(
        "group inline-flex h-[var(--auth-btn,52px)] w-full items-center justify-center gap-2 rounded-[max(8px,calc(9*var(--u,1px)))] px-6 text-[length:max(16px,calc(20*var(--u,1px)))] font-medium text-white",
        "bg-gradient-to-r from-[#433ed6] via-[#d22aa6] to-[#433ed6] bg-[length:200%_auto] bg-[position:0%_50%]",
        "shadow-[0_12px_26px_-14px_rgba(124,58,237,0.6)] transition-all duration-300",
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
