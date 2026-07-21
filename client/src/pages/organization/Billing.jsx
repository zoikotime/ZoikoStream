// client/src/pages/organization/Billing.jsx
// Billing — subscription, usage, payment methods, invoices. Route: /organization/billing
// Rendered inside OrganizationLayout. No backend: plan/status/payment changes run on
// local state; invoice download builds a text file client-side.
import { useMemo, useState } from "react";
import {
  FiClock, FiHardDrive, FiUsers, FiCalendar, FiCreditCard, FiPlus, FiCheck,
  FiTrendingUp, FiDownload, FiTrash2, FiStar,
} from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import { notify } from "../../ui/Toast";
import { fmtDate } from "../../data/events";
import {
  plans, getPlan, currentPlanId, renewalDate, usage, paymentMethodsSeed, invoices, fmtGB,
} from "../../data/billing";

const INVOICE_TONE = { Paid: "success", Pending: "warning", Failed: "error" };

function UsageMeter({ icon: Icon, label, used, limit, accent, format }) {
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const near = pct >= 90;
  const fmt = format || ((v) => v.toLocaleString());
  return (
    <Card padding="md">
      <div className="flex items-center gap-3">
        <span className={cx("grid h-10 w-10 shrink-0 place-items-center rounded-xl", ACCENT[accent].chip)}>
          <Icon className="text-lg" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
          <p className="text-lg font-bold text-slate-900 dark:text-white">
            {fmt(used)} <span className="text-sm font-medium text-slate-400">/ {fmt(limit)}</span>
          </p>
        </div>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className={cx("h-full rounded-full transition-all", near ? "bg-rose-500" : ACCENT[accent].solid)} style={{ width: `${pct}%` }} />
      </div>
      <p className={cx("mt-1.5 text-xs", near ? "text-rose-500" : "text-slate-400")}>{pct}% used</p>
    </Card>
  );
}

