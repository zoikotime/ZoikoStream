// Lead-flow helpers shared by the form and the Guide header (Figma §8 "Lead flow" title).
const ACTIVE_PHASES = new Set(["collecting", "checking", "reviewing", "submitting", "failed"]);

/** A lead card the visitor is still working on (not confirmed, unknown or cancelled). */
export const isActiveLead = (item) => item?.kind === "lead" && ACTIVE_PHASES.has(item.phase || "collecting");

export const leadTitle = (flow) => (flow?.flow_kind === "sales" ? "Talk to sales" : "Request a demo");
