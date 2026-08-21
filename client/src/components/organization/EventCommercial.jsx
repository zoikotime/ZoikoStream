import { useEffect, useRef, useState } from "react";
import {
  FiCreditCard, FiFileText, FiPackage, FiCheckCircle, FiXCircle, FiAlertTriangle,
  FiClock, FiShield, FiActivity, FiDollarSign,
} from "react-icons/fi";
import api from "../../api";
import { cx } from "../../ui/tokens";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import SectionCard from "../admin/SectionCard";
import StatCard from "../admin/StatCard";
import EmptyState from "./OrganizationEmptyState";
import ErrorState from "./OrganizationErrorState";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import Skeleton from "../../ui/Skeleton";
import { Textarea, Label } from "../../ui/forms";
import { fmtDateTime } from "../../data/events";
import { money } from "../../utils/money";

// The commercial/billing layer (ZST-LE-COM-001) is a completely separate ledger from the
// platform subscription shown on /organization/billing — see models/commercial.py's module
// docstring. This tab is per-EVENT (an EventOrder belongs to one Event), which is why it
// lives here rather than as its own top-level page.
//
// Every write action here is scoped to what an org_admin is actually allowed to do
// (routers/commercial.py's RBAC mapping): accept a quote, accept an order, pay, request
// cancellation. Building the catalog, constructing orders, and approving refunds are
// Zoiko-staff actions with no UI yet — this tab only ever shows what your org's own team
// can act on.

// No currency default — see utils/money. Shared with the super-admin commerce console so the
// two cannot drift apart again (the admin copy kept a "USD" fallback after this one lost it).

const statusLabel = (s) => (s || "—").split("_").map((w) => w[0]?.toUpperCase() + w.slice(1)).join(" ");

const ORDER_TONE = {
  draft: "neutral", pending_acceptance: "warning", accepted: "info",
  active: "success", completed: "success", canceled: "danger", terminated: "danger",
};
const QUOTE_TONE = {
  draft: "neutral", issued: "warning", accepted: "success",
  expired: "danger", withdrawn: "danger", superseded: "neutral",
};
const PAYMENT_TONE = {
  requires_action: "warning", pending: "warning", partially_paid: "warning", paid: "success",
  failed: "danger", refunded: "neutral", part_refunded: "neutral", reversed: "danger",
  disputed: "danger", unmatched: "warning",
};
const READINESS_TONE = { pass: "success", conditional_pass: "warning", fail: "danger", in_progress: "warning", not_started: "neutral" };

// A 404 here means "nothing exists yet", not an error — every commercial sub-resource is
// optional until Zoiko staff creates a quote/order for this event.
const orNull = (p) => p.catch((e) => (e?.response?.status === 404 ? null : Promise.reject(e)));

function useCommercialData(eventId) {
  return useApi(async () => {
    const [quotes, order, capacity, readiness, incidents] = await Promise.all([
      api.get(`/commercial/events/${eventId}/quotes`).then((r) => r.data),
      orNull(api.get(`/commercial/events/${eventId}/orders/current`).then((r) => r.data)),
      api.get(`/commercial/events/${eventId}/capacity`).then((r) => r.data),
      api.get(`/commercial/events/${eventId}/readiness`).then((r) => r.data),
      api.get(`/commercial/events/${eventId}/incidents`).then((r) => r.data),
    ]);
    let schedule = [], payments = [], invoices = [];
    if (order) {
      [schedule, payments, invoices] = await Promise.all([
        api.get(`/commercial/orders/${order.id}/payment-schedule`).then((r) => r.data),
        api.get(`/commercial/orders/${order.id}/payments`).then((r) => r.data),
        api.get(`/commercial/orders/${order.id}/invoices`).then((r) => r.data),
      ]);
    }
    return { quotes, order, capacity, readiness, incidents, schedule, payments, invoices };
  });
}

