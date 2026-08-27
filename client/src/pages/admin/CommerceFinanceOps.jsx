// Seller legal entities + capacity pools — the two registries that gate every invoice and
// every reservation, and had no UI at all until now (ZST-LE-COM-001 L1/P2, C4).
//
// Both back-ends deliberately ship EMPTY and fail closed: with no ACTIVE seller entity
// crud.resolve_seller_entity refuses to issue an invoice, and with no ACTIVE capacity pool
// covering the window crud.soft_hold_capacity refuses to hold. That is correct behaviour, but
// with no screen to populate them it meant the whole commercial path terminated at a 400 that
// only an API call could clear. These are those screens.
import { useState } from "react";
import {
  FiBriefcase, FiPlus, FiCheckCircle, FiCalendar, FiUsers, FiAlertTriangle,
} from "react-icons/fi";
import { Panel, Button, Badge, StatCard } from "../../components/admin";
import Modal from "../../ui/Modal";
import { ConsoleButton as ModalButton } from "../../ui/Button";
import { Input, Textarea, Label, Select } from "../../ui/forms";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { notify } from "../../ui/Toast";

const STATUS_TONE = {
  draft: "neutral", active: "success", published: "success",
  suspended: "warning", retired: "danger",
};

function StatusBadge({ status }) {
  return <Badge status={STATUS_TONE[status] || "neutral"}>{status}</Badge>;
}

// ── Seller legal entities (doc L1) ────────────────────────────────────────────────────────