export default function OrganizationBilling() {
  const [planId, setPlanId] = useState(currentPlanId);
  const [status, setStatus] = useState("Active"); // Active | Canceled
  const [methods, setMethods] = useState(paymentMethodsSeed);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);

  const plan = useMemo(() => getPlan(planId), [planId]);
  const canceled = status === "Canceled";

  const meters = [
    { icon: FiClock, label: "Streaming Hours", used: usage.streamingHours, limit: plan.limits.streamingHours, accent: "violet", format: (v) => `${v.toLocaleString()} hrs` },
    { icon: FiHardDrive, label: "Storage Used", used: usage.storageGB, limit: plan.limits.storageGB, accent: "blue", format: fmtGB },
    { icon: FiUsers, label: "Active Users", used: usage.users, limit: plan.limits.users, accent: "emerald" },
    { icon: FiCalendar, label: "Events Hosted", used: usage.events, limit: plan.limits.events, accent: "amber" },
  ];

  const choosePlan = (p) => {
    setPlanId(p.id);
    setStatus("Active");
    setUpgradeOpen(false);
    notify.success(`You're now on the ${p.name} plan`);
  };
  const confirmCancel = () => {
    setStatus("Canceled");
    setCancelOpen(false);
    notify.success("Subscription canceled — active until the renewal date");
  };
  const reactivate = () => {
    setStatus("Active");
    notify.success("Subscription reactivated");
  };

  const setDefaultMethod = (id) => {
    setMethods((list) => list.map((m) => ({ ...m, default: m.id === id })));
    notify.success("Default payment method updated");
  };
  const removeMethod = (id) => {
    const m = methods.find((x) => x.id === id);
    if (m?.default) return notify.error("Can't remove the default card — set another as default first");
    setMethods((list) => list.filter((x) => x.id !== id));
    notify.success("Payment method removed");
  };

  const downloadInvoice = (inv) => {
    const body = [
      "ZoikoStream",
      "-----------------------------",
      `Invoice:      ${inv.id}`,
      `Date:         ${fmtDate(inv.date)}`,
      `Description:  ${inv.description}`,
      `Amount:       $${inv.amount.toFixed(2)}`,
      `Status:       ${inv.status}`,
    ].join("\n");
    const url = URL.createObjectURL(new Blob([body], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${inv.id}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    notify.success(`Downloaded ${inv.id}`);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Billing</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Manage your subscription, usage, and payment details</p>
      </div>

      {/* Current subscription */}
      <Card padding="lg" className="relative overflow-hidden">
        <span className="absolute inset-y-0 left-0 w-1.5 bg-gradient-to-b from-violet-600 to-fuchsia-500" />
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-bold text-slate-900 dark:text-white">{plan.name} Plan</h2>
              <Badge status={canceled ? "error" : "active"} dot>{canceled ? "Canceled" : "Active"}</Badge>
            </div>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{plan.blurb}</p>
            <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
              <span>
                <span className="text-3xl font-bold text-slate-900 dark:text-white">${plan.price}</span>
                <span className="text-sm text-slate-400"> /month</span>
              </span>
              <span className="inline-flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                <FiCalendar /> {canceled ? "Access ends" : "Renews"} on {fmtDate(renewalDate)}
              </span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <Button onClick={() => setUpgradeOpen(true)}>
              <FiTrendingUp className="text-base" /> {canceled ? "Change Plan" : "Upgrade Plan"}
            </Button>
            {canceled ? (
              <Button variant="secondary" onClick={reactivate}>Reactivate</Button>
            ) : (
              <button
                onClick={() => setCancelOpen(true)}
                className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-rose-600 transition hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10"
              >
                Cancel Subscription
              </button>
            )}
          </div>
        </div>
        {canceled && (
          <div className="mt-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
            Your subscription won't renew. You'll keep {plan.name} features until {fmtDate(renewalDate)}.
          </div>
        )}
      </Card>

      {/* Usage */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Usage this cycle</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {meters.map((m) => (
            <UsageMeter key={m.label} {...m} />
          ))}
        </div>
      </div>

      {/* Payment methods */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Payment Methods</h2>
          <Button variant="secondary" size="sm" onClick={() => notify.info("Add a card — coming soon")}>
            <FiPlus className="text-base" /> Add Method
          </Button>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {methods.map((m) => (
            <Card key={m.id} padding="md" className="flex flex-col justify-between">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-14 place-items-center rounded-lg bg-gradient-to-br from-slate-800 to-slate-600 text-white dark:from-slate-700 dark:to-slate-900">
                    <FiCreditCard />
                  </span>
                  <div>
                    <p className="font-medium text-slate-800 dark:text-slate-100">{m.brand} •••• {m.last4}</p>
                    <p className="text-xs text-slate-400">Expires {m.exp} · {m.name}</p>
                  </div>
                </div>
                {m.default && <Badge status="info">Default</Badge>}
              </div>
              <div className="mt-4 flex items-center gap-1.5">
                {!m.default && (
                  <button
                    onClick={() => setDefaultMethod(m.id)}
                    className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  >
                    <FiStar className="text-sm" /> Set default
                  </button>
                )}
                <button
                  onClick={() => removeMethod(m.id)}
                  className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1.5 text-xs font-medium text-slate-600 transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-rose-500/40 dark:hover:bg-rose-500/10 dark:hover:text-rose-400"
                >
                  <FiTrash2 className="text-sm" /> Remove
                </button>
              </div>
            </Card>
          ))}
        </div>
      </div>

      {/* Billing history / invoices */}
      <Card padding="none" className="overflow-hidden">
        <div className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-white">Billing History</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">Download invoices for your records</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px]">
            <thead className="border-b border-slate-100 dark:border-slate-800">
              <tr>
                {["Invoice", "Date", "Description", "Amount", "Status", ""].map((h, i) => (
                  <th key={i} className={cx("px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-400 whitespace-nowrap", (h === "Amount" || h === "") && "text-right")}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {invoices.map((inv) => (
                <tr key={inv.id} className="text-sm transition hover:bg-slate-50 dark:hover:bg-slate-800/50">
                  <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-800 dark:text-slate-100">{inv.id}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-300">{fmtDate(inv.date)}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-300">{inv.description}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right font-medium tabular-nums text-slate-800 dark:text-slate-100">${inv.amount.toFixed(2)}</td>
                  <td className="whitespace-nowrap px-4 py-3"><Badge status={INVOICE_TONE[inv.status]}>{inv.status}</Badge></td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">
                    <button
                      onClick={() => downloadInvoice(inv)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
                    >
                      <FiDownload /> Invoice
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Upgrade / change plan modal */}
      <Modal open={upgradeOpen} onClose={() => setUpgradeOpen(false)} title="Choose your plan" className="max-w-4xl">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {plans.map((p) => {
            const current = p.id === plan.id;
            return (
              <div
                key={p.id}
                className={cx(
                  "flex flex-col rounded-2xl border p-4",
                  current ? "border-violet-500 ring-1 ring-violet-500/30" : "border-slate-200 dark:border-slate-700"
                )}
              >
                <div className="flex items-center justify-between">
                  <p className="font-semibold text-slate-900 dark:text-white">{p.name}</p>
                  {current && <Badge status="info">Current</Badge>}
                </div>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{p.blurb}</p>
                <p className="mt-3">
                  <span className="text-2xl font-bold text-slate-900 dark:text-white">${p.price}</span>
                  <span className="text-sm text-slate-400"> /mo</span>
                </p>
                <ul className="mt-3 flex-1 space-y-1.5">
                  {p.features.map((f) => (
                    <li key={f} className="flex items-start gap-1.5 text-xs text-slate-600 dark:text-slate-300">
                      <FiCheck className="mt-0.5 shrink-0 text-emerald-500" /> {f}
                    </li>
                  ))}
                </ul>
                <Button
                  size="sm"
                  variant={current ? "secondary" : "primary"}
                  disabled={current}
                  className="mt-4 w-full"
                  onClick={() => choosePlan(p)}
                >
                  {current ? "Current plan" : p.price > plan.price ? "Upgrade" : "Switch"}
                </Button>
              </div>
            );
          })}
        </div>
      </Modal>

      {/* Cancel subscription modal */}
      <Modal
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Cancel subscription?"
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setCancelOpen(false)}>Keep subscription</Button>
            <Button variant="danger" size="sm" onClick={confirmCancel}>Cancel subscription</Button>
          </>
        }
      >
        <p>
          Your <strong className="text-slate-800 dark:text-slate-100">{plan.name}</strong> plan will stay active until{" "}
          <strong className="text-slate-800 dark:text-slate-100">{fmtDate(renewalDate)}</strong>. After that it won't
          renew, and hosting, recordings, and analytics will be paused.
        </p>
      </Modal>
    </div>
  );
}
