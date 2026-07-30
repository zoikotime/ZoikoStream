import { cx } from "./tokens";

// Shimmering placeholder (uses .zk-skeleton in index.css). `variant` picks the shape.
const SHAPES = { line: "h-4 rounded-lg", title: "h-8 rounded-xl", block: "h-40 rounded-2xl", circle: "rounded-full", pill: "h-6 rounded-full" };

export default function Skeleton({ variant = "line", className = "", style }) {
  // Translucent dark fill (not a fixed grey) so the placeholder sits correctly on the org
  // area's slate page AND on the admin console's true-black one.
  return <div className={cx("zk-skeleton bg-slate-200 dark:bg-white/[0.07]", SHAPES[variant], className)} style={style} aria-hidden="true" />;
}

// Section-sized loading state — used as the homepage Suspense fallback.
export function SectionFallback() {
  return (
    <div className="mx-auto w-full max-w-7xl px-5 py-24 sm:px-8" aria-hidden="true">
      <div className="mx-auto max-w-2xl space-y-4 text-center">
        <Skeleton variant="pill" className="mx-auto w-32" />
        <Skeleton variant="title" className="mx-auto w-3/4" />
        <Skeleton variant="line" className="mx-auto w-2/3" />
      </div>
      <div className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => <Skeleton key={i} variant="block" />)}
      </div>
    </div>
  );
}
