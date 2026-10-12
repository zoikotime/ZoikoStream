import { renderable } from "./actionUtils";

export default function Actions({ actions = [], onAction, max = 2 }) {
  const list = actions.filter(renderable);
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
