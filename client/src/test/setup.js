
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

// jsdom implements no media playback at all: HTMLMediaElement.play() returns undefined
// rather than a Promise, so the usual `el.play().catch(...)` — the standard way to handle a
// browser's autoplay refusal — throws a TypeError instead. Any test that renders the video
// player hits it. A resolved promise is the honest stand-in: nothing plays in jsdom, and the
// caller's rejection path is for autoplay policy, which does not exist here either.
if (typeof globalThis.HTMLMediaElement !== "undefined") {
  // Assigned unconditionally: jsdom DOES define play(), it just throws "Not implemented"
  // and returns undefined, and that is not detectable from the function's source text.
  const proto = globalThis.HTMLMediaElement.prototype;
  proto.play = () => Promise.resolve();
  proto.pause = () => {};
}
