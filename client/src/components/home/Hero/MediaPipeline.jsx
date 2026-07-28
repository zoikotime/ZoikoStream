import { useRef, useState } from "react";
import useInterval from "../../../hooks/useInterval";
import { FiUploadCloud, FiCpu, FiLock, FiGlobe, FiPlayCircle } from "react-icons/fi";

// Heavyweight interactive #1: the media pipeline. Auto-advances through stages,
// pauses on hover/focus, and supports full keyboard (arrow) navigation.
// Animated packets flow edge-to-edge between stages.
const STAGES = [
  { key: "ingest", icon: FiUploadCloud, label: "Ingest", detail: "RTMP · SRT · WebRTC accepted at the nearest edge." },
  { key: "produce", icon: FiCpu, label: "Transcode", detail: "Real-time adaptive renditions + live captions." },
  { key: "secure", icon: FiLock, label: "Secure", detail: "Encrypt, sign, and apply DRM before delivery." },
  { key: "deliver", icon: FiGlobe, label: "Deliver", detail: "Multi-CDN edge across 60+ regions." },
  { key: "play", icon: FiPlayCircle, label: "Play", detail: "Sub-second playback on any device." },
];

const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export default function MediaPipeline() {
  const [index, setIndex] = useState(2);
  const [paused, setPaused] = useState(false);
  const [engaged, setEngaged] = useState(false); // any tap/click/key hands control to the user
  const btnRefs = useRef([]);
  const activeStage = STAGES[index];

  // Auto-advance until the user engages (works on touch, where hover never fires),
  // paused on hover, and off under reduced-motion.
  useInterval(() => setIndex((i) => (i + 1) % STAGES.length), 3200, !paused && !engaged && !prefersReducedMotion());

  const select = (i) => { setIndex(i); setEngaged(true); };

  // Arrow-key navigation across the tablist (roving focus).
  const onKeyDown = (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    setEngaged(true);
    setIndex((i) => {
      const next = e.key === "ArrowRight" ? (i + 1) % STAGES.length : (i - 1 + STAGES.length) % STAGES.length;
      btnRefs.current[next]?.focus();
      return next;
    });
  };

  return (
    <div
      className="relative rounded-3xl border border-white/10 bg-white/5 p-5 backdrop-blur-md sm:p-8"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setEngaged(true)}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-widest text-emerald-300/80">Live media pipeline</span>
        <span className="flex items-center gap-1.5 text-xs font-medium text-white/60">
          <span className="relative flex h-2 w-2">
            <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-emerald-400" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
          </span>
          Streaming
        </span>
      </div>

      {/* Stage rail */}
      <div
        role="tablist"
        aria-label="Media pipeline stages"
        onKeyDown={onKeyDown}
        className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-stretch sm:gap-0"
      >
        {STAGES.map((s, i) => {
          const isActive = i === index;
          return (
            <div key={s.key} className="flex flex-1 items-center sm:flex-col">
              <button
                ref={(el) => (btnRefs.current[i] = el)}
                role="tab"
                aria-selected={isActive}
                tabIndex={isActive ? 0 : -1}
                onClick={() => select(i)}
                className={`group relative z-10 flex w-full items-center gap-3 rounded-2xl border px-3 py-3 text-left transition-all duration-300 sm:w-auto sm:flex-col sm:gap-2 sm:px-4 ${
                  isActive
                    ? "border-emerald-400/50 bg-emerald-400/10 shadow-lg shadow-emerald-500/10"
                    : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.06]"
                }`}
              >
                <span
                  className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition-all duration-300 ${
                    isActive ? "scale-110 bg-emerald-400 text-slate-900" : "bg-white/10 text-emerald-200 group-hover:scale-105"
                  }`}
                >
                  <s.icon className="text-lg" />
                </span>
                <span className={`text-sm font-semibold transition-colors ${isActive ? "text-white" : "text-white/70"}`}>
                  {s.label}
                </span>
              </button>

              {/* Connector with flowing packets (between stages only) */}
              {i < STAGES.length - 1 && (
                <div className="relative mx-1 hidden h-0.5 flex-1 self-center overflow-hidden rounded-full bg-white/10 sm:block">
                  <span
                    className="zk-flow-dot absolute top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-emerald-300 shadow-[0_0_8px_2px_rgba(110,231,183,0.6)]"
                    style={{ "--zk-flow-distance": "3000%", animationDelay: `${i * 0.4}s` }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Active stage detail (crossfades on change via keyed remount) */}
      <div
        role="tabpanel"
        className="mt-6 rounded-2xl border border-white/10 bg-slate-900/40 p-4"
      >
        <div key={activeStage.key} className="zk-fade-in">
          <p className="text-sm font-semibold text-emerald-300">{activeStage.label}</p>
          <p className="mt-1 text-sm text-white/70">{activeStage.detail}</p>
        </div>

        {/* Progress ticks */}
        <div className="mt-4 flex gap-1.5" aria-hidden="true">
          {STAGES.map((s, i) => (
            <span
              key={s.key}
              className={`h-1 flex-1 rounded-full transition-colors duration-300 ${
                i === index ? "bg-emerald-400" : "bg-white/15"
              }`}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
