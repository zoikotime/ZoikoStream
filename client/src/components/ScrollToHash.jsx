import { useEffect } from "react";
import { useLocation } from "react-router-dom";

// Scroll to #anchor after a client-side navigation.
//
// React Router does not do this: <Link to="/solutions#worship"> changes the URL and renders the
// page at the top, so the ten footer links and five solution cards that point at an anchored
// section would all land in the wrong place. The browser only handles a hash natively for a
// same-document jump or a full page load.
//
// The retry matters as much as the scroll: the target page is lazily loaded, so on the first
// attempt the element usually does not exist yet. Polling a few animation frames costs nothing
// and covers the Suspense gap without guessing a timeout.
const MAX_FRAMES = 30; // ~0.5s at 60fps — long enough for a lazy chunk, short enough to give up

export default function ScrollToHash() {
  const { pathname, hash } = useLocation();

  useEffect(() => {
    // No hash: land at the top, which is what a fresh page should do. (Not scroll-restoring
    // here on purpose — none of these pages is long enough for that to be missed, and guessing
    // wrong is more jarring than starting at the top.)
    if (!hash) {
      window.scrollTo({ top: 0, behavior: "auto" });
      return;
    }

    let frame = 0;
    let raf;
    const tryScroll = () => {
      const el = document.getElementById(decodeURIComponent(hash.slice(1)));
      if (el) {
        // scroll-mt-* on the targets clears the fixed header; "smooth" is respected by
        // index.css, which already disables it under prefers-reduced-motion.
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if (++frame < MAX_FRAMES) raf = requestAnimationFrame(tryScroll);
    };
    raf = requestAnimationFrame(tryScroll);
    return () => cancelAnimationFrame(raf);
  }, [pathname, hash]);

  return null;
}
