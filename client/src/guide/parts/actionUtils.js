// Action validation shared by Guide components (kept out of component files for fast refresh).
// Approved actions only (API §8.3): a known `type`, and for navigation a server-resolved
// RELATIVE href from the destination registry. Anything else is not rendered — never
// executed — so a malformed or unexpected action cannot become a link.
const KNOWN = new Set([
  "navigate", "start_support_flow", "start_lead_flow", "handoff", "sign_in", "open_help",
  "open_status", "__local",
]);

export const isSafeHref = (href) =>
  typeof href === "string" && href.startsWith("/") && !href.startsWith("//") && !href.includes("\\");

export function renderable(action) {
  if (!action || !KNOWN.has(action.type) || typeof action.label !== "string") return false;
  if (action.href !== undefined && !isSafeHref(action.href)) return false;
  const inGuide = ["start_support_flow", "start_lead_flow", "__local"].includes(action.type)
    || (action.type === "handoff" && action.target === "organizer");
  return inGuide || isSafeHref(action.href);
}

