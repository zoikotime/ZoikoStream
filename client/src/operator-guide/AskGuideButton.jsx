// "Ask Guide" button for console pages (§5 inline entry point, §23 guide-inline-entry). Opens
// Guide with a question and, where there is one, the page's event attached. Renders nothing
// unless Guide can open here (role, console route, web build — published by the mount).
// Lightweight by design: no Guide bundle, no auth module, no Guide styles — console Tailwind
// classes only.
import { useSyncExternalStore } from "react";
import { useLocation } from "react-router-dom";
import { askGuide, isGuideAvailable, subscribeGuideAvailable } from "./askGuide";

export default function AskGuideButton({ question, eventId, label = "Ask Guide", className = "" }) {
  const available = useSyncExternalStore(subscribeGuideAvailable, isGuideAvailable, () => false);
  const { pathname } = useLocation();
  if (!available) return null;
  return (
    <button
      type="button"
      onClick={() => askGuide({ question, eventId, route: pathname })}
      title={question}
      className={`inline-flex min-h-[44px] items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-violet-700 hover:border-violet-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 sm:min-h-[36px] dark:border-slate-700 dark:bg-slate-900 dark:text-violet-300 ${className}`}
    >
      <img src="/zoiko-icon.png" alt="" width="16" height="16" className="rounded" />
      {label}
    </button>
  );
}
