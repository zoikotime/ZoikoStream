import { FiArrowRight } from "react-icons/fi";
import { Section, Eyebrow, Reveal, Button } from "../../../ui";
import { DEV_FEATURES } from "../../../data/home";
import CodeEditor from "./CodeEditor";

export default function DeveloperPlatform() {
  return (
    <Section id="developers" tone="dark">
      <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
        {/* Left: copy */}
        <div>
          <Reveal><Eyebrow className="border-emerald-400/30 bg-emerald-400/10 text-emerald-300">Developer platform</Eyebrow></Reveal>
          <Reveal delay={60}>
            <h2 className="mt-4 text-3xl font-bold tracking-tight text-white sm:text-4xl">
              A video API you'll actually enjoy building on
            </h2>
          </Reveal>
          <Reveal delay={120}>
            <p className="mt-4 text-lg leading-relaxed text-white/70">
              Create a secure, recorded, DRM-protected live stream in a single call. Typed SDKs,
              predictable REST + realtime APIs, and a sandbox that behaves exactly like production.
            </p>
          </Reveal>

          <div className="mt-8 space-y-4">
            {DEV_FEATURES.map((f, i) => (
              <Reveal key={f.title} delay={160 + i * 80}>
                <div className="flex gap-4">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/10 text-emerald-300">
                    <f.icon className="text-lg" />
                  </span>
                  <div>
                    <h3 className="font-semibold text-white">{f.title}</h3>
                    <p className="mt-0.5 text-sm text-white/60">{f.body}</p>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>

          <Reveal delay={420}>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button href="/docs" variant="primary" size="lg">
                Read the docs <FiArrowRight />
              </Button>
              <Button href="/signup" variant="outlineLight" size="lg">Get sandbox keys</Button>
            </div>
          </Reveal>
        </div>

        {/* Right: interactive editor */}
        <Reveal delay={120}>
          <CodeEditor />
        </Reveal>
      </div>
    </Section>
  );
}
