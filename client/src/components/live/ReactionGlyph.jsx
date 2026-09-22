// client/src/components/live/ReactionGlyph.jsx
// ONE reaction visual, used by every surface that draws one: the viewer's tap targets
// (components/watch/ReactionBar.jsx) and the floating emoji over both players
// (components/live/ReactionOverlay.jsx). Sharing the component is the point — the picker
// and the thing that floats must be the same artwork, or tapping 🔥 and watching a
// different 🔥 drift up reads as a bug.
//
// ── WHY AN IMAGE AND NOT THE CHARACTER ──────────────────────────────────────────────────
// A system emoji is whatever the viewer's OS ships: Apple's on iPhone, Google's on Android,
// Microsoft's on Windows, and a flat monochrome box on some Linux builds. The same tap
// therefore looked like five different products. These are Microsoft Fluent Emoji 3D
// (MIT licensed, vendored in src/assets/reactions/ — see that folder's note), so every
// viewer on every platform sees the identical mark.
//
// Preloading and the shared failure set live in data/reactionAssets.js — this file stays
// components-only so Fast Refresh keeps working on it.
//
// ── THE CHARACTER IS STILL THE FALLBACK ─────────────────────────────────────────────────
// If an asset 404s, is blocked, or the bundle is served half-cold, the component renders
// the original emoji character instead. A reaction that arrives as a broken-image icon over
// live video would be worse than one that arrives as the OS glyph, and this path is what
// keeps the feature working rather than merely failing tidily.
import { useState } from "react";

import { hasFailed, markFailed } from "../../data/reactionAssets";
import { cx } from "../../ui/tokens";

/**
 * @param {object} props
 * @param {string} props.reactionKey - wire key (`like` | `heart` | `party` | `fire` | `clap`).
 * @param {string} props.emoji - the character to fall back to.
 * @param {string} props.asset - bundled URL of the artwork.
 * @param {number} [props.size] - rendered box in px. Set on BOTH the image and the fallback
 *   so the two are interchangeable and neither can shift the layout around them.
 */
export default function ReactionGlyph({ reactionKey, emoji, asset, size = 28, className = "" }) {
  // Seeded from the shared set, so an instance mounted after a failure never re-requests.
  const [broken, setBroken] = useState(() => hasFailed(reactionKey));

  if (broken || !asset) {
    return (
      <span
        aria-hidden="true"
        data-reaction-glyph="fallback"
        className={cx("inline-flex items-center justify-center leading-none", className)}
        // Matching the image's box exactly is what makes the fallback a swap rather than a
        // reflow — the surrounding button and the floating item keep their geometry.
        style={{ width: size, height: size, fontSize: Math.round(size * 0.86) }}
      >
        {emoji}
      </span>
    );
  }

  return (
    <img
      src={asset}
      alt=""
      aria-hidden="true"
      data-reaction-glyph="asset"
      draggable={false}
      // async so decoding a 256px source never blocks the frame a reaction starts on.
      decoding="async"
      // Explicit width/height, not just CSS: the box is reserved before the bytes arrive,
      // which is what keeps a cold load from nudging the bar as each emoji lands.
      width={size}
      height={size}
      className={cx("select-none object-contain", className)}
      style={{ width: size, height: size }}
      onError={() => {
        markFailed(reactionKey);
        setBroken(true);
      }}
    />
  );
}
