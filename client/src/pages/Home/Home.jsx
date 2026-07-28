import { lazy, Suspense } from "react";
import MarketingLayout from "../../layouts/MarketingLayout";
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
    <MarketingLayout>
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
    </MarketingLayout>
  );
}