// Currencies are ISO 4217 codes the operator types, not a curated list: the set a Zoiko
// entity can invoice in is a Finance fact, and hard-coding a menu here would be this file
// inventing commercial configuration. services/payments.currency_exponent validates the code
// on the way to the provider.
function SellerEntityModal({ onClose, onSaved }) {
  const [form, setForm] = useState({
    code: "", legal_name: "", country: "", default_currency: "",
    supported_currencies: "", tax_registration_id: "", tax_registration_country: "",
    invoice_number_prefix: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault();
    if (!form.code.trim() || !form.legal_name.trim()) {
      return notify.error("Code and legal name are required");
    }
    setSaving(true);
    try {
      await api.post("/commercial/seller-entities", {
        code: form.code.trim(),
        legal_name: form.legal_name.trim(),
        country: form.country.trim() || null,
        default_currency: form.default_currency.trim().toUpperCase() || null,
        supported_currencies: form.supported_currencies
          .split(",").map((c) => c.trim().toUpperCase()).filter(Boolean),
        tax_registration_id: form.tax_registration_id.trim() || null,
        tax_registration_country: form.tax_registration_country.trim() || null,
        invoice_number_prefix: form.invoice_number_prefix.trim() || null,
      });
      notify.success("Seller entity registered as draft");
      onSaved();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      title="Register seller legal entity"
      size="md"
      footer={
        <>
          <ModalButton variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</ModalButton>
          <ModalButton size="sm" onClick={submit} loading={saving}>Create draft</ModalButton>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
          Created as a draft. Activating it is a separate step, because an entity that appears on
          an issued invoice must have verified legal, tax and merchant identity first.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Code</Label>
            <Input variant="console" value={form.code} onChange={(e) => set("code", e.target.value)} placeholder="zoiko_tech_inc" />
          </div>
          <div>
            <Label variant="console">Country</Label>
            <Input variant="console" value={form.country} onChange={(e) => set("country", e.target.value)} placeholder="US" />
          </div>
        </div>
        <div>
          <Label variant="console">Registered legal name</Label>
          <Input variant="console" value={form.legal_name} onChange={(e) => set("legal_name", e.target.value)} placeholder="Zoiko Tech Inc." />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Default currency</Label>
            <Input variant="console" value={form.default_currency} onChange={(e) => set("default_currency", e.target.value)} placeholder="USD" maxLength={3} />
          </div>
          <div>
            <Label variant="console">Also supported</Label>
            <Input variant="console" value={form.supported_currencies} onChange={(e) => set("supported_currencies", e.target.value)} placeholder="GBP, EUR" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Tax registration ID</Label>
            <Input variant="console" value={form.tax_registration_id} onChange={(e) => set("tax_registration_id", e.target.value)} />
          </div>
          <div>
            <Label variant="console">Tax registration country</Label>
            <Input variant="console" value={form.tax_registration_country} onChange={(e) => set("tax_registration_country", e.target.value)} />
          </div>
        </div>
        <div>
          <Label variant="console">Invoice number prefix</Label>
          <Input variant="console" value={form.invoice_number_prefix} onChange={(e) => set("invoice_number_prefix", e.target.value)} placeholder="ZST-LE-INV" />
          <p className="mt-1 text-xs text-slate-400">
            Each entity keeps its own invoice series. Leave blank for the ZST-LE-INV default.
          </p>
        </div>
      </form>
    </Modal>
  );
}

export function SellerEntitiesTab() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/commercial/seller-entities").then((r) => r.data));
  const [modalOpen, setModalOpen] = useState(false);
  const rows = data || [];
  const active = rows.filter((r) => r.status === "active").length;

  const activate = async (entity) => {
    try {
      await api.post(`/commercial/seller-entities/${entity.id}/activate`);
      notify.success(`${entity.legal_name} activated`);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-4">
      {!loading && active === 0 && (
        <Panel static>
          <div className="flex items-start gap-3 py-1">
            <FiAlertTriangle className="mt-0.5 shrink-0 text-lg text-amber-500" />
            <div>
              <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No active seller entity</p>
              <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                No invoice can be issued until one is registered and activated, and assigned to
                the commercial account on the order.
              </p>
            </div>
          </div>
        </Panel>
      )}
      <div className="flex justify-end">
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModalOpen(true)}>Register entity</Button>
      </div>
      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load seller entities. {errMsg(error)}</p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiBriefcase className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No seller entities registered</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              This registry is the legal entity that invoices a purchaser. It ships empty on purpose.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && Array.from({ length: 2 }).map((_, i) => (
              <li key={i} className="px-5 py-4"><div className="zk-skeleton h-4 w-48 rounded bg-slate-200 dark:bg-slate-800" /></li>
            ))}
            {!loading && rows.map((entity) => (
              <li key={entity.id} className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-800 dark:text-slate-100">{entity.legal_name}</p>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">
                    {entity.code}
                    {entity.country ? ` · ${entity.country}` : ""}
                    {entity.default_currency ? ` · ${entity.default_currency}` : " · no currency set"}
                    {entity.invoice_number_prefix ? ` · ${entity.invoice_number_prefix}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={entity.status} />
                  {entity.status === "draft" && (
                    <Button variant="secondary" size="sm" leftIcon={FiCheckCircle} onClick={() => activate(entity)}>
                      Activate
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {modalOpen && <SellerEntityModal onClose={() => setModalOpen(false)} onSaved={reload} />}
    </div>
  );
}

// ── Capacity pools (doc C4) ───────────────────────────────────────────────────────────────

// Resource types are free text, matching the backend: a pool's resource_type is whatever
// Operations calls that resource, and the readiness gate matches on the same string the
// ServiceProfile requires (backup_contribution, dual_recording, reserved_capacity, ...).
const COMMON_RESOURCES = [
  "production_operator", "backup_contribution", "dual_recording",
  "reserved_capacity", "audience_capacity",
];

function CapacityPoolModal({ onClose, onSaved }) {
  const [form, setForm] = useState({
    resource_type: COMMON_RESOURCES[0], region: "", seller_legal_entity_id: "",
    window_start: "", window_end: "", total_capacity: "1", notes: "",
  });
  const [saving, setSaving] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e?.preventDefault();
    if (!form.window_start || !form.window_end) return notify.error("A start and end time are required");
    if (new Date(form.window_end) <= new Date(form.window_start)) {
      return notify.error("The window must end after it starts");
    }
    if (Number(form.total_capacity) < 0) return notify.error("Capacity cannot be negative");
    setSaving(true);
    try {
      await api.post("/commercial/capacity-pools", {
        resource_type: form.resource_type.trim(),
        region: form.region.trim() || null,
        seller_legal_entity_id: form.seller_legal_entity_id.trim() || null,
        window_start: new Date(form.window_start).toISOString(),
        window_end: new Date(form.window_end).toISOString(),
        total_capacity: Number(form.total_capacity),
        notes: form.notes.trim() || null,
      });
      notify.success("Capacity pool created as draft");
      onSaved();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={() => !saving && onClose()}
      title="New capacity pool"
      size="md"
      footer={
        <>
          <ModalButton variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</ModalButton>
          <ModalButton size="sm" onClick={submit} loading={saving}>Create draft</ModalButton>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
          A reservation needs an <strong>active</strong> pool whose window fully contains the
          requested window — an operator rostered 09:00–12:00 does not cover an 11:00–14:00
          event. Capacity is never oversold; requests with no headroom are refused.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Resource type</Label>
            <Select variant="console" value={form.resource_type} onChange={(e) => set("resource_type", e.target.value)}>
              {COMMON_RESOURCES.map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console">Total capacity</Label>
            <Input variant="console" type="number" min="0" step="1" value={form.total_capacity} onChange={(e) => set("total_capacity", e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Window start</Label>
            <Input variant="console" type="datetime-local" value={form.window_start} onChange={(e) => set("window_start", e.target.value)} />
          </div>
          <div>
            <Label variant="console">Window end</Label>
            <Input variant="console" type="datetime-local" value={form.window_end} onChange={(e) => set("window_end", e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label variant="console">Region</Label>
            <Input variant="console" value={form.region} onChange={(e) => set("region", e.target.value)} placeholder="optional" />
          </div>
          <div>
            <Label variant="console">Seller entity code</Label>
            <Input variant="console" value={form.seller_legal_entity_id} onChange={(e) => set("seller_legal_entity_id", e.target.value)} placeholder="optional" />
          </div>
        </div>
        <div>
          <Label variant="console">Notes</Label>
          <Textarea variant="console" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </div>
      </form>
    </Modal>
  );
}

function PoolUtilisation({ poolId }) {
  const { data, loading } = useApi(() =>
    api.get(`/commercial/capacity-pools/${poolId}/utilisation`).then((r) => r.data));
  if (loading || !data) return <span className="text-xs text-slate-400">…</span>;
  return (
    <span className="text-xs text-slate-500 dark:text-slate-400">
      {data.reserved_capacity}/{data.total_capacity} held · {data.available_capacity} free
    </span>
  );
}

export function CapacityPoolsTab() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/commercial/capacity-pools").then((r) => r.data));
  const [modalOpen, setModalOpen] = useState(false);
  const rows = data || [];
  const active = rows.filter((r) => r.status === "active").length;

  const activate = async (pool) => {
    try {
      await api.post(`/commercial/capacity-pools/${pool.id}/activate`);
      notify.success("Capacity pool activated");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const sweep = async () => {
    try {
      const r = await api.post("/commercial/maintenance/expire-holds");
      notify.success(r.data.expired
        ? `${r.data.expired} lapsed soft hold(s) released`
        : "No lapsed soft holds to release");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Pools" value={rows.length} />
        <StatCard label="Active" value={active} />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={sweep}>Release lapsed holds</Button>
        <Button leftIcon={FiPlus} size="sm" onClick={() => setModalOpen(true)}>New capacity pool</Button>
      </div>
      <Panel flush>
        {error ? (
          <p className="px-5 py-8 text-center text-sm text-rose-600 dark:text-rose-400">Couldn't load capacity pools. {errMsg(error)}</p>
        ) : !loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiUsers className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No capacity pools yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Until Operations publishes inventory, every capacity request fails closed — no
              capacity is ever assumed.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading && Array.from({ length: 2 }).map((_, i) => (
              <li key={i} className="px-5 py-4"><div className="zk-skeleton h-4 w-56 rounded bg-slate-200 dark:bg-slate-800" /></li>
            ))}
            {!loading && rows.map((pool) => (
              <li key={pool.id} className="flex items-center justify-between gap-3 px-5 py-4">
                <div className="min-w-0">
                  <p className="truncate font-medium text-slate-800 dark:text-slate-100">
                    {pool.resource_type}
                    {pool.region ? ` · ${pool.region}` : ""}
                  </p>
                  <p className="flex items-center gap-1.5 truncate text-xs text-slate-500 dark:text-slate-400">
                    <FiCalendar className="shrink-0" />
                    {new Date(pool.window_start).toLocaleString()} – {new Date(pool.window_end).toLocaleString()}
                  </p>
                  {pool.status === "active" && <PoolUtilisation poolId={pool.id} />}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={pool.status} />
                  {pool.status === "draft" && (
                    <Button variant="secondary" size="sm" leftIcon={FiCheckCircle} onClick={() => activate(pool)}>
                      Activate
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {modalOpen && <CapacityPoolModal onClose={() => setModalOpen(false)} onSaved={reload} />}
    </div>
  );
}