// ── Cancel-order dialog: reason is required, the refund/charge is policy-computed server
// side, never invented client side. ──────────────────────────────────────────────────────
function CancelOrderDialog({ onClose, onConfirm, busy }) {
  const [reason, setReason] = useState("");
  return (
    <Modal
      open
      onClose={busy ? () => {} : onClose}
      title="Request cancellation"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Never mind</Button>
          <Button variant="danger" size="sm" loading={busy} disabled={busy || !reason.trim()} onClick={() => onConfirm(reason.trim())}>
            Cancel this order
          </Button>
        </>
      }
    >
      <p>
        Any refund is calculated automatically from the cancellation policy attached to this
        order, based on how much notice you're giving — no amount is decided here. This
        cannot be undone.
      </p>
      <div className="mt-4">
        <Label variant="console" htmlFor="zk-cancel-reason">Reason</Label>
        <Textarea
          id="zk-cancel-reason" variant="console" rows={3} value={reason}
          onChange={(e) => setReason(e.target.value)} placeholder="Why are you canceling?"
        />
      </div>
    </Modal>
  );
}

// ── Pay dialog (Phase 4C): Stripe-hosted Checkout ──────────────────────────────────────
// There is deliberately NO amount input. The payer chooses WHETHER to pay, never HOW MUCH:
// the outstanding balance comes from the backend (crud.order_payable_amount, which reads the
// published CatalogVersion -> order line -> tax determination chain) and is rendered here
// read-only. A client-supplied amount is exactly what the commercial standard forbids.
//
// Card details are never entered in this app. Confirming redirects the browser to Stripe's
// own hosted page, so no payment credential touches our origin or this bundle.
function PayDialog({ onClose, onConfirm, busy, outstanding, currency }) {
  return (
    <Modal
      open
      onClose={busy ? () => {} : onClose}
      title="Pay this order"
      size="sm"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          {/* disabled while busy is UX only — the backend/provider idempotency key is what
              actually prevents a duplicate payment from a double click. */}
          <Button size="sm" leftIcon={FiCreditCard} loading={busy} disabled={busy}
            onClick={onConfirm}>
            {busy ? "Opening secure checkout…" : `Pay ${money(outstanding, currency)}`}
          </Button>
        </>
      }
    >
      <dl className="space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <dt className="text-slate-500 dark:text-slate-400">Amount due</dt>
          {/* Read-only: this figure is the backend's, not an editable field. */}
          <dd className="font-semibold text-slate-900 dark:text-white" data-testid="pay-amount">
            {money(outstanding, currency)}
          </dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-slate-500 dark:text-slate-400">Currency</dt>
          <dd className="font-medium text-slate-700 dark:text-slate-200" data-testid="pay-currency">
            {currency}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
        You'll be taken to our payment provider's secure page to enter your card details.
        Card information is never entered on or stored by ZoikoStream.
      </p>
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
        Payment is confirmed by our provider after you complete checkout — this order will
        update once that confirmation arrives.
      </p>
    </Modal>
  );
}

function Row({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className="text-right font-medium text-slate-800 dark:text-slate-100">{children}</span>
    </div>
  );
}

// Checkout return outcomes we surface. Deliberately does NOT include a "paid" outcome:
// returning from the provider says the payer finished the page, not that money moved. The
// authoritative state arrives via webhook -> provider_events -> payment state machine, so we
// re-fetch and show whatever the backend says.
const CHECKOUT_RETURN = {
  success: {
    tone: "info",
    text: "Payment submitted. We're waiting for confirmation from our payment provider — " +
          "this order will update automatically once it arrives.",
  },
  cancelled: {
    tone: "warning",
    text: "Checkout was cancelled. No payment was taken and nothing has changed.",
  },
};

/** Reads ?checkout= from the URL ONCE on mount, then strips it so a refresh or a shared link
 *  cannot re-assert an outcome. The value only chooses a message — it never decides payment
 *  state.
 *
 *  Runs exactly once, guarded by a ref. The callback is held in a ref rather than declared as
 *  a dependency: useApi's `reload` is a fresh closure on every render, so depending on it
 *  would re-run this effect each render, and since it CALLS reload() that is an infinite
 *  render loop (it exhausted V8 the first time round). Same latest-ref pattern useApi uses
 *  internally. */
