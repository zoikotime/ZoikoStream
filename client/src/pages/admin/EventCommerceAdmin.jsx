import { useState } from "react";
import {
  FiPlus, FiFileText, FiShield, FiActivity, FiCheckCircle,
  FiAlertTriangle, FiUpload, FiUnlock,
} from "react-icons/fi";
import { Panel, Button, Badge } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";
import { fmtDateTime } from "../../data/events";
import {
  NewQuoteModal, NewOrderModal, AddLineModal, CapacityHoldModal,
  PaymentScheduleModal, ReadinessCheckModal, IncidentModal, RemedyModal,
} from "./EventCommerceModals";

// Staff side of constructing a Live Event commercial order (ZST-LE-COM-001). The customer
// (org_admin) only ever sees and accepts what gets built here — see
// components/organization/EventCommercial.jsx for that read/accept-only counterpart.
// Every write below is a require_super_admin route (routers/commercial.py's RBAC mapping).

const money = (amount, currency = "USD") => {
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  if (amount == null || Number.isNaN(n)) return "—";
  try { return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(n); }
  catch { return `${n} ${currency || ""}`.trim(); }
};
const label = (s) => (s || "—").split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");
const ORDER_TONE = { draft: "neutral", pending_acceptance: "warning", accepted: "info", active: "success", completed: "success", canceled: "danger", terminated: "danger" };
const QUOTE_TONE = { draft: "neutral", issued: "warning", accepted: "success", expired: "danger", withdrawn: "danger", superseded: "neutral" };
const PAYMENT_TONE = { requires_action: "warning", pending: "warning", partially_paid: "warning", paid: "success", failed: "danger", refunded: "neutral", part_refunded: "neutral", reversed: "danger", disputed: "danger", unmatched: "warning" };

const orNull = (p) => p.catch((e) => (e?.response?.status === 404 ? null : Promise.reject(e)));

function useAdminCommerceData(eventId) {
  return useApi(async () => {
    const [quotes, order, capacity, readiness, incidents, catalogVersions, serviceProfiles, cancellationPolicies] = await Promise.all([
      api.get(`/commercial/events/${eventId}/quotes`).then((r) => r.data),
      orNull(api.get(`/commercial/events/${eventId}/orders/current`).then((r) => r.data)),
      api.get(`/commercial/events/${eventId}/capacity`).then((r) => r.data),
      api.get(`/commercial/events/${eventId}/readiness`).then((r) => r.data),
      api.get(`/commercial/events/${eventId}/incidents`).then((r) => r.data),
      api.get("/commercial/catalog-versions?status_=published").then((r) => r.data),
      api.get("/commercial/service-profiles?status_=published").then((r) => r.data),
      api.get("/commercial/cancellation-policies?status_=published").then((r) => r.data),
    ]);
    const orders = await api.get(`/commercial/events/${eventId}/orders`).then((r) => r.data);
    let schedule = [], payments = [], invoices = [];
    if (order) {
      [schedule, payments, invoices] = await Promise.all([
        api.get(`/commercial/orders/${order.id}/payment-schedule`).then((r) => r.data),
        api.get(`/commercial/orders/${order.id}/payments`).then((r) => r.data),
        api.get(`/commercial/orders/${order.id}/invoices`).then((r) => r.data),
      ]);
    }
    return { quotes, order, orders, capacity, readiness, incidents, catalogVersions, serviceProfiles, cancellationPolicies, schedule, payments, invoices };
  });
}

