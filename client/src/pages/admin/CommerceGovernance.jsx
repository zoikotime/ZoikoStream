// Finance governance: the commercial exception queue and financial period close
// (ZST-LE-COM-001 Section 25 maker-checker, Section 29 period close).
//
// Both were API-only. The exception queue is where every money-affecting override is decided
// — discount, price override, waiver, write-off, financial-hold override, exceptional
// cancellation, complimentary event, risk-tier reduction — and period close is where a
// month's commercial activity is frozen for accounting.
//
// Lives in its own module rather than inside Commerce.jsx because that page already carries
// six tabs; two more tab bodies inline would push it past the point of being reviewable.
// CommerceFinanceOps.jsx set the same precedent for seller entities + capacity pools.
import { useState } from "react";
import {
  FiShield, FiCheckCircle, FiXCircle, FiPlus, FiLock, FiDollarSign,
} from "react-icons/fi";
import { Panel, Button, Badge, StatCard } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { fmtDateTime } from "../../data/events";
import { ExceptionDecisionModal, PeriodModal, PeriodCloseModal } from "./EventCommerceModals";

const label = (s) => (s || "—").split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");

const EXCEPTION_TONE = {
  requested: "warning", approved: "success", declined: "neutral", expired: "danger",
};

// Which exception types move money directly. Surfaced so a reviewer can see at a glance that
// this is a revenue decision rather than an operational one.
const MONEY_TYPES = new Set([
  "discount", "price_override", "waiver", "write_off", "complimentary_event",
  "exceptional_cancellation",
]);

const STATUS_FILTERS = [
  { key: "requested", label: "Pending" },
  { key: "approved", label: "Approved" },
  { key: "declined", label: "Declined" },
  { key: "all", label: "All" },
];

// ── Exceptions ────────────────────────────────────────────────────────────────────────────

