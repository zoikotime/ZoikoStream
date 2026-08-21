import { useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Textarea, Label, Select } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { VERTICALS } from "../../data/commerce";

export function CatalogVersionModal({ onClose, onSaved }) {
  const [vertical, setVertical] = useState(VERTICALS[0].value);
  const [versionLabel, setVersionLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!versionLabel.trim()) return notify.error("Version label is required");
    setSaving(true);
    try {
      await api.post("/commercial/catalog-versions", {
        vertical, version_label: versionLabel.trim(), notes: notes.trim() || null,
      });
      notify.success("Catalog version created as draft");
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
      title="New catalog version"
      size="md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button size="sm" onClick={submit} loading={saving}>Create draft</Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label variant="console">Vertical</Label>
          <Select variant="console" value={vertical} onChange={(e) => setVertical(e.target.value)}>
            {VERTICALS.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
          </Select>
        </div>
        <div>
          <Label variant="console">Version label</Label>
          <Input variant="console" value={versionLabel} onChange={(e) => setVersionLabel(e.target.value)} placeholder="e.g. 2026-Q1" />
        </div>
        <div>
          <Label variant="console">Notes</Label>
          <Textarea variant="console" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Internal notes about this version…" />
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Created as a draft. Add priced lines, then publish — an unpriced line blocks publishing.
        </p>
      </form>
    </Modal>
  );
}

const UNIT_BASES = ["per_event", "per_hour", "per_unit"];

export function CatalogVersionDrawer({ version, onClose, onChanged }) {
  const [lines, setLines] = useState(version.lines || []);
  // Currency starts EMPTY and is required. It used to default to "USD", which silently priced a
  // catalog line in dollars whenever nobody touched the field — and a catalog line's currency
  // propagates into every order and invoice built from it. The last-used code is carried over
  // between adds below, so pricing a whole version in one currency is still one keystroke each.
  const [form, setForm] = useState({ service_code: "", name: "", unit_price: "", currency: "", unit_basis: "per_event", is_addon: false });
  const [adding, setAdding] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const addLine = async (e) => {
    e.preventDefault();
    if (!form.service_code.trim() || !form.name.trim() || !form.unit_price) {
      return notify.error("Service code, name and unit price are required");
    }
    // A price without a currency is not a price. Refused here rather than defaulted, so the
    // line never reaches the catalog priced in a currency nobody chose.
    if (form.currency.trim().length !== 3) {
      return notify.error("A 3-letter currency code is required (e.g. USD, EUR, GBP)");
    }
    setAdding(true);
    try {
      const res = await api.post(`/commercial/catalog-versions/${version.id}/lines`, {
        service_code: form.service_code.trim(), name: form.name.trim(),
        unit_price: form.unit_price, currency: form.currency.trim().toUpperCase(),
        unit_basis: form.unit_basis, is_addon: form.is_addon,
      });
      setLines((ls) => [...ls, res.data]);
      setForm({ service_code: "", name: "", unit_price: "", currency: form.currency, unit_basis: "per_event", is_addon: false });
      notify.success("Line added");
      onChanged();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setAdding(false);
    }
  };

  const publish = async () => {
    setPublishing(true);
    try {
      await api.post(`/commercial/catalog-versions/${version.id}/publish`);
      notify.success("Catalog version published");
      onChanged();
      onClose();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setPublishing(false);
    }
  };

  const canPublish = version.status === "draft";

  return (
    <Modal
      open
      onClose={onClose}
      title={`${version.vertical} · ${version.version_label}`}
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Close</Button>
          {canPublish && (
            <Button size="sm" loading={publishing} onClick={publish}>Publish version</Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {lines.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No lines yet — add at least one priced line before publishing.</p>
        ) : (
          <div className="overflow-hidden rounded-lg border border-slate-100 dark:border-slate-800">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400 dark:bg-slate-800/50">
                <tr>
                  <th className="px-3 py-2 font-medium">Code</th>
                  <th className="px-3 py-2 font-medium">Name</th>
                  <th className="px-3 py-2 font-medium">Basis</th>
                  <th className="px-3 py-2 text-right font-medium">Price</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {lines.map((l) => (
                  <tr key={l.id}>
                    <td className="px-3 py-2 font-mono text-xs">{l.service_code}</td>
                    <td className="px-3 py-2">{l.name}{l.is_addon && <span className="ml-1.5 text-xs text-slate-400">(add-on)</span>}</td>
                    <td className="px-3 py-2 text-xs text-slate-500">{l.unit_basis}</td>
                    <td className="px-3 py-2 text-right">{l.unit_price} {l.currency}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {canPublish && (
          <form onSubmit={addLine} className="space-y-3 rounded-lg border border-dashed border-slate-200 p-4 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Add a line</p>
            <div className="grid grid-cols-2 gap-3">
              <Input variant="console" placeholder="service_code" value={form.service_code} onChange={(e) => set("service_code", e.target.value)} />
              <Input variant="console" placeholder="Display name" value={form.name} onChange={(e) => set("name", e.target.value)} />
              <Input variant="console" type="number" min="0" step="0.01" placeholder="Unit price" value={form.unit_price} onChange={(e) => set("unit_price", e.target.value)} />
              <Input variant="console" placeholder="Currency (3-letter code)" value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} maxLength={3} />
              <Select variant="console" value={form.unit_basis} onChange={(e) => set("unit_basis", e.target.value)}>
                {UNIT_BASES.map((b) => <option key={b} value={b}>{b}</option>)}
              </Select>
              <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
                <input type="checkbox" checked={form.is_addon} onChange={(e) => set("is_addon", e.target.checked)} className="h-4 w-4" />
                Add-on (not in base package)
              </label>
            </div>
            <Button type="submit" size="sm" loading={adding}>Add line</Button>
          </form>
        )}
      </div>
    </Modal>
  );
}
