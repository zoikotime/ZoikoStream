// Chargeback case management (ZST-LE-COM-001 P4/Section 20).
//
// Disputes arrive INBOUND only — the card network opens them, Stripe exposes no create-dispute
// API, and crud.ingest_dispute_event applies them from the webhook. So there is nothing to
// "open" here: this panel is where Finance sees the cases that already exist, files evidence
// before the network's deadline, and records the outcome the network eventually returns.
//
// SCOPE NOTE: the API exposes disputes per ORDER (GET /commercial/orders/{id}/disputes) —
// there is no platform-wide dispute list endpoint, and adding one would be a backend change.
// So this mounts on the event's commercial page where the order is known, alongside the
// payments and refunds it relates to, rather than as a standalone finance queue.
import { useState } from "react";
import {
  FiAlertOctagon, FiClock, FiFileText, FiCheckCircle, FiUploadCloud,
} from "react-icons/fi";
import { Panel, Button, Badge } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { fmtDateTime } from "../../data/events";
import { money } from "../../utils/money";
import { DisputeEvidenceModal, DisputeResolveModal } from "./EventCommerceModals";

const label = (s) => (s || "—").split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");

// opened/evidence_required are live cases needing action; won/withdrawn are settled in our
// favour; lost means the funds are gone.
const DISPUTE_TONE = {
  opened: "warning",
  evidence_required: "danger",
  evidence_submitted: "info",
  won: "success",
  withdrawn: "neutral",
  lost: "danger",
};

const OPEN_STATUSES = ["opened", "evidence_required", "evidence_submitted"];
const FILTERS = [
  { key: "open", label: "Open" },
  { key: "resolved", label: "Resolved" },
  { key: "all", label: "All" },
];

// A deadline is only actionable while the case is still open — a won case with a passed
// due-by is not "overdue", it is finished.
function deadlineTone(dispute) {
  if (!dispute.evidence_due_by || !OPEN_STATUSES.includes(dispute.status)) return null;
  const hoursLeft = (new Date(dispute.evidence_due_by) - Date.now()) / 3_600_000;
  if (hoursLeft < 0) return { tone: "danger", text: "Deadline passed" };
  if (hoursLeft < 72) return { tone: "warning", text: `${Math.ceil(hoursLeft)}h left` };
  return { tone: "neutral", text: fmtDateTime(dispute.evidence_due_by) };
}

