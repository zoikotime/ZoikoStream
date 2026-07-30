import { useEffect, useRef, useState } from "react";
import { useInView, prefersReducedMotion } from "./motion";

// Animated count-up when scrolled into view. Tabular figures so width doesn't jitter.
// Tweens from wherever it currently sits to the new value — on mount that's 0 (the
// marketing count-up), and on a later change it's a short nudge from the old number.
// Restarting from 0 on every change would make a live counter unreadable.
export default function Counter({ value, prefix = "", suffix = "", decimals = 0, duration = 1400 }) {
  const reduced = prefersReducedMotion();
  const [ref, inView] = useInView({ threshold: 0.4 });
  const [display, setDisplay] = useState(0);
  // The tween's own record of where it left off. Written only inside the animation frame
  // (never during render) so the next value change knows where to start from without
  // making `display` an effect dependency, which would restart the tween every frame.
  const current = useRef(0);

  useEffect(() => {
    if (!inView || reduced) return undefined;
    const from = current.current;
    if (from === value) return undefined;
    // Short hop for a small live delta, full duration for a first count-up from 0.
    const span = Math.abs(value - from) < Math.abs(value) / 2 ? Math.min(duration, 400) : duration;
    let raf;
    let start;
    const step = (t) => {
      if (start === undefined) start = t;
      const p = Math.min((t - start) / span, 1);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      current.current = from + (value - from) * eased;
      setDisplay(current.current);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [inView, reduced, value, duration]);

  const formatted = (reduced ? value : display).toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (
    <span ref={ref} className="zk-tnum tabular-nums">{prefix}{formatted}{suffix}</span>
  );
}
