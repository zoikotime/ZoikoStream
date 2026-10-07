import { useState } from "react";
import { useTheme } from "../theme/ThemeContext";
import { logoCandidates } from "../utils/orgLogo";
import Logo from "./Logo";

// An organization's logo for the viewer's current theme, walking the chain in utils/orgLogo
// (dark -> light -> ZoikoStream). A logo that fails to load drops to the next candidate rather
// than leaving a broken-image icon, and the image keeps the caller's height, so a bad URL never
// reflows the header around it.
//
// No CSS filter or invert: a brand's colours are the brand. The dark-theme asset is the fix.
//
// `theme` pins a theme (a preview of the other one); otherwise the app's current theme is used,
// so toggling it swaps the logo. `fallback` replaces the ZoikoStream mark when a surface needs
// a different default shape.
export default function OrgLogo({ light, dark, name, height = "h-8", className = "", theme, fallback }) {
  const app = useTheme();
  const mode = theme || app?.theme || "light";
  const [failed, setFailed] = useState([]);
  const src = logoCandidates({ light, dark }, mode).find((url) => !failed.includes(url));

  if (!src) return fallback ?? <Logo height={height} />;
  return (
    <img
      key={src}
      src={src}
      alt={`${(name || "").trim() || "Organization"} logo`}
      className={`${height} block w-auto max-w-[200px] object-contain ${className}`}
      onError={() => setFailed((f) => [...f, src])}
    />
  );
}
