import { cx } from "./tokens";
import { useParallax } from "./motion";

// Ambient gradient blobs + optional grid overlay. Sits absolutely inside a
// `relative` dark container. `parallax` drifts the layer on scroll.
export default function GradientBackground({ grid = true, parallax = true, className = "" }) {
  // Hook order must be stable, so always call it; only attach the ref when enabled.
  const ref = useParallax(-80);
  return (
    <div
      ref={parallax ? ref : undefined}
      className={cx("pointer-events-none absolute inset-0 overflow-hidden", parallax && "will-change-transform", className)}
      aria-hidden="true"
    >
      <div className="zk-drift absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-emerald-500/25 blur-3xl" />
      <div className="zk-drift absolute top-20 right-1/4 h-96 w-96 rounded-full bg-indigo-500/25 blur-3xl" style={{ animationDelay: "-7s" }} />
      {grid && (
        <div
          className="absolute inset-0 opacity-[0.15]"
          style={{
            backgroundImage:
              "linear-gradient(to right, rgba(255,255,255,.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,.08) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
            maskImage: "radial-gradient(ellipse at 50% 0%, black 40%, transparent 75%)",
            WebkitMaskImage: "radial-gradient(ellipse at 50% 0%, black 40%, transparent 75%)",
          }}
        />
      )}
    </div>
  );
}
