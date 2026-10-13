import { AlertTriangle, CheckCircle2, ChevronRight, LifeBuoy } from "lucide-react";
import Actions from "./Actions";

// One deterministic step at a time (WF-08/09). Every input is a server-listed option; there
// is no free-text field, so a PIN, password or link can never be typed here (UX-AT-09).
export default function SupportStep({ item, onStep, onAction }) {
  const { flow, busy } = item;
  const titleId = `zg-flow-${flow.flow_id}`;
  return (
    <section className="zg-card" aria-labelledby={titleId} data-flow-id={flow.flow_id}>
      <p className="zg-eyebrow"><LifeBuoy aria-hidden="true" /> Viewer help · {flow.title}</p>
      {flow.notice && (
        <div className="zg-notice zg-notice--warning" style={{ marginBottom: 12 }}>
          <AlertTriangle aria-hidden="true" />
          <span>{flow.notice}</span>
        </div>
      )}

      {flow.status === "in_progress" && flow.step && (
        <>
          <h3 id={titleId} tabIndex={-1}>{flow.step.prompt}</h3>
          <div className="zg-options" role="group" aria-labelledby={titleId}>
            {flow.step.options.map((o) => (
              <button
                key={o.option_id}
                type="button"
                className="zg-option"
                disabled={busy}
                onClick={() => onStep(item.id, flow, { optionId: o.option_id })}
              >
                <span>{o.label}</span>
                <ChevronRight aria-hidden="true" />
              </button>
            ))}
          </div>
          <Actions actions={flow.actions} onAction={onAction} max={1} />
          <div className="zg-step-nav">
            {flow.step.allow_back && (
              <button type="button" className="zg-link" disabled={busy} onClick={() => onStep(item.id, flow, { command: "back" })}>
                Back
              </button>
            )}
            {flow.step.allow_back && (
              <button type="button" className="zg-link" disabled={busy} onClick={() => onStep(item.id, flow, { command: "restart" })}>
                Start over
              </button>
            )}
            <button type="button" className="zg-link" disabled={busy} onClick={() => onStep(item.id, flow, { command: "cancel" })}>
              Stop viewer help
            </button>
          </div>
        </>
      )}

      {flow.status === "resolved" && (
        <div className="zg-notice zg-notice--success" role="status">
          <CheckCircle2 aria-hidden="true" />
          <span id={titleId}>{flow.message}</span>
        </div>
      )}
      {flow.status === "escalated" && (
        <>
          <div className="zg-notice" role="status">
            <LifeBuoy aria-hidden="true" />
            <span id={titleId}>{flow.message}</span>
          </div>
          <Actions actions={flow.actions} onAction={onAction} />
        </>
      )}
      {flow.status === "cancelled" && (
        <p id={titleId} className="zg-meta" style={{ margin: 0 }}>
          {flow.resolution_code?.startsWith("switched_to_") || flow.resolution_code?.startsWith("category_")
            ? "Continuing below."
            : "Viewer help stopped."}
        </p>
      )}
    </section>
  );
}
