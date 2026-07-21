import { useEffect, useState } from "react";
import { useInView, prefersReducedMotion } from "./motion";

// Animated count-up when scrolled into view. Tabular figures so width doesn't jitter.
export default function Counter({ value, prefix = "", suffix = "", decimals = 0, duration = 1400 }) {
  const reduced = prefersReducedMotion();
  const [ref, inView] = useInView({ threshold: 0.4 });
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView || reduced) return;
    let raf;
    let start;
    const step = (t) => {
      if (start === undefined) start = t;
      const p = Math.min((t - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      setDisplay(value * eased);
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
