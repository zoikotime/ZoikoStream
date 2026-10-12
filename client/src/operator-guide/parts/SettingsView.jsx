// Guide governance for organization admins (§6.17): availability, capability switches, the
// fixed controls, and the Guide audit trail. The server enforces every setting; this view
// only reflects and edits them.
import { useEffect, useState } from "react";
import { Lock } from "lucide-react";
import { guideErrorMessage, opGuide } from "../operatorGuideApi";

function Toggle({ id, label, checked, disabled, onChange }) {
  return (
    <label className="og-toggle" htmlFor={id}>
      <span>{label}</span>
      <input id={id} type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function AuditList() {
  const [page, setPage] = useState({ items: [], next_before_id: null });
  const [error, setError] = useState(null);
  const load = (before) => opGuide.audit(before)
    .then((data) => setPage((p) => ({ items: before ? [...p.items, ...data.items] : data.items, next_before_id: data.next_before_id })))
    .catch((e) => setError(guideErrorMessage(e)));
  useEffect(() => {
    load(null);
  }, []);
  return (
    <section aria-labelledby="og-audit-h">
      <h3 id="og-audit-h" className="og-section-title">Guide activity</h3>
      {error && <p className="og-error og-small" role="alert">{error}</p>}
      <table className="og-table">
        <caption className="og-sr">Guide audit events, newest first</caption>
        <thead><tr><th scope="col">When</th><th scope="col">What</th><th scope="col">Outcome</th></tr></thead>
        <tbody>
          {page.items.map((r) => (
            <tr key={r.id}>
              <td>{new Date(r.at).toLocaleString()}</td>
              <td>{r.kind} · {r.intent || "—"}<span className="og-row-detail">{r.role}</span></td>
              <td>{r.outcome}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {page.next_before_id && (
        <button type="button" className="og-btn" onClick={() => load(page.next_before_id)}>Load more</button>
      )}
      <p className="og-fineprint">The audit trail records what Guide did and why — never conversation text.</p>
    </section>
  );
}

export default function SettingsView({ onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    opGuide.settings().then(setData).catch((e) => setError(guideErrorMessage(e)));
  }, []);

  const save = async (body) => {
    setSaving(true);
    setError(null);
    try {
      setData(await opGuide.saveSettings(body));
      onChanged?.();
    } catch (e) {
      setError(guideErrorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  if (!data) return <div className="og-pane">{error ? <p className="og-error" role="alert">{error}</p> : <p className="og-muted">Loading settings…</p>}</div>;
  const editable = data.editable && !saving;
  return (
    <div className="og-pane">
      <section aria-labelledby="og-gov-h">
        <h3 id="og-gov-h" className="og-section-title">Guide for your organization</h3>
        {error && <p className="og-error og-small" role="alert">{error}</p>}
        <Toggle id="og-enabled" label="Guide enabled" checked={data.enabled} disabled={!editable} onChange={(v) => save({ enabled: v })} />
        <fieldset className="og-fieldset" disabled={!editable || !data.enabled}>
          <legend className="og-small og-muted">Capabilities</legend>
          {data.capabilities.map((c) => (
            <Toggle key={c.key} id={`og-cap-${c.key}`} label={c.label} checked={c.enabled} disabled={!editable || !data.enabled}
              onChange={(v) => save({ capabilities: { [c.key]: v } })} />
          ))}
        </fieldset>
        <ul className="og-fixed">
          {data.governance.map((g) => (
            <li key={g.key}><Lock size={14} aria-hidden="true" /> {g.label}: <strong>{g.value}</strong></li>
          ))}
        </ul>
        {!data.editable && <p className="og-fineprint">Only an organization admin can change these settings.</p>}
      </section>
      {data.editable && <AuditList />}
    </div>
  );
}
