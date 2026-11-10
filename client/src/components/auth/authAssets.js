// Images used by the auth hero (client/public/auth/). All served as static files.
//
// hero-art.webp — the backdrop of the approved ZoikoStream login design: light arcs, tilted
//   concert screens, world-map screen, stage and crowd. Made from the approved reference image
//   (its 880 × 1024 hero) with its lettering and mock devices painted out — the page draws the
//   real text, and devices showing real screenshots, on top. 1280 wide: the left 400 px are a
//   soft continuation of the artwork's own left edge, so on screens wider than the design's
//   3:2 it fills the hero at the SAME scale as the copy and devices instead of zooming in.
//
// Screenshots — the real ZoikoStream viewer page (/events/:id/watch) in its live state,
// rendered with invented demo data (no real customer data): desktop dark theme for the laptop,
// mobile light theme for the phone. A live broadcast needs the LiveKit + backend stack, so the
// player's broadcast frame in these captures is the project's concert photograph
// (live-stage.webp); everything around it is the page as a viewer sees it. Re-capture them
// when the viewer page changes materially.
export const AUTH_ASSETS = {
  heroArt: "/auth/hero-art.webp",
  watchShot: "/auth/showcase-watch.webp",
  watchMobileShot: "/auth/showcase-watch-mobile.webp",
};
