import { useState } from "react";
import { useTheme } from "../theme/ThemeContext";
import { logoCandidates } from "../utils/orgLogo";
import Logo from "./Logo";

// An organization's logo for the viewer's current theme, walking the chain in utils/orgLogo
// (dark -> light -> ZoikoStream). A logo that fails to load drops to the next candidate rather
// than leaving a broken-image icon, and a URL that failed once is never retried. The ZoikoStream
// fallback is ui/Logo itself — the full wordmark on its light chip — so its navy lettering stays
// legible on a dark surface. Every surface that shows an organization's logo (the viewer page's
// brand bar, the Settings branding preview) renders through this one component, so the two can
// never pick, size or fall back differently.
//
// Sizing: the caller's `height` is fixed and the width follows the image (w-auto), bounded by
// `className` (a max-width; 200px by default). object-contain only takes effect when that bound
// is reached, and then letterboxes instead of squashing. Nothing is stretched or cropped.
//
// No CSS filter or invert: a brand's colours are the brand. The dark-theme asset is the fix.
//
// `theme` pins a theme (a preview of the other one); otherwise the app's current theme is used,
// so toggling it swaps the logo.
export default function OrgLogo({ light, dark, name, height = "h-8", className = "max-w-[200px]", theme }) {
  const app = useTheme();
  const mode = theme || app?.theme || "light";
  const [failed, setFailed] = useState([]);
  const src = logoCandidates({ light, dark }, mode).find((url) => !failed.includes(url));

  if (!src) return <Logo height={height} />;
  return (
    <img
      key={src}
      src={src}
      alt={`${(name || "").trim() || "Organization"} logo`}
      className={`${height} block w-auto object-contain object-center ${className}`}
      onError={() => setFailed((f) => [...f, src])}
    />
  );
}
