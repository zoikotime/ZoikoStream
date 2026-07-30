import { useEffect, useRef } from "react";

// Declarative setInterval (the standard ref pattern): the callback can close over fresh
// state without restarting the timer. Pass enabled=false (or delay=null) to pause;
// the timer restarts when `delay`/`enabled` change and is always cleared on unmount.
// Replaces the copy-pasted useEffect(setInterval)/clearInterval blocks (clocks, elapsed
// counters, tagline rotators, live viewer-count drift).
export default function useInterval(callback, delay, enabled = true) {
  const cb = useRef(callback);
  useEffect(() => {
    cb.current = callback;
  });
  useEffect(() => {
    if (!enabled || delay == null) return undefined;
    const id = setInterval(() => cb.current(), delay);
    return () => clearInterval(id);
  }, [delay, enabled]);
}
