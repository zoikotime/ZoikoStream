import { cx } from "./tokens";

// Ring spinner (CSS border + .zk-spin). Inherits color via currentColor.
const SIZES = { sm: "h-4 w-4 border-2", md: "h-6 w-6 border-2", lg: "h-9 w-9 border-[3px]" };

export default function Spinner({ size = "md", className = "", label = "Loading" }) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cx("zk-spin inline-block rounded-full border-current border-t-transparent text-emerald-600 dark:text-emerald-400", SIZES[size], className)}
    />
  );
}

// Centered full-area loading state (e.g. route/page fallback).
export function PageSpinner({ label = "Loading" }) {
  return (
    <div className="grid min-h-[40vh] place-items-center">
      <Spinner size="lg" label={label} />
    </div>
  );
}
