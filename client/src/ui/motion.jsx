/* eslint-disable react-refresh/only-export-components -- motion module: hooks + components together by design */
// Motion utilities — no animation library. IntersectionObserver + CSS transitions,
// all reduced-motion aware. Shared by the homepage and dashboards.
import { useEffect, useRef, useState } from "react";
import { cx } from "./tokens";

export const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Fires once when the returned ref scrolls into view. True immediately if reduced-motion.
export function useInView(options = { threshold: 0.15, rootMargin: "0px 0px -10% 0px" }) {
  const ref = useRef(null);
  const reduced = prefersReducedMotion();
  const [inView, setInView] = useState(reduced);

  useEffect(() => {
    if (reduced) return;
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) {
        setInView(true);
        io.disconnect();
      }
    }, options);
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return [ref, inView];
}

// True once the page is scrolled past `threshold`px. Drives sticky headers.
export function useScrolled(threshold = 24) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

// Scroll parallax written straight to the DOM (no re-render). rAF-throttled, reduced-motion off.
export function useParallax(speed = -40) {
  const ref = useRef(null);
  useEffect(() => {
    if (prefersReducedMotion()) return;
    let raf = 0;
    const update = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const vh = window.innerHeight || 1;
      const progress = (rect.top + rect.height / 2) / vh - 0.5;
      el.style.transform = `translate3d(0, ${(progress * speed).toFixed(1)}px, 0)`;
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [speed]);
  return ref;
}

const REVEAL_EASE = "cubic-bezier(0.22, 1, 0.36, 1)";

// Fade + rise when scrolled into view. `delay` (ms) staggers grids.
export function Reveal({ children, delay = 0, className = "", as: Tag = "div" }) {
  const [ref, inView] = useInView();
  return (
    <Tag
      ref={ref}
      style={{ transitionDelay: `${delay}ms`, transitionTimingFunction: REVEAL_EASE }}
      className={cx(
        "transition-all duration-[900ms] will-change-[transform,opacity] motion-reduce:transition-none",
        inView ? "opacity-100 translate-y-0 blur-0" : "opacity-0 translate-y-8 blur-[2px]",
        className
      )}
    >
      {children}
    </Tag>
  );
}
