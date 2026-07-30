import { useEffect, useState } from "react";

// Client-side machine + network telemetry for the host header, from REAL browser APIs only.
//
// The honesty boundary matters here, because a broadcast console that invents numbers is
// worse than one that admits a gap:
//
//   memory   — performance.memory (Chromium only). This is the JS heap, NOT system RAM.
//   network  — navigator.connection: effective type, downlink estimate, RTT.
//   load     — measured main-thread frame budget. NOT OS CPU: no browser exposes process
//              or system CPU to a page. Reported as "UI load" and labelled as such.
//   cores    — navigator.hardwareConcurrency (logical cores, static).
//
// Anything unavailable returns null so the UI can render "—" rather than a plausible lie.
// Encoder bitrate/FPS are deliberately absent: they need a live peer connection, which
// only exists once a publisher is wired in.

const SAMPLE_MS = 2000;
// A 60fps frame is 16.7ms. Treat sustained frame times at/above ~50ms (20fps) as fully
// loaded — beyond that the console feels broken anyway.
const FRAME_BUDGET_MS = 16.7;
const FRAME_CEILING_MS = 50;

const pct = (n) => Math.max(0, Math.min(100, Math.round(n)));

function readMemory() {
  const m = performance?.memory;
  if (!m?.jsHeapSizeLimit) return null;
  return {
    usedMb: Math.round(m.usedJSHeapSize / 1048576),
    limitMb: Math.round(m.jsHeapSizeLimit / 1048576),
    percent: pct((m.usedJSHeapSize / m.jsHeapSizeLimit) * 100),
  };
}

function readNetwork() {
  const c = navigator?.connection || navigator?.mozConnection || navigator?.webkitConnection;
  if (!c) return null;
  return {
    effectiveType: c.effectiveType || null,
    downlinkMbps: typeof c.downlink === "number" ? c.downlink : null,
    rttMs: typeof c.rtt === "number" ? c.rtt : null,
    saveData: !!c.saveData,
  };
}

export default function useSystemStats(enabled = true) {
  const [stats, setStats] = useState(() => ({
    memory: null,
    network: readNetwork(),
    load: null,
    cores: navigator?.hardwareConcurrency || null,
  }));

  // Rolling frame-time average. requestAnimationFrame is throttled hard in background
  // tabs, so this would read as "pegged" on a tab nobody is looking at — pause when hidden.
  useEffect(() => {
    if (!enabled) return undefined;
    let raf;
    let last = performance.now();
    let sum = 0;
    let count = 0;

    const tick = (t) => {
      if (!document.hidden) {
        sum += t - last;
        count += 1;
      }
      last = t;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    const timer = setInterval(() => {
      const avg = count ? sum / count : null;
      sum = 0;
      count = 0;
      setStats((prev) => ({
        ...prev,
        memory: readMemory(),
        network: readNetwork(),
        load: avg == null ? null : pct(((avg - FRAME_BUDGET_MS) / (FRAME_CEILING_MS - FRAME_BUDGET_MS)) * 100),
        frameMs: avg == null ? null : Math.round(avg * 10) / 10,
      }));
    }, SAMPLE_MS);

    return () => {
      cancelAnimationFrame(raf);
      clearInterval(timer);
    };
  }, [enabled]);

  // Network changes fire an event rather than needing a poll.
  useEffect(() => {
    const c = navigator?.connection;
    if (!c?.addEventListener) return undefined;
    const onChange = () => setStats((prev) => ({ ...prev, network: readNetwork() }));
    c.addEventListener("change", onChange);
    return () => c.removeEventListener("change", onChange);
  }, []);

  return stats;
}
