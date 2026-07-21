import { FiArrowRight, FiRadio } from "react-icons/fi";
import { Button, Counter, GradientBackground, GradientText } from "../../../ui";
import { HERO_STATS } from "../../../data/home";
import MediaPipeline from "./MediaPipeline";

export default function Hero() {
  return (
    <section className="relative overflow-hidden bg-slate-950 pt-28 pb-20 sm:pt-36 sm:pb-28">
      <GradientBackground />

      {/* Brand watermark — screen blend drops the navy tile, leaving the swirl glowing. */}
      <img
        src="/zoiko-icon.png"
        alt=""
        aria-hidden
        className="zk-float pointer-events-none absolute -top-20 right-[-8rem] w-[34rem] max-w-none select-none opacity-20 blur-[1px] mix-blend-screen"
      />

      <div className="relative mx-auto grid max-w-7xl items-center gap-12 px-5 sm:px-8 lg:grid-cols-2 lg:gap-8">
        {/* Copy */}
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs font-semibold text-emerald-300 backdrop-blur">
            <span className="relative flex h-2 w-2">
              <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-emerald-400" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            Secure video infrastructure
          </span>
          <h1 className="mt-6 text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl">
            Secure video infrastructure for products, broadcasts, and{" "}
            <GradientText>live events</GradientText>.
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-white/70">
            Ingest, transcode, protect, and deliver video from one API — with the analytics
            and live-event tooling to run it all at scale. You build the product; we run the pipeline.
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button href="/signup" variant="primary" size="lg">
              Start Building <FiArrowRight />
            </Button>
            <Button href="/contact" variant="outlineLight" size="lg">Talk to an Expert</Button>
            <Button href="#live-events" variant="ghost" size="lg" className="text-white/80 hover:bg-white/10">
              <FiRadio /> Live Events
            </Button>
          </div>

          {/* Stats */}
          <dl className="mt-12 grid max-w-lg grid-cols-3 gap-4 border-t border-white/10 pt-8 sm:gap-6">
            {HERO_STATS.map((s) => (
              <div key={s.label}>
                <dt className="text-2xl font-bold text-white sm:text-3xl">
                  <Counter value={s.value} prefix={s.prefix} suffix={s.suffix} decimals={s.decimals} />
                </dt>
                <dd className="mt-1 text-sm text-white/60">{s.label}</dd>
              </div>
            ))}
          </dl>
        </div>

        {/* Interactive pipeline visualization */}
        <div className="zk-float">
          <MediaPipeline />
        </div>
      </div>
    </section>
  );
}
