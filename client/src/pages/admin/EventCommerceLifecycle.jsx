// Commercial lifecycle timeline + refund/credit approval — the two staff surfaces the
// commercial engine had no UI for (ZST-LE-COM-001 Section 28, Section 15/K).
//
// The lifecycle is DERIVED server-side from committed facts, so this panel is read-only by
// construction: there is no endpoint to move it. What it shows instead is which gate is
// outstanding, which is the only thing that actually moves it.
import { useState } from "react";
import {
  FiClock, FiAlertTriangle, FiCheckCircle, FiCornerUpLeft,
} from "react-icons/fi";
import { Panel, Button, Badge } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { fmtDateTime } from "../../data/events";
import { money } from "../../utils/money";

const label = (s) => (s || "—").split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");

// The canonical order from the standard. Rendered as a rail so an operator can see how far an
// order has progressed and what is next, rather than reading a bare status word.
const LIFECYCLE_ORDER = [
  "draft", "quoted", "order_accepted", "financial_hold", "capacity_held",
  "confirmed", "ready", "live", "completed",
];

const STATE_TONE = {
  draft: "neutral", quoted: "info", order_accepted: "info",
  financial_hold: "danger", capacity_held: "warning",
  confirmed: "success", ready: "success", live: "success",
  completed: "success", canceled: "danger",
};

