import { useEffect, useRef } from "react";
import { Activity, AlertTriangle, CheckCircle2, ChevronRight, HelpCircle, LifeBuoy, XCircle } from "lucide-react";
import Actions from "./Actions";
import { isSafeHref } from "./actionUtils";

// Playback status card (C5, WF-09 A4 "If a verified status integration exists, show
// service-status result as a separate authoritative card"). The server's D1 lookup supplies
// state, summary and check time; nothing here is inferred. Meaning is carried by text and an
// icon, never by color alone (WF §08 "Color").
const STATUS_STATE = {
  operational: { label: "Operational", icon: CheckCircle2, tone: "success" },
  degraded: { label: "Degraded performance", icon: AlertTriangle, tone: "warning" },
  outage: { label: "Service outage", icon: XCircle, tone: "error" },
  unknown: { label: "Status unavailable", icon: HelpCircle, tone: "info" },
};

function checkedAt(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(d);
}

export function StatusCard({ card, onAction }) {
  const meta = STATUS_STATE[card?.state] || STATUS_STATE.unknown;
  const Icon = meta.icon;
  const time = checkedAt(card.checked_at);
  return (
    <section className={`zg-status zg-status--${meta.tone}`} aria-label="ZoikoStream service status">
      <p className="zg-eyebrow"><Activity aria-hidden="true" /> Service status</p>
      <p className="zg-status-state">
        <Icon aria-hidden="true" />
        <strong>{meta.label}</strong>
      </p>
      {card.summary && <p className="zg-status-summary">{card.summary}</p>}
      {time && <p className="zg-meta zg-caption">Checked {time}</p>}
      {isSafeHref(card.status_url) && (
        <button
          type="button"
          className="zg-link"
          onClick={() => onAction({ action_id: "status-page", type: "open_status", label: "Service status", href: card.status_url })}
        >
          Open status page
        </button>
      )}
    </section>
  );
}

// One deterministic step at a time (WF-08/09). Every input is a server-listed option; there
// is no free-text field, so a PIN, password or link can never be typed here (UX-AT-09).
// When a flow starts or a step advances, focus moves to the step heading (Figma §13.2
// "Viewer support: Step heading / first option"); the widget announces the prompt.
export default function SupportStep({ item, onStep, onAction, onPatch = () => {} }) {
  const { flow, busy } = item;
  const titleId = `zg-flow-${flow.flow_id}`;
  const headingRef = useRef(null);
  const statusCard = flow.status_card || flow.step?.status_card;

  useEffect(() => {
    if (!item.focusPending) return;
    const el = headingRef.current;
    if (el) {
      el.focus();
      el.scrollIntoView?.({ block: "nearest" });
    }
    onPatch(item.id, { focusPending: false });
  }, [item.focusPending, item.id, flow.step?.step_id, flow.status, onPatch]);

  return (
    <section className="zg-card" aria-labelledby={titleId} data-flow-id={flow.flow_id}>
      <p className="zg-eyebrow"><LifeBuoy aria-hidden="true" /> Viewer help · {flow.title}</p>
      {flow.notice && (
        <div className="zg-notice zg-notice--warning" style={{ marginBottom: 12 }}>
          <AlertTriangle aria-hidden="true" />
          <span>{flow.notice}</span>
        </div>
      )}
      {statusCard && <StatusCard card={statusCard} onAction={onAction} />}

      {flow.status === "in_progress" && flow.step && (
        <>
          <h3 id={titleId} tabIndex={-1} ref={headingRef}>{flow.step.prompt}</h3>
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
          <span id={titleId} tabIndex={-1} ref={headingRef}>{flow.message}</span>
        </div>
      )}
      {flow.status === "escalated" && (
        <>
          <div className="zg-notice" role="status">
            <LifeBuoy aria-hidden="true" />
            <span id={titleId} tabIndex={-1} ref={headingRef}>{flow.message}</span>
          </div>
          <Actions actions={flow.actions} onAction={onAction} />
        </>
      )}
      {flow.status === "cancelled" && (
        <p id={titleId} tabIndex={-1} ref={headingRef} className="zg-meta" style={{ margin: 0 }}>
          {flow.resolution_code?.startsWith("switched_to_") || flow.resolution_code?.startsWith("category_")
            ? "Continuing below."
            : "Viewer help stopped."}
        </p>
      )}
    </section>
  );
}
