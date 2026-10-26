import { useEffect } from "react";
import { isKnownActionType, renderable } from "./actionUtils";
import { track } from "../guideAnalytics";

// Unknown action types are never rendered or executed (API §8.3); each distinct one is
// recorded once as telemetry so a server/client vocabulary drift is visible (type only).
const reported = new Set();
function reportUnknown(actions) {
  for (const a of actions || []) {
    if (!a || isKnownActionType(a.type)) continue;
    const type = typeof a.type === "string" ? a.type : "invalid";
    const key = `${a.action_id || ""}:${type}`;
    if (reported.has(key)) continue;
    reported.add(key);
    track("unknown_action", { action_type: type, cta_id: a.action_id });
  }
}

export default function Actions({ actions = [], onAction, max = 2 }) {
  useEffect(() => reportUnknown(actions), [actions]);
  const list = (actions || []).filter(renderable);
  if (!list.length) return null;
  // At most two prominent actions; extras become tertiary links (Prompt §15, Figma §5.1).
  const prominent = list.slice(0, max);
  const extra = list.slice(max);
  return (
    <div>
      <div className="zg-actions">
        {prominent.map((a, i) => (
          <button
            key={`${a.action_id}-${i}`}
            type="button"
            className={`zg-btn ${i === 0 ? "zg-btn--primary" : "zg-btn--secondary"}`}
            onClick={() => onAction(a)}
          >
            {a.label}
          </button>
        ))}
      </div>
      {extra.length > 0 && (
        <div className="zg-links">
          {extra.map((a, i) => (
            <button key={`${a.action_id}-x${i}`} type="button" className="zg-link" onClick={() => onAction(a)}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