function useCheckoutReturn(onReturn) {
  // Read the URL SYNCHRONOUSLY during the first render rather than setting state from an
  // effect: the value never changes for the life of this mount, so an effect + setState would
  // just be an extra render (and is what react-hooks/set-state-in-effect correctly objects to).
  const [outcome] = useState(() => {
    const value = new URLSearchParams(window.location.search).get("checkout");
    return CHECKOUT_RETURN[value] ? value : null;
  });
  const onReturnRef = useRef(onReturn);
  const handled = useRef(false);

  // Keep the latest callback without depending on it: useApi's `reload` is a fresh closure
  // every render, so listing it as a dependency would re-run the effect each render — and
  // since the effect CALLS it, that is an infinite render loop.
  useEffect(() => {
    onReturnRef.current = onReturn;
  });

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;
    const params = new URLSearchParams(window.location.search);
    const returned = params.has("checkout");
    if (!returned && !params.has("session_id")) return;
    // Strip the return markers so a refresh or a shared link cannot re-assert an outcome.
    // session_id goes too: we never trust a provider-supplied identifier, and leaving one in
    // the address bar invites pasting another org's reference (a session id is not
    // authorization).
    params.delete("checkout");
    params.delete("session_id");
    const qs = params.toString();
    window.history.replaceState(
      {}, "", window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash);
    // Re-fetch order/payment/invoice/readiness — the redirect itself is not evidence of payment.
    if (returned) onReturnRef.current?.();
  }, []);

  return outcome;
}


