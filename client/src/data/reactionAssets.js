// Loading state for the reaction artwork — the non-component half of
// components/live/ReactionGlyph.jsx.
//
// Its own module (not the component file) so ReactionGlyph keeps Fast Refresh: a file that
// exports anything other than components loses it. Same reason data/recordingState.js and
// data/timezoneSearch.js exist.
import { REACTIONS } from "./reactions";

// Assets known to have failed, shared across every instance for the life of the page.
// Once `fire.webp` has 404'd there is nothing to gain from the next forty floating fires
// each re-requesting it: a glyph reads this on its FIRST render and goes straight to the
// character. Deliberately module-level rather than React state — it is a fact about the
// network, not about any one component, and a context would re-render every overlay item
// to deliver news none of them can act on differently.
const failed = new Set();

export const hasFailed = (key) => failed.has(key);
export const markFailed = (key) => failed.add(key);

/** Test-only: the set outlives a render tree, so a suite that simulates a broken asset has
 *  to be able to put it back. Never called by application code. */
export const resetFailedAssets = () => failed.clear();

let done = false;

/**
 * Warm the browser cache so the FIRST reaction of a session animates with artwork rather
 * than popping in a frame or two late. Idempotent and safe to call from several surfaces —
 * the browser coalesces identical requests, and `done` stops us building Image objects on
 * every mount.
 *
 * Not called at module scope on purpose: an import must not fire five network requests in
 * a test runner or on a page that never shows reactions.
 */
export function preloadReactionAssets() {
  if (done || typeof Image === "undefined") return;
  done = true;
  for (const r of REACTIONS) {
    const img = new Image();
    // A preload that fails is recorded too, so the very first render of that key already
    // knows to use the character instead of waiting for its own error event.
    img.onerror = () => markFailed(r.key);
    img.src = r.asset;
  }
}
