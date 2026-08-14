import { FiCheckCircle, FiExternalLink, FiXCircle } from "react-icons/fi";
import { Badge, DetailField, CONSOLE, cx } from "../../components/admin";
import Drawer from "../../ui/Drawer";

// Per-event readiness record — the drawer opened from a Readiness pipeline row. Every field
// here comes straight off the GET /admin/event-readiness row (services/ops.py's
// event_readiness/_gate_results): real gates, computed from the event's real configuration.
// There is no control in this drawer that edits a gate — resolve the underlying
// configuration (assign a host, enable recording, resolve a single-path override in
// Governance) and the verdict changes itself on the next refresh.

const VERDICT_COPY = {
  passed: { title: "Ready to operate", body: "Every mandatory gate for this event's impact class passes." },
  conditional: { title: "Ready with open items", body: "Every mandatory gate passes; one or more non-mandatory gates are still open." },
  blocked: { title: "Not ready to operate", body: "At least one mandatory gate for this event's impact class is not satisfied." },
};

const VERDICT_BANNER = {
  passed: "border-green-200 bg-green-50 dark:border-green-500/25 dark:bg-green-500/10",
  conditional: "border-amber-200 bg-amber-50 dark:border-amber-500/25 dark:bg-amber-500/10",
  blocked: "border-rose-200 bg-rose-50 dark:border-rose-500/25 dark:bg-rose-500/10",
};

const IMPACT_LABEL = { standard: "Standard", high: "High", unrepeatable: "Unrepeatable" };
const IMPACT_TONE = { standard: "neutral", high: "warning", unrepeatable: "danger" };

function GateRow({ gate }) {
  return (
    <li className="flex items-start gap-2.5 py-2">
      {gate.passed ? (
        <FiCheckCircle className="mt-0.5 shrink-0 text-green-500" aria-hidden="true" />
      ) : (
        <FiXCircle className={cx("mt-0.5 shrink-0", gate.required ? "text-rose-500" : "text-slate-400")} aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1">
        <p className={cx("text-[13px]", CONSOLE.body)}>{gate.label}</p>
      </div>
      {gate.required && <Badge tone="neutral" size="sm">Mandatory</Badge>}
    </li>
  );
}

export default function ReadinessRecordDrawer({ event, open, onClose }) {
  if (!event) return <Drawer open={open} onClose={onClose} title="Readiness record" />;

  const copy = VERDICT_COPY[event.verdict] || VERDICT_COPY.blocked;

  return (
    <Drawer open={open} onClose={onClose} title={event.title} width="w-[28rem] max-w-[90vw]">
      <div className={cx("rounded-xl border px-4 py-3", VERDICT_BANNER[event.verdict])}>
        <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{copy.title}</p>
        <p className={cx("mt-0.5 text-[12px]", CONSOLE.muted)}>{copy.body}</p>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <DetailField label="Organization" value={event.organization || "—"} />
        <DetailField
          label="Impact"
          value={<Badge tone={IMPACT_TONE[event.impact]}>{IMPACT_LABEL[event.impact] || event.impact}</Badge>}
        />
        <DetailField
          label="Start"
          value={event.start_time ? new Date(event.start_time).toLocaleString() : "—"}
        />
        <DetailField label="Timezone" value={event.timezone || "—"} />
      </div>

      <div className="mt-5">
        <p className={cx("mb-1 text-[11px] font-semibold uppercase tracking-wider", CONSOLE.faint)}>
          Gates
        </p>
        <ul className={cx("divide-y", CONSOLE.divider)}>
          {event.gates.map((g) => (
            <GateRow key={g.key} gate={g} />
          ))}
        </ul>
        <p className={cx("mt-3 text-[11px] leading-relaxed", CONSOLE.faint)}>
          "Mandatory" gates are required for this event's impact class (High/Unrepeatable);
          an unsatisfied mandatory gate blocks the event. Standard-impact events have no
          mandatory gates.
        </p>
      </div>

      <a
        href={`/admin/live-events/${event.id}`}
        className="mt-5 inline-flex items-center gap-1.5 text-[13px] font-medium text-violet-600 hover:text-violet-500 dark:text-violet-400"
      >
        Open in Live Operations <FiExternalLink aria-hidden="true" />
      </a>
    </Drawer>
  );
}