export default function EventCommercial({ event }) {
  const eventId = event.id;
  const { data, loading, error, reload } = useCommercialData(eventId);
  const mutate = useMutation({ onDone: reload });

  const [cancelOpen, setCancelOpen] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const checkoutOutcome = useCheckoutReturn(reload);

  if (loading) {
    return (
      <div className="space-y-4" aria-hidden="true">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-32 w-full rounded-xl" />)}
      </div>
    );
  }
  if (error) return <ErrorState error={error} onRetry={reload} title="Couldn't load billing details" />;

  const { quotes, order, capacity, readiness, incidents, schedule, payments, invoices } = data;

  const acceptQuote = (quote) =>
    mutate.run(() => api.post(`/commercial/events/${eventId}/quotes/${quote.id}/accept`), {
      success: "Quote accepted",
    });

  const acceptOrder = () =>
    mutate.run(() => api.post(`/commercial/events/${eventId}/orders/${order.id}/accept`, {}), {
      success: "Order accepted",
    });

  const cancelOrder = async (reason) => {
    const res = await mutate.run(
      () => api.post(`/commercial/events/${eventId}/orders/${order.id}/cancel`, { reason }),
      {
        success: (r) =>
          r.data.refund_amount > 0
            ? `Order canceled — ${money(r.data.refund_amount, order.currency)} refund initiated`
            : "Order canceled",
      }
    );
    if (res) setCancelOpen(false);
  };

  const capturedAmount = payments
    .filter((p) => ["paid", "part_refunded"].includes(p.state))
    .reduce((sum, p) => sum + parseFloat(p.amount), 0);
  const outstanding = order ? Math.max(0, parseFloat(order.total_amount) - capturedAmount) : 0;

  // Start hosted Checkout. The request body carries NO financial fields — the backend reads
  // the authoritative amount/currency from the order itself. On success we hand the browser
  // to the provider; we never mark anything paid here.
  const payNow = async () => {
    const res = await mutate.run(
      () => api.post(`/commercial/orders/${order.id}/payments/checkout-session`, {
        provider_name: "stripe",
      }),
      { success: null }
    );
    const url = res?.data?.checkout_url;
    if (url) {
      window.location.assign(url);   // leaves the SPA; nothing after this runs
      return;
    }
    setPayOpen(false);
  };

  return (
    <div className="space-y-6">
      {checkoutOutcome && (
        <div
          role="status"
          aria-live="polite"
          data-testid="checkout-return"
          className={cx(
            "rounded-xl border px-4 py-3 text-sm",
            checkoutOutcome === "cancelled"
              ? "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300"
              : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-500/20 dark:bg-sky-500/10 dark:text-sky-300"
          )}
        >
          {CHECKOUT_RETURN[checkoutOutcome].text}
        </div>
      )}

      {/* Summary */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="Order status" value={order ? statusLabel(order.status) : "No order"} />
        <StatCard label="Order total" value={order ? money(order.total_amount, order.currency) : "—"} />
        <StatCard label="Risk tier" value={order ? order.risk_tier.toUpperCase() : "—"} />
        <StatCard
          label="Readiness"
          value={readiness.ready ? "Ready" : readiness.blocking_reasons.length ? `${readiness.blocking_reasons.length} blocking` : "Not started"}
        />
      </div>

      {!order && quotes.length === 0 && (
        <SectionCard title="No commercial order yet" icon={FiPackage}>
          <EmptyState
            icon={FiPackage}
            title="Nothing to show yet"
            description="This event has no quote or order. Your Zoiko contact sets this up from the commercial catalog — once a quote is issued, it will appear here for you to accept."
          />
        </SectionCard>
      )}

      {/* Quotes */}
      {quotes.length > 0 && (
        <SectionCard title="Quotes" subtitle="Accept a quote to create a binding order" icon={FiFileText} padding="none">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {quotes.map((q) => (
              <li key={q.id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="font-medium text-slate-800 dark:text-slate-100">v{q.version} · {money(q.amount, q.currency)}</p>
                    <Badge tone={QUOTE_TONE[q.status] || "neutral"} size="sm">{statusLabel(q.status)}</Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                    {q.valid_until ? `Valid until ${fmtDateTime(q.valid_until)}` : "No expiry set"}
                    {q.commercial_notes ? ` · ${q.commercial_notes}` : ""}
                  </p>
                </div>
                {q.status === "issued" && (
                  <Button size="sm" leftIcon={FiCheckCircle} loading={mutate.busy} disabled={mutate.busy} onClick={() => acceptQuote(q)}>
                    Accept quote
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Order + lines */}
      {order && (
        <SectionCard
          title="Order"
          subtitle={`Version ${order.order_version} · ${order.billing_classification}`}
          icon={FiPackage}
          action={
            <div className="flex gap-2">
              {order.status === "pending_acceptance" && (
                <Button size="sm" leftIcon={FiCheckCircle} loading={mutate.busy} disabled={mutate.busy} onClick={acceptOrder}>
                  Accept order
                </Button>
              )}
              {["accepted", "active"].includes(order.status) && (
                <Button variant="danger" size="sm" leftIcon={FiXCircle} onClick={() => setCancelOpen(true)}>
                  Request cancellation
                </Button>
              )}
            </div>
          }
        >
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            <Row label="Status"><Badge tone={ORDER_TONE[order.status] || "neutral"}>{statusLabel(order.status)}</Badge></Row>
            <Row label="Subtotal">{money(order.subtotal, order.currency)}</Row>
            <Row label="Tax">{money(order.tax_amount, order.currency)}</Row>
            <Row label="Total">{money(order.total_amount, order.currency)}</Row>
            {order.accepted_at && <Row label="Accepted">{fmtDateTime(order.accepted_at)}</Row>}
          </div>
          {order.lines.length > 0 && (
            <div className="mt-4 overflow-x-auto rounded-lg border border-slate-100 dark:border-slate-800">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400 dark:bg-slate-800/50">
                  <tr>
                    <th className="px-3 py-2 font-medium">Line</th>
                    <th className="px-3 py-2 font-medium">Qty</th>
                    <th className="px-3 py-2 text-right font-medium">Unit price</th>
                    <th className="px-3 py-2 text-right font-medium">Total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {order.lines.map((l) => (
                    <tr key={l.id}>
                      <td className="px-3 py-2">
                        {l.description || l.service_code}
                        {l.is_complimentary && <Badge tone="brand" size="sm" className="ml-2">Complimentary</Badge>}
                        {l.is_addon && !l.is_complimentary && <Badge tone="neutral" size="sm" className="ml-2">Add-on</Badge>}
                      </td>
                      <td className="px-3 py-2">{l.quantity}</td>
                      <td className="px-3 py-2 text-right">{money(l.unit_price, order.currency)}</td>
                      <td className="px-3 py-2 text-right font-medium">{money(l.line_total, order.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      )}

      {/* Payment schedule + payments */}
      {order && (
        <SectionCard
          title="Payments"
          subtitle={outstanding > 0 ? `${money(outstanding, order.currency)} outstanding` : "Fully authorized"}
          icon={FiDollarSign}
          action={
            outstanding > 0 && (
              <Button size="sm" leftIcon={FiCreditCard} onClick={() => setPayOpen(true)} data-testid="pay-button">Pay with card</Button>
            )
          }
        >
          {schedule.length > 0 && (
            <div className="mb-4 space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Schedule</p>
              {schedule.map((s) => (
                <div key={s.id} className="flex items-center justify-between rounded-lg border border-slate-100 px-3 py-2 text-sm dark:border-slate-800">
                  <span className="text-slate-600 dark:text-slate-300">
                    {statusLabel(s.milestone)}{s.due_at ? ` · due ${fmtDateTime(s.due_at)}` : ""}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-slate-800 dark:text-slate-100">{money(s.amount, order.currency)}</span>
                    <Badge tone={s.status === "financial_hold" ? "danger" : s.status === "satisfied" ? "success" : "warning"} size="sm">
                      {statusLabel(s.status)}
                    </Badge>
                  </span>
                </div>
              ))}
            </div>
          )}
          {payments.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">No payments recorded yet.</p>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {payments.map((p) => (
                <li key={p.id} className="flex items-center justify-between py-2.5 text-sm">
                  <span className="text-slate-600 dark:text-slate-300">
                    {p.authorized_at ? fmtDateTime(p.authorized_at) : "—"} · {p.provider}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-slate-800 dark:text-slate-100">{money(p.amount, p.currency)}</span>
                    <Badge tone={PAYMENT_TONE[p.state] || "neutral"} size="sm">{statusLabel(p.state)}</Badge>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}

      {/* Invoices */}
      {order && invoices.length > 0 && (
        <SectionCard title="Invoices" icon={FiFileText} padding="none">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {invoices.map((inv) => (
              <li key={inv.id} className="flex items-center justify-between px-5 py-3 text-sm">
                <div>
                  <p className="font-medium text-slate-800 dark:text-slate-100">{inv.number}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {inv.issue_date ? `Issued ${fmtDateTime(inv.issue_date)}` : "Not yet issued"}
                  </p>
                </div>
                <span className="flex items-center gap-2">
                  <span className="font-medium text-slate-800 dark:text-slate-100">{money(inv.total_amount, inv.currency)}</span>
                  <Badge tone={inv.state === "paid" ? "success" : inv.state === "void" ? "danger" : "info"} size="sm">
                    {statusLabel(inv.state)}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Capacity */}
      {capacity.length > 0 && (
        <SectionCard title="Capacity" subtitle="Production resources reserved for this event" icon={FiShield} padding="none">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {capacity.map((c) => (
              <li key={c.id} className="flex items-center justify-between px-5 py-3 text-sm">
                <span className="text-slate-700 dark:text-slate-200">{statusLabel(c.resource_type)}</span>
                <Badge tone={c.state === "hard_reserved" ? "success" : c.state === "released" || c.state === "expired" ? "neutral" : "warning"} size="sm">
                  {statusLabel(c.state)}
                </Badge>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Readiness detail */}
      {(order || readiness.required_checks.length > 0) && (
        <SectionCard
          title="Readiness"
          subtitle={readiness.ready ? "All required gates pass" : "Blocking reasons below"}
          icon={readiness.ready ? FiCheckCircle : FiAlertTriangle}
          accent={readiness.ready ? "emerald" : "amber"}
        >
          {readiness.ready ? (
            <p className="text-sm text-emerald-600 dark:text-emerald-400">This event is READY.</p>
          ) : readiness.blocking_reasons.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">No readiness requirements apply to this event.</p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {readiness.blocking_reasons.map((r, i) => (
                <li key={i} className="flex items-start gap-2 text-amber-600 dark:text-amber-400">
                  <FiClock className="mt-0.5 shrink-0" /> {r}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}

      {/* Incidents */}
      {incidents.length > 0 && (
        <SectionCard title="Incidents" icon={FiActivity} padding="none">
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {incidents.map((inc) => (
              <li key={inc.id} className="flex items-center justify-between px-5 py-3 text-sm">
                <div>
                  <p className="font-medium text-slate-800 dark:text-slate-100">{inc.affected_service || statusLabel(inc.cause_domain)}</p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{fmtDateTime(inc.opened_at)}</p>
                </div>
                <Badge tone={READINESS_TONE[inc.review_state] || "warning"} size="sm">{statusLabel(inc.review_state)}</Badge>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {cancelOpen && (
        <CancelOrderDialog onClose={() => setCancelOpen(false)} onConfirm={cancelOrder} busy={mutate.busy} />
      )}
      {payOpen && order && (
        <PayDialog
          onClose={() => setPayOpen(false)} onConfirm={payNow} busy={mutate.busy}
          outstanding={outstanding} currency={order.currency}
        />
      )}
    </div>
  );
}
