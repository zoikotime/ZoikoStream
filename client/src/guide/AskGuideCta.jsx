// Inline "Ask Guide" CTA for public marketing pages (Engineering Wireframe §2.1: "May appear in
// Pricing, Solutions, Viewer Help, and Contact sections; opens Guide with a preselected
// intent"). Renders nothing until Guide is available on the page.
import { useSyncExternalStore } from "react";
import { askGuide, isGuideAvailable, subscribeGuideAvailable } from "./askGuide";

export default function AskGuideCta({ quickAction = null, label = "Ask Guide", className = "" }) {
  const available = useSyncExternalStore(subscribeGuideAvailable, isGuideAvailable, () => false);
  if (!available) return null;
  return (
    <button
      type="button"
      className={className}
      aria-haspopup="dialog"
      onClick={() => askGuide({ quickAction, entryPoint: "inline_cta" })}
    >
      {label}
    </button>
  );
}