export function LifecyclePanel({ eventId, orderId }) {
  // Keyed by order when one exists, by event otherwise — a DRAFT/QUOTED event has no order
  // yet, and that is exactly the state most worth showing.
  const path = orderId
    ? `/commercial/orders/${orderId}/lifecycle`
    : `/commercial/events/${eventId}/lifecycle`;
  const { data, loading, error } = useApi(() => api.get(path).then((r) => r.data));
  const [showHistory, setShowHistory] = useState(false);

  if (error) {
    return (
      <Panel title="Commercial lifecycle">
        <p className="text-sm text-rose-600 dark:text-rose-400">Couldn't load lifecycle. {errMsg(error)}</p>
      </Panel>
    );
  }
  if (loading || !data) {
    return <Panel title="Commercial lifecycle"><div className="zk-skeleton h-20 w-full rounded-lg bg-slate-200 dark:bg-slate-800" /></Panel>;
  }

  const currentIndex = LIFECYCLE_ORDER.indexOf(data.state);
  const canceled = data.state === "canceled";
  const history = data.history || [];

  return (
    <Panel
      title="Commercial lifecycle"
      action={
        history.length > 0 && (
          <Button variant="secondary" size="sm" leftIcon={FiClock} onClick={() => setShowHistory((v) => !v)}>
            {showHistory ? "Hide history" : `History (${history.length})`}
          </Button>
        )
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge status={STATE_TONE[data.state] || "neutral"}>{label(data.state)}</Badge>
          {data.financial_state && (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              Financial: {label(data.financial_state)}
            </span>
          )}
          {data.capacity_satisfied != null && (
            <span className="text-xs text-slate-500 dark:text-slate-400">
              · Capacity: {data.capacity_satisfied ? "reserved" : data.capacity_held ? "partial" : "none"}
            </span>
          )}
          {data.readiness_verdict && (
            <span className="text-xs text-slate-500 dark:text-slate-400">· {label(data.readiness_verdict)}</span>
          )}
        </div>

        {/* Progress rail. Canceled is terminal and off-path, so it is shown as its own state
            rather than a position on the rail. */}
        {!canceled && (
          <div className="flex flex-wrap gap-1">
            {LIFECYCLE_ORDER.map((state, i) => (
              <div
                key={state}
                title={label(state)}
                className={[
                  "h-1.5 flex-1 min-w-[24px] rounded-full",
                  i < currentIndex ? "bg-emerald-400 dark:bg-emerald-500"
                    : i === currentIndex ? "bg-violet-500"
                      : "bg-slate-200 dark:bg-slate-700",
                ].join(" ")}
              />
            ))}
          </div>
        )}
        {!canceled && (
          <p className="text-xs text-slate-400">
            {LIFECYCLE_ORDER.map(label).join(" → ")}
          </p>
        )}

        {/* The gates actually holding it back. This is the operative content of the panel —
            the state name alone never tells anyone what to do next. */}
        {data.blocking_reasons?.length > 0 ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
            <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
              <FiAlertTriangle /> Outstanding gates
            </p>
            <ul className="mt-1.5 space-y-1">
              {data.blocking_reasons.map((r, i) => (
                <li key={i} className="text-sm text-amber-800 dark:text-amber-300">• {r}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p className="flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
            <FiCheckCircle /> No outstanding commercial gates.
          </p>
        )}

        {showHistory && (
          <ul className="space-y-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
            {history.map((t) => (
              <li key={t.id} className="flex items-start justify-between gap-3 text-sm">
                <span className="min-w-0 text-slate-600 dark:text-slate-300">
                  {t.from_state ? `${label(t.from_state)} → ` : ""}
                  <strong>{label(t.to_state)}</strong>
                  <span className="ml-1.5 text-xs text-slate-400">{t.trigger}</span>
                  {t.illegal && (
                    <Badge status="danger" className="ml-1.5">unexpected</Badge>
                  )}
                  {/* The human rationale, where the operation carried one (a cancellation
                      reason, a reschedule reason, a write-off justification). */}
                  {t.reason && (
                    <span className="mt-0.5 block text-xs italic text-slate-400">{t.reason}</span>
                  )}
                </span>
                <span className="shrink-0 text-xs text-slate-400">{fmtDateTime(t.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

// ── Refunds & credits (doc Section 15/K, maker-checker) ───────────────────────────────────

const REFUND_TONE = { pending: "warning", approved: "info", executed: "success", declined: "neutral" };

export function RefundsPanel({ order, currency, onChanged }) {
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/commercial/orders/${order.id}/refund-credits`).then((r) => r.data));
  const rows = data || [];

  const act = async (fn, success) => {
    try {
      await fn();
      notify.success(success);
      reload();
      onChanged?.();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  if (error) {
    return (
      <Panel title="Refunds & credits">
        <p className="text-sm text-rose-600 dark:text-rose-400">Couldn't load remedies. {errMsg(error)}</p>
      </Panel>
    );
  }
  if (loading) {
    return <Panel title="Refunds & credits"><div className="zk-skeleton h-16 w-full rounded-lg bg-slate-200 dark:bg-slate-800" /></Panel>;
  }
  if (rows.length === 0) {
    return (
      <Panel title="Refunds & credits">
        <p className="text-sm text-slate-500 dark:text-slate-400">No refunds or credits on this order.</p>
      </Panel>
    );
  }

  return (
    <Panel title="Refunds & credits" count={rows.length}>
      <div className="space-y-2">
        <p className="text-xs text-slate-400">
          Approval requires a different person than the requester, and only a refund bound to a
          source payment can return money.
        </p>
        <ul className="space-y-1.5">
          {rows.map((rc) => (
            <li key={rc.id} className="flex items-center justify-between gap-3 text-sm">
              <span className="min-w-0 text-slate-600 dark:text-slate-300">
                <span className="font-medium">{money(rc.amount, currency)}</span>
                {" · "}{label(rc.type)}
                <span className="ml-1.5 truncate text-xs text-slate-400">{rc.reason_code}</span>
                {rc.type === "refund" && !rc.source_payment_id && (
                  <span className="ml-1.5 text-xs text-rose-500">no source payment — cannot return money</span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <Badge status={REFUND_TONE[rc.status] || "neutral"}>{label(rc.status)}</Badge>
                {rc.status === "pending" && (
                  <Button variant="secondary" size="sm" leftIcon={FiCheckCircle}
                          onClick={() => act(() => api.post(`/commercial/refund-credits/${rc.id}/approve`), "Remedy approved")}>
                    Approve
                  </Button>
                )}
                {rc.status === "approved" && (
                  <Button size="sm" leftIcon={FiCornerUpLeft}
                          onClick={() => act(() => api.post(`/commercial/refund-credits/${rc.id}/execute`), "Remedy executed")}>
                    Execute
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}