function EvidenceTrail({ evidence }) {
  // submit_dispute_evidence appends: {"submissions": [{submitted_at, submitted_by, evidence}]}.
  // Older rows may carry a flat dict, which the backend migrates into submissions[0] on the
  // next write — render both shapes rather than showing nothing for a legacy case.
  const submissions = evidence?.submissions;
  if (!evidence || (Array.isArray(submissions) && submissions.length === 0)) {
    return <p className="text-xs text-slate-400">No evidence submitted yet.</p>;
  }
  const rows = Array.isArray(submissions)
    ? submissions
    : [{ submitted_at: null, submitted_by: null, evidence }];
  return (
    <ol className="space-y-2">
      {rows.map((s, i) => (
        <li key={i} className="rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
          <p className="text-xs font-medium text-slate-400">
            Submission {i + 1}
            {s.submitted_at ? ` · ${fmtDateTime(s.submitted_at)}` : ""}
          </p>
          <dl className="mt-1 space-y-1">
            {Object.entries(s.evidence || {}).map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-slate-400">{label(k)}</dt>
                <dd className="whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">
                  {typeof v === "string" ? v : JSON.stringify(v)}
                </dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ol>
  );
}

export function DisputesPanel({ order, event, payments = [], onChanged }) {
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/commercial/orders/${order.id}/disputes`).then((r) => r.data));
  const [filter, setFilter] = useState("open");
  const [expanded, setExpanded] = useState(null);
  const [modal, setModal] = useState(null);          // { kind, dispute }
  const close = () => setModal(null);

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

  const all = data || [];
  const rows = all.filter((d) =>
    filter === "all" ? true
      : filter === "open" ? OPEN_STATUSES.includes(d.status)
        : !OPEN_STATUSES.includes(d.status));
  const openCount = all.filter((d) => OPEN_STATUSES.includes(d.status)).length;
  const paymentRef = (id) => payments.find((p) => p.id === id)?.provider_payment_ref;

  if (error) {
    return (
      <Panel title="Disputes">
        <p className="text-sm text-rose-600 dark:text-rose-400">Couldn&apos;t load disputes. {errMsg(error)}</p>
      </Panel>
    );
  }
  if (loading) {
    return <Panel title="Disputes"><div className="zk-skeleton h-16 w-full rounded-lg bg-slate-200 dark:bg-slate-800" /></Panel>;
  }
  if (all.length === 0) {
    return (
      <Panel title="Disputes">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          No chargebacks on this order. Disputes are opened by the cardholder&apos;s bank and
          arrive automatically as provider events — they are never raised from here.
        </p>
      </Panel>
    );
  }

  return (
    <Panel
      title="Disputes"
      count={all.length}
      action={
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <Button key={f.key} size="sm"
                    variant={filter === f.key ? "primary" : "secondary"}
                    onClick={() => setFilter(f.key)}>
              {f.label}
            </Button>
          ))}
        </div>
      }
    >
      <div className="space-y-3">
        {openCount > 0 && (
          <p className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
            <FiAlertOctagon className="shrink-0" />
            {openCount} open case{openCount === 1 ? "" : "s"} — funds are contested until the
            network decides.
          </p>
        )}

        {rows.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No {filter} disputes.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {rows.map((d) => {
              const deadline = deadlineTone(d);
              const isOpen = OPEN_STATUSES.includes(d.status);
              return (
                <li key={d.id} className="py-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium text-slate-800 dark:text-slate-100">
                        {money(d.amount, d.currency)} · {label(d.reason_code)}
                      </p>
                      <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                        {d.provider} · {d.provider_dispute_ref}
                        {paymentRef(d.payment_id) ? ` · ${paymentRef(d.payment_id)}` : ""}
                        {event?.title ? ` · ${event.title}` : ""}
                      </p>
                      <p className="text-xs text-slate-400">
                        Opened {fmtDateTime(d.opened_at)}
                        {Number(d.reserve_amount) > 0 && (
                          <> · reserve {money(d.reserve_amount, d.currency)}</>
                        )}
                        {d.case_owner_id ? " · owner assigned" : " · unassigned"}
                        {d.resolved_at ? ` · resolved ${fmtDateTime(d.resolved_at)}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      {deadline && (
                        <Badge status={deadline.tone}>
                          <FiClock className="mr-1 inline" />{deadline.text}
                        </Badge>
                      )}
                      <Badge status={DISPUTE_TONE[d.status] || "neutral"}>{label(d.status)}</Badge>
                      <Button variant="secondary" size="sm" leftIcon={FiFileText}
                              onClick={() => setExpanded(expanded === d.id ? null : d.id)}>
                        {expanded === d.id ? "Hide" : "Details"}
                      </Button>
                      {isOpen && (
                        <>
                          <Button variant="secondary" size="sm" leftIcon={FiUploadCloud}
                                  onClick={() => setModal({ kind: "evidence", dispute: d })}>
                            Evidence
                          </Button>
                          <Button size="sm" leftIcon={FiCheckCircle}
                                  onClick={() => setModal({ kind: "resolve", dispute: d })}>
                            Outcome
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  {expanded === d.id && (
                    <div className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800/40">
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
                        <dt className="text-xs text-slate-400">Order</dt>
                        <dd className="text-slate-600 dark:text-slate-300">{d.event_order_id.slice(0, 8)}</dd>
                        <dt className="text-xs text-slate-400">Payment</dt>
                        <dd className="text-slate-600 dark:text-slate-300">{d.payment_id.slice(0, 8)}</dd>
                        <dt className="text-xs text-slate-400">Disputed</dt>
                        <dd className="text-slate-600 dark:text-slate-300">{money(d.amount, d.currency)}</dd>
                        <dt className="text-xs text-slate-400">Reserve held</dt>
                        <dd className="text-slate-600 dark:text-slate-300">{money(d.reserve_amount, d.currency)}</dd>
                      </dl>
                      <EvidenceTrail evidence={d.evidence} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {modal?.kind === "evidence" && (
        <DisputeEvidenceModal
          dispute={modal.dispute} onClose={close}
          onCreate={(body) => act(
            () => api.post(`/commercial/disputes/${modal.dispute.id}/evidence`, body),
            "Evidence submitted",
          )}
        />
      )}
      {modal?.kind === "resolve" && (
        <DisputeResolveModal
          dispute={modal.dispute} onClose={close}
          onCreate={(body) => act(
            () => api.post(`/commercial/disputes/${modal.dispute.id}/resolve`, body),
            body.won ? "Dispute won — payment returned to paid" : "Dispute lost — payment reversed",
          )}
        />
      )}
    </Panel>
  );
}

export default DisputesPanel;
