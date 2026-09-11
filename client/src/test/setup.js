
// Vitest setup — dev only, never part of the app bundle.
import "@testing-library/jest-dom/vitest";

// jsdom implements neither of these, and ui/motion.jsx constructs an IntersectionObserver
// in an effect — so ANY test that renders a page using a reveal-on-scroll wrapper throws
// "IntersectionObserver is not defined" from inside a passive effect, which surfaces as an
// opaque React AggregateError rather than as a missing browser API.
//
// Stubbed as inert no-ops rather than simulated: nothing here should assert on scroll
// behaviour, and a fake that "reveals" everything would quietly change what a test sees.
// The components treat a never-firing observer as "not yet revealed", which is their normal
// initial state.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

if (!globalThis.IntersectionObserver) globalThis.IntersectionObserver = NoopObserver;
if (!globalThis.ResizeObserver) globalThis.ResizeObserver = NoopObserver;
