import { lazy, Suspense } from "react";
import Header from "../../components/home/Header/Header";
import Hero from "../../components/home/Hero/Hero";
import { SectionFallback } from "../../ui";

// Above the fold loads eagerly; everything else is code-split + lazy-loaded.
const PlatformDefinition = lazy(() => import("../../components/home/PlatformDefinition/PlatformDefinition"));
const AudiencePathways = lazy(() => import("../../components/home/AudiencePathways/AudiencePathways"));
const MediaLifecycle = lazy(() => import("../../components/home/MediaLifecycle/MediaLifecycle"));
const DeveloperPlatform = lazy(() => import("../../components/home/DeveloperPlatform/DeveloperPlatform"));
const EnterpriseOperations = lazy(() => import("../../components/home/EnterpriseOperations/EnterpriseOperations"));
const LiveEvents = lazy(() => import("../../components/home/LiveEvents/LiveEvents"));
const Trust = lazy(() => import("../../components/home/Trust/Trust"));
const Proof = lazy(() => import("../../components/home/Proof/Proof"));
const CTA = lazy(() => import("../../components/home/CTA/CTA"));
const Resources = lazy(() => import("../../components/home/Resources/Resources"));
const FAQ = lazy(() => import("../../components/home/FAQ/FAQ"));
const Footer = lazy(() => import("../../components/home/Footer/Footer"));

export default function Home() {
  return (
    <div className="min-h-screen overflow-x-clip bg-white dark:bg-slate-950">
      {/* Keyboard/screen-reader skip link */}
      <a href="#main" className="zk-skip rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-lg">
        Skip to content
      </a>
      <Header />
      <main id="main">
        <Hero />
        <Suspense fallback={<SectionFallback />}>
          <PlatformDefinition />
          <AudiencePathways />
          <MediaLifecycle />
          <DeveloperPlatform />
          <EnterpriseOperations />
          <LiveEvents />
          <Trust />
          <Proof />
          <CTA />
          <Resources />
          <FAQ />
          <Footer />
        </Suspense>
      </main>
    </div>
  );
}