export function ExceptionsTab() {
  // Fetched once unfiltered and narrowed in memory — useApi has no dependency array (it
  // refetches only on reload()), and the queue is small enough that a request per filter click
  // would be pure latency. Same shape as Subscriptions.jsx.
  const { data, loading, error, reload } = useApi(() =>
    api.get("/commercial/commercial-exceptions").then((r) => r.data));
  const [status, setStatus] = useState("requested");
  const [modal, setModal] = useState(null);          // { kind, exception }
  const close = () => setModal(null);
  const all = data || [];
  const rows = status === "all" ? all : all.filter((e) => e.status === status);

  const act = async (fn, success) => {
    try {
      await fn();
      notify.success(success);
      reload();
    } catch (e) {
      // Maker-checker violations, already-decided exceptions and expiry all surface here as
      // the server's own message — the rule lives on the server and this never second-guesses it.
      notify.error(errMsg(e));
    }
  };

  // Stats describe the whole queue, not the current filter — a reviewer needs the standing
  // pending count regardless of which tab they are looking at.
  const pending = all.filter((e) => e.status === "requested").length;
  const exposure = all
    .filter((e) => e.status === "requested" && e.amount_exposure)
    .reduce((s, e) => s + Number(e.amount_exposure), 0);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
        <StatCard label="Awaiting decision" value={pending} />
        <StatCard label="Exposure pending" value={exposure ? exposure.toLocaleString() : "—"} />
        <StatCard label="Shown" value={rows.length} />
      </div>

      <div className="flex flex-wrap justify-between gap-2">
        <div className="flex gap-1">
          {STATUS_FILTERS.map((f) => (
            <Button key={f.key} size="sm"
                    variant={status === f.key ? "primary" : "secondary"}
                    onClick={() => setStatus(f.key)}>
              {f.label}
            </Button>
          ))}
        </div>
      </div>

      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">
            Couldn&apos;t load exceptions. {errMsg(error)}
          </p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiShield className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
              No {status === "all" ? "" : label(status).toLowerCase()} exceptions
            </p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Overrides are raised against a specific order or event, never globally.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && Array.from({ length: 3 }).map((_, i) => (
              <li key={i} className="px-5 py-4">
                <div className="zk-skeleton h-4 w-64 rounded bg-slate-200 dark:bg-slate-800" />
              </li>
            ))}
            {!loading && rows.map((e) => (
              <li key={e.id} className="px-5 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 font-medium text-slate-800 dark:text-slate-100">
                      {label(e.exception_type)}
                      {MONEY_TYPES.has(e.exception_type) && (
                        <Badge status="warning"><FiDollarSign className="mr-1 inline" />Revenue</Badge>
                      )}
                      {e.overridden_gate && (
                        <span className="text-xs font-normal text-slate-400">
                          overrides {label(e.overridden_gate)}
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{e.rationale}</p>
                    <p className="mt-1 text-xs text-slate-400">
                      {e.event_order_id ? `Order ${e.event_order_id.slice(0, 8)}` : ""}
                      {e.event_id ? ` · Event ${e.event_id.slice(0, 8)}` : ""}
                      {e.amount_exposure != null ? ` · exposure ${e.amount_exposure}` : ""}
                      {` · requested ${fmtDateTime(e.created_at)}`}
                      {e.expiry_at ? ` · expires ${fmtDateTime(e.expiry_at)}` : ""}
                    </p>
                    {/* Maker-checker visibility: both parties named on the row, so a reviewer
                        can see at a glance that two different people were involved. */}
                    <p className="mt-1 text-xs text-slate-400">
                      Requested by {e.requested_by ? e.requested_by.slice(0, 8) : "—"}
                      {e.approver_id
                        ? ` · decided by ${e.approver_id.slice(0, 8)}`
                        : " · awaiting a different approver"}
                      {e.decided_at ? ` · ${fmtDateTime(e.decided_at)}` : ""}
                    </p>
                    {e.evidence && (
                      <p className="mt-1 text-xs text-slate-400">
                        Evidence: {Object.keys(e.evidence).join(", ")}
                      </p>
                    )}
                    {e.decision_notes && (
                      <p className="mt-1 text-xs italic text-slate-400">{e.decision_notes}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge status={EXCEPTION_TONE[e.status] || "neutral"}>{label(e.status)}</Badge>
                    {e.status === "requested" && (
                      <>
                        <Button variant="secondary" size="sm" leftIcon={FiXCircle}
                                onClick={() => setModal({ kind: "decline", exception: e })}>
                          Decline
                        </Button>
                        <Button size="sm" leftIcon={FiCheckCircle}
                                onClick={() => setModal({ kind: "approve", exception: e })}>
                          Approve
                        </Button>
                      </>
                    )}
                    {/* A write-off is approved like any other exception, then EXECUTED as a
                        separate action — approval authorises, execution applies the credit. */}
                    {e.status === "approved" && e.exception_type === "write_off" && (
                      <Button size="sm" leftIcon={FiDollarSign}
                              onClick={() => act(
                                () => api.post(`/commercial/commercial-exceptions/${e.id}/execute-write-off`),
                                "Write-off executed — the balance is waived",
                              )}>
                        Execute write-off
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {modal && (
        <ExceptionDecisionModal
          exception={modal.exception}
          decision={modal.kind}
          onClose={close}
          onCreate={(body) => act(
            () => api.post(`/commercial/commercial-exceptions/${modal.exception.id}/${modal.kind}`, body),
            modal.kind === "approve" ? "Exception approved" : "Exception declined",
          )}
        />
      )}
    </div>
  );
}

// ── Financial periods ─────────────────────────────────────────────────────────────────────

function PeriodExceptions({ periodId }) {
  const { data, loading } = useApi(() =>
    api.get(`/commercial/periods/${periodId}/exceptions`).then((r) => r.data));
  if (loading) return <p className="text-xs text-slate-400">Loading findings…</p>;
  if (!data || data.length === 0) {
    return <p className="text-xs text-emerald-600 dark:text-emerald-400">Closed with no findings.</p>;
  }
  return (
    <ul className="space-y-1">
      {data.map((x) => (
        <li key={x.id} className="flex items-start justify-between gap-3 text-sm">
          <span className="text-slate-600 dark:text-slate-300">
            <span className="text-xs text-slate-400">{label(x.category)}</span>
            <span className="mt-0.5 block">{x.description}</span>
          </span>
          <Badge status={x.status === "open" ? "warning" : "neutral"}>{label(x.status)}</Badge>
        </li>
      ))}
    </ul>
  );
}

export function PeriodsTab() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/commercial/periods").then((r) => r.data));
  // The live reconciliation report is what close will file as exceptions. Fetched here so the
  // confirmation dialog can show what is about to be recorded rather than asking someone to
  // close blind.
  const { data: recon } = useApi(() =>
    api.get("/commercial/reconciliation").then((r) => r.data));
  const [modal, setModal] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const close = () => setModal(null);
  const rows = data || [];

  const act = async (fn, success) => {
    try {
      await fn();
      notify.success(success);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const blockers = [
    { label: "Capacity without order", count: recon?.reservations_without_order?.length || 0 },
    { label: "Orphaned reservations", count: recon?.reservations_with_missing_order?.length || 0 },
    { label: "Unmatched settlements", count: recon?.unmatched_settlements?.length || 0 },
    { label: "Orders missing invoice", count: recon?.orders_missing_invoice?.length || 0 },
    { label: "Orders missing seller entity", count: recon?.orders_missing_seller_entity?.length || 0 },
  ];

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModal({ kind: "create" })}>
          Open period
        </Button>
      </div>

      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">
            Couldn&apos;t load periods. {errMsg(error)}
          </p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiLock className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No financial periods</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Open one to freeze a month&apos;s commercial activity for accounting.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {!loading && rows.map((p) => (
              <li key={p.id} className="px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-slate-800 dark:text-slate-100">{p.label}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {fmtDateTime(p.period_start)} – {fmtDateTime(p.period_end)}
                    </p>
                    <p className="text-xs text-slate-400">
                      Created {fmtDateTime(p.created_at)}
                      {p.closed_at ? ` · closed ${fmtDateTime(p.closed_at)}` : ""}
                      {p.closed_by ? ` by ${p.closed_by.slice(0, 8)}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge status={p.status === "closed" ? "neutral" : "success"}>
                      {label(p.status)}
                    </Badge>
                    {p.status === "closed" && (
                      <Button variant="secondary" size="sm"
                              onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                        {expanded === p.id ? "Hide findings" : "Findings"}
                      </Button>
                    )}
                    {p.status === "open" && (
                      <Button size="sm" leftIcon={FiLock}
                              onClick={() => setModal({ kind: "close", period: p })}>
                        Close period
                      </Button>
                    )}
                  </div>
                </div>
                {expanded === p.id && (
                  <div className="mt-3 space-y-3 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/40">
                    {p.snapshot && (
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
                        {Object.entries(p.snapshot).map(([k, v]) => (
                          <div key={k}>
                            <dt className="text-xs text-slate-400">{label(k)}</dt>
                            <dd className="text-slate-600 dark:text-slate-300">
                              {v?.count != null ? `${v.count}` : ""}
                              {v?.total_amount ? ` · ${v.total_amount}` : ""}
                              {v?.collected_amount ? ` · ${v.collected_amount}` : ""}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    <PeriodExceptions periodId={p.id} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {modal?.kind === "create" && (
        <PeriodModal
          onClose={close}
          onCreate={(body) => act(() => api.post("/commercial/periods", body), "Period opened")}
        />
      )}
      {modal?.kind === "close" && (
        <PeriodCloseModal
          period={modal.period} blockers={blockers} onClose={close}
          onCreate={() => act(
            () => api.post(`/commercial/periods/${modal.period.id}/close`),
            "Period closed — snapshot frozen",
          )}
        />
      )}
    </div>
  );
}

// Named exports only: both tabs are mounted by Commerce.jsx, which owns the TabStrip.
export default ExceptionsTab;