export default function EventCommerceAdmin({ eventId }) {
  const { data, loading, error, reload } = useAdminCommerceData(eventId);
  const [modal, setModal] = useState(null); // { kind, ... }
  const close = () => setModal(null);

  const act = async (fn, success) => {
    try {
      await fn();
      notify.success(success);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  if (error) {
    return <Panel title="Commercial"><p className="text-sm text-rose-600 dark:text-rose-400">Couldn't load commercial data. {errMsg(error)}</p></Panel>;
  }
  if (loading) {
    return <Panel title="Commercial"><div className="zk-skeleton h-40 w-full rounded-lg bg-slate-200 dark:bg-slate-800" /></Panel>;
  }

  const { quotes, order, orders, capacity, readiness, incidents, catalogVersions, serviceProfiles, cancellationPolicies, schedule, payments, invoices } = data;
  const selectedCatalogVersion = order ? catalogVersions.find((v) => v.id === order.catalog_version_id) : null;
  const acceptedOrders = orders.filter((o) => ["accepted", "active", "completed"].includes(o.status));

  return (
    <div className="space-y-6">
      <Panel
        eyebrow="ZST-LE-COM-001"
        title="Quotes"
        action={<Button size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "quote" })}>New quote</Button>}
      >
        {quotes.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No quotes yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {quotes.map((q) => (
              <li key={q.id} className="flex items-center justify-between gap-3 py-2.5">
                <span className="text-sm text-slate-700 dark:text-slate-200">v{q.version} · {money(q.amount, q.currency)}</span>
                <span className="flex items-center gap-2">
                  <Badge status={QUOTE_TONE[q.status] || "neutral"}>{label(q.status)}</Badge>
                  {q.status === "draft" && (
                    <Button variant="secondary" size="sm" leftIcon={FiUpload}
                      onClick={() => act(() => api.post(`/commercial/events/${eventId}/quotes/${q.id}/issue`), "Quote issued")}>
                      Issue
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Order"
        description={order ? `Version ${order.order_version}` : "No order for this event yet"}
        action={
          !order && (
            <Button size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "order" })} disabled={catalogVersions.length === 0}>
              New order
            </Button>
          )
        }
      >
        {!order ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {catalogVersions.length === 0 ? "Publish a catalog version first (Live Events Commerce)." : "Build an order to start this event's commercial lifecycle."}
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Badge status={ORDER_TONE[order.status] || "neutral"}>{label(order.status)}</Badge>
              <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{money(order.total_amount, order.currency)}</span>
              <Badge status="brand">{order.risk_tier.toUpperCase()}</Badge>
              <div className="ml-auto flex gap-2">
                {order.status === "draft" && (
                  <Button variant="secondary" size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "line" })}>Add line</Button>
                )}
                {order.status === "draft" && order.lines.length > 0 && (
                  <Button size="sm" leftIcon={FiUpload} onClick={() => act(() => api.post(`/commercial/events/${eventId}/orders/${order.id}/submit`), "Submitted for acceptance")}>
                    Submit for acceptance
                  </Button>
                )}
                {order.status === "accepted" && (
                  <Button size="sm" leftIcon={FiCheckCircle} onClick={() => act(() => api.post(`/commercial/events/${eventId}/orders/${order.id}/activate`), "Order activated")}>
                    Activate
                  </Button>
                )}
              </div>
            </div>
            {order.lines.length > 0 && (
              <table className="w-full text-sm">
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {order.lines.map((l) => (
                    <tr key={l.id}>
                      <td className="py-1.5">{l.description || l.service_code}</td>
                      <td className="py-1.5 text-right text-slate-500">{l.quantity} × {money(l.unit_price, order.currency)}</td>
                      <td className="py-1.5 text-right font-medium">{money(l.line_total, order.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </Panel>

      <Panel
        title="Capacity"
        action={<Button size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "capacity" })}>Soft-hold</Button>}
      >
        {capacity.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No capacity reservations.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {capacity.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <span className="text-slate-700 dark:text-slate-200">{label(c.resource_type)}</span>
                <span className="flex items-center gap-2">
                  <Badge status={c.state === "hard_reserved" ? "success" : c.state === "released" || c.state === "expired" ? "neutral" : "warning"}>{label(c.state)}</Badge>
                  {c.state === "soft_held" && order && ["accepted", "active"].includes(order.status) && (
                    <Button variant="secondary" size="sm" leftIcon={FiShield}
                      onClick={() => act(() => api.post(`/commercial/events/${eventId}/capacity/${c.id}/reserve`, null, { params: { order_id: order.id } }), "Capacity hard-reserved")}>
                      Hard-reserve
                    </Button>
                  )}
                  {c.state === "hard_reserved" && (
                    <Button variant="secondary" size="sm" leftIcon={FiUnlock}
                      onClick={() => act(() => api.post(`/commercial/events/${eventId}/capacity/${c.id}/release`, null, { params: { reason: "manual_release" } }), "Capacity released")}>
                      Release
                    </Button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {order && (
        <Panel
          title="Payments & invoices"
          action={
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "schedule" })}>Add milestone</Button>
              <Button size="sm" leftIcon={FiFileText} onClick={() => act(() => api.post(`/commercial/orders/${order.id}/invoices`, {}), "Invoice issued")}>Issue invoice</Button>
            </div>
          }
        >
          <div className="space-y-4">
            {schedule.length > 0 && (
              <ul className="space-y-1.5">
                {schedule.map((s) => (
                  <li key={s.id} className="flex items-center justify-between text-sm">
                    <span className="text-slate-600 dark:text-slate-300">{label(s.milestone)}{s.due_at ? ` · due ${fmtDateTime(s.due_at)}` : ""}</span>
                    <span className="flex items-center gap-2"><span className="font-medium">{money(s.amount, order.currency)}</span><Badge status={s.status === "financial_hold" ? "danger" : s.status === "satisfied" ? "success" : "warning"}>{label(s.status)}</Badge></span>
                  </li>
                ))}
              </ul>
            )}
            {payments.length > 0 && (
              <ul className="space-y-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
                {payments.map((p) => (
                  <li key={p.id} className="flex items-center justify-between text-sm">
                    <span className="text-slate-600 dark:text-slate-300">{p.provider} · {money(p.amount, p.currency)}</span>
                    <span className="flex items-center gap-2">
                      <Badge status={PAYMENT_TONE[p.state] || "neutral"}>{label(p.state)}</Badge>
                      {p.state === "pending" && (
                        <Button variant="secondary" size="sm" onClick={() => act(() => api.post(`/commercial/payments/${p.id}/capture`), "Payment captured")}>Capture</Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {invoices.length > 0 && (
              <ul className="space-y-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
                {invoices.map((inv) => (
                  <li key={inv.id} className="flex items-center justify-between text-sm">
                    <span className="text-slate-600 dark:text-slate-300">{inv.number}</span>
                    <span className="font-medium">{money(inv.total_amount, inv.currency)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      )}

      <Panel
        title="Readiness"
        action={
          <Button size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "readiness" })}>Record check</Button>
        }
      >
        <div className="mb-3 flex items-center gap-2">
          <Badge status={readiness.ready ? "success" : "warning"}>{readiness.ready ? "READY" : "Not ready"}</Badge>
        </div>
        {readiness.blocking_reasons.length > 0 && (
          <ul className="space-y-1 text-sm text-amber-600 dark:text-amber-400">
            {readiness.blocking_reasons.map((r, i) => <li key={i} className="flex items-start gap-2"><FiAlertTriangle className="mt-0.5 shrink-0" /> {r}</li>)}
          </ul>
        )}
      </Panel>

      <Panel
        title="Incidents & remedies"
        action={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" leftIcon={FiPlus} onClick={() => setModal({ kind: "remedy" })} disabled={incidents.length === 0 || acceptedOrders.length === 0}>
              Propose remedy
            </Button>
            <Button size="sm" leftIcon={FiActivity} onClick={() => setModal({ kind: "incident" })}>Open incident</Button>
          </div>
        }
      >
        {incidents.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No incidents recorded for this event.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {incidents.map((inc) => (
              <li key={inc.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                <span className="text-slate-700 dark:text-slate-200">{inc.affected_service || label(inc.cause_domain)} · {inc.severity.toUpperCase()}</span>
                <Badge status="warning">{label(inc.review_state)}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {modal?.kind === "quote" && (
        <NewQuoteModal
          catalogVersions={catalogVersions} onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/quotes`, body), "Quote created")}
        />
      )}
      {modal?.kind === "order" && (
        <NewOrderModal
          catalogVersions={catalogVersions} serviceProfiles={serviceProfiles} cancellationPolicies={cancellationPolicies} quotes={quotes}
          onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/orders`, body), "Order created")}
        />
      )}
      {modal?.kind === "line" && order && (
        <AddLineModal
          catalogLines={selectedCatalogVersion?.lines || []} onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/orders/${order.id}/lines`, body), "Line added")}
        />
      )}
      {modal?.kind === "capacity" && (
        <CapacityHoldModal
          onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/capacity/hold`, body), "Capacity soft-held")}
        />
      )}
      {modal?.kind === "schedule" && order && (
        <PaymentScheduleModal
          onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/orders/${order.id}/payment-schedule`, body), "Milestone added")}
        />
      )}
      {modal?.kind === "readiness" && (
        <ReadinessCheckModal
          requiredChecks={readiness.required_checks} onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/readiness/checks`, body), "Readiness check recorded")}
        />
      )}
      {modal?.kind === "incident" && (
        <IncidentModal
          orders={acceptedOrders} onClose={close}
          onCreate={(body) => act(() => api.post(`/commercial/events/${eventId}/incidents`, body), "Incident opened")}
        />
      )}
      {modal?.kind === "remedy" && (
        <RemedyModal
          orders={acceptedOrders} incidents={incidents} onClose={close}
          onCreate={({ incident_id, ...rest }) =>
            act(() => api.post(`/commercial/incidents/${incident_id}/remedy`, rest), "Remedy proposed — pending a different approver")
          }
        />
      )}
    </div>
  );
}
