import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";

const STATUSES = ["trial", "active", "past_due", "cancelled"];
const statusLabel = (s) => s.split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// Edit an org's subscription: status, seats, plan, renewal date. No create mode — a
// subscription is created implicitly (org create with plan_slug, or org plan switch).
export default function SubscriptionModal({ open, onClose, subscription, plans = [], onSaved }) {
  // Mounted only while open and keyed by the subscription being edited (Subscriptions.jsx),
  // so this initial state IS the per-open reset. The `!subscription` fallback is kept: the
  // component still renders (and submit() no-ops) if it is ever mounted without one.
  const [form, setForm] = useState(() =>
    subscription
      ? {
          status: subscription.status || "trial",
          seats: subscription.seats ?? 1,
          plan_slug: (plans.find((p) => p.name === subscription.plan) || {}).slug || "",
          current_period_end: subscription.current_period_end
            ? subscription.current_period_end.slice(0, 10)
            : "",
        }
      : { status: "trial", seats: 1, plan_slug: "", current_period_end: "" }
  );
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!subscription) return;
    setSaving(true);
    try {
      await api.patch(`/admin/subscriptions/${subscription.id}`, {
        status: form.status,
        seats: Number(form.seats) || 1,
        plan_slug: form.plan_slug || null,
        current_period_end: form.current_period_end ? new Date(form.current_period_end).toISOString() : null,
      });
      notify.success(`${subscription.organization_name || "Subscription"} updated`);
      onSaved?.();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  if (!subscription) return null;

  return (
    <Modal
      open={open}
      onClose={close}
      title={`Edit Subscription — ${subscription.organization_name || ""}`}
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Save changes</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Status</Label>
            <Select variant="console" value={form.status} onChange={(e) => set("status", e.target.value)}>
              {STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
            </Select>
          </div>
          <div>
            <Label>Seats</Label>
            <Input variant="console" type="number" min={1} value={form.seats} onChange={(e) => set("seats", e.target.value)} />
          </div>
        </div>
        <div>
          <Label>Plan</Label>
          <Select variant="console" value={form.plan_slug} onChange={(e) => set("plan_slug", e.target.value)}>
            <option value="">Keep current plan</option>
            {plans.map((p) => (
              /* price_monthly is null until an approved price is published — don't render
                 "$null/mo" or imply the plan is free. */
              <option key={p.slug} value={p.slug}>
                {p.pricing_state === "PUBLISHED"
                  ? `${p.name} — $${p.price_monthly}/mo`
                  : p.pricing_state === "CUSTOM"
                    ? `${p.name} — custom pricing`
                    : `${p.name} — price not published`}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label>Current period end</Label>
          <Input variant="console" type="date" value={form.current_period_end} onChange={(e) => set("current_period_end", e.target.value)} />
        </div>
      </form>
    </Modal>
  );
}
