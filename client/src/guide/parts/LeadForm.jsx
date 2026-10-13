import { useMemo, useRef, useState } from "react";
import { AlertTriangle, CalendarCheck, CheckCircle2, XCircle } from "lucide-react";
import * as api from "../guideApi";
import Actions from "./Actions";

// Demo / sales request (WF-06, Conversation Flow §8). The server supplies the purpose notice,
// the field set and the consent wording; this form renders them and reports exactly the
// outcome the server confirmed: confirmed | failed | unknown (PR-014, PR-027).

const COUNTRIES = (() => {
  try {
    const names = new Intl.DisplayNames(["en"], { type: "region" });
    const out = [];
    for (let a = 65; a <= 90; a += 1) {
      for (let b = 65; b <= 90; b += 1) {
        const code = String.fromCharCode(a, b);
        const name = names.of(code);
        if (name && name !== code) out.push([code, name]);
      }
    }
    return out.sort((x, y) => x[1].localeCompare(y[1]));
  } catch {
    return [["US", "United States"], ["GB", "United Kingdom"], ["IN", "India"], ["CA", "Canada"]];
  }
})();

function Field({ field, value, error, onChange, idPrefix }) {
  const id = `${idPrefix}-${field.field_id}`;
  const describedBy = [error ? `${id}-err` : null, field.hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  const common = {
    id,
    name: field.field_id,
    value: value ?? "",
    "aria-invalid": error ? true : undefined,
    "aria-describedby": describedBy,
    "aria-required": field.required || undefined,
    onChange: (e) => onChange(field.field_id, e.target.value),
  };
  let control;
  if (field.type === "select") {
    control = (
      <select {...common}>
        <option value="">Select</option>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    );
  } else if (field.type === "country") {
    control = (
      <select {...common} autoComplete="country">
        <option value="">Select country or region</option>
        {COUNTRIES.map(([code, name]) => (
          <option key={code} value={code}>{name}</option>
        ))}
      </select>
    );
  } else if (field.type === "textarea") {
    control = <textarea {...common} maxLength={field.max_length} rows={3} />;
  } else {
    control = (
      <input
        {...common}
        type={field.type === "email" ? "email" : "text"}
        inputMode={field.type === "email" ? "email" : undefined}
        autoComplete={field.autocomplete}
        maxLength={field.max_length}
      />
    );
  }
  return (
    <div className="zg-field">
      <label htmlFor={id}>
        {field.label}
        {field.required && <span className="zg-req" aria-hidden="true"> *</span>}
      </label>
      {control}
      {field.hint && <div id={`${id}-hint`} className="zg-hint">{field.hint}</div>}
      {error && <div id={`${id}-err`} className="zg-error">{error}</div>}
    </div>
  );
}

export default function LeadForm({ item, onAction, onDone }) {
  const { flow } = item;
  const idPrefix = `zg-lead-${flow.flow_id.slice(0, 8)}`;
  const initialValues = useMemo(
    () => Object.fromEntries(flow.fields.filter((f) => f.value !== undefined).map((f) => [f.field_id, f.value])),
    [flow],
  );
  const [values, setValues] = useState(initialValues);
  const [errors, setErrors] = useState({});
  const [consent, setConsent] = useState(false);
  const [phase, setPhase] = useState("collecting"); // collecting | submitting | confirmed | failed | unknown | cancelled
  const [result, setResult] = useState(null);
  const [formError, setFormError] = useState(null);
  const idemKey = useRef(api.uuid()); // reused across retries of the same attempt

  const set = (fid, v) => {
    setValues((s) => ({ ...s, [fid]: v }));
    if (errors[fid]) setErrors((e) => ({ ...e, [fid]: undefined }));
  };

  const focusFirstError = (errs) => {
    const first = flow.fields.find((f) => errs[f.field_id]) ? `${idPrefix}-${flow.fields.find((f) => errs[f.field_id]).field_id}` : errs.consent ? `${idPrefix}-consent` : null;
    if (first) requestAnimationFrame(() => document.getElementById(first)?.focus());
  };

  const submit = async (e) => {
    e.preventDefault();
    setFormError(null);
    // Light client checks; the server is authoritative (Figma §11.1).
    const local = {};
    flow.fields.forEach((f) => {
      if (f.required && !String(values[f.field_id] || "").trim()) local[f.field_id] = "This field is required.";
    });
    if (flow.consent.required && !consent) local.consent = "Please confirm we may contact you about this request.";
    if (Object.keys(local).length) {
      setErrors(local);
      focusFirstError(local);
      return;
    }
    setPhase("submitting");
    try {
      const payload = Object.fromEntries(flow.fields.map((f) => [f.field_id, String(values[f.field_id] ?? "")]));
      try {
        await api.updateLeadFlow(flow.flow_id, payload);
      } catch (err) {
        const fe = err?.problem?.field_errors;
        if (err.code === "GUIDE_VALIDATION_ERROR" && fe?.length) {
          const mapped = Object.fromEntries(fe.map((x) => [x.field, x.message]));
          setErrors(mapped);
          setPhase("collecting");
          focusFirstError(mapped);
          return;
        }
        throw err;
      }
      let receipt = null;
      if (flow.consent.required) {
        const c = await api.putConsent("sales_contact", "granted", flow.consent.notice_version);
        receipt = c.receipt_id;
      }
      const res = await api.submitLead(flow.flow_id, receipt, idemKey.current);
      setResult(res);
      setPhase(res.outcome);
      if (res.outcome === "failed") idemKey.current = api.uuid(); // a new attempt is a new key
      onDone?.(res.outcome);
    } catch (err) {
      if (err.code === "GUIDE_OUTCOME_UNKNOWN" || err.problem?.outcome === "unknown") {
        setResult({ message: err.problem?.detail || err.message, actions: err.problem?.actions || [] });
        setPhase("unknown");
        return;
      }
      if (err.code === "GUIDE_VALIDATION_ERROR" && err.problem?.field_errors?.some((x) => x.field === "consent")) {
        setErrors({ consent: "Please confirm we may contact you about this request." });
        setPhase("collecting");
        return;
      }
      setPhase("collecting");
      setFormError(err.message || "Your request couldn't be sent. Please try again.");
    }
  };

  const cancel = async () => {
    try {
      await api.cancelLeadFlow(flow.flow_id);
    } catch {
      /* cancelling is best-effort */
    }
    setPhase("cancelled");
  };

  const headingId = `${idPrefix}-title`;
  const title = flow.flow_kind === "sales" ? "Talk to sales" : "Request a demo";

  if (phase === "confirmed") {
    return (
      <section className="zg-card" aria-labelledby={headingId}>
        <div className="zg-notice zg-notice--success" role="status">
          <CheckCircle2 aria-hidden="true" />
          <div>
            <strong id={headingId}>Request received</strong>
            <div>{result.message}</div>
            {result.submission_reference && <div className="zg-meta">Reference: {result.submission_reference}</div>}
          </div>
        </div>
      </section>
    );
  }
  if (phase === "unknown") {
    return (
      <section className="zg-card" aria-labelledby={headingId}>
        <div className="zg-notice zg-notice--warning" role="alert">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong id={headingId}>We can't confirm this yet</strong>
            <div>{result?.message}</div>
          </div>
        </div>
        <Actions actions={result?.actions} onAction={onAction} />
      </section>
    );
  }
  if (phase === "cancelled") {
    return <p className="zg-meta">Request cancelled. Nothing was sent.</p>;
  }

  return (
    <section className="zg-card" aria-labelledby={headingId}>
      <p className="zg-eyebrow"><CalendarCheck aria-hidden="true" /> {title}</p>
      <h3 id={headingId} tabIndex={-1}>{flow.purpose_notice.text}</h3>
      <div className="zg-links" style={{ marginTop: 4 }}>
        <button
          type="button"
          className="zg-link"
          onClick={() => onAction({ action_id: "privacy-notice", type: "navigate", label: "Privacy notice", href: flow.purpose_notice.privacy_href })}
        >
          Privacy notice
        </button>
      </div>

      {phase === "failed" && (
        <div className="zg-notice zg-notice--error" role="alert" style={{ marginTop: 12 }}>
          <XCircle aria-hidden="true" />
          <span>{result?.message}</span>
        </div>
      )}
      {formError && (
        <div className="zg-notice zg-notice--error" role="alert" style={{ marginTop: 12 }}>
          <XCircle aria-hidden="true" />
          <span>{formError}</span>
        </div>
      )}

      <form className="zg-form" onSubmit={submit} noValidate>
        {flow.fields.map((f) => (
          <Field key={f.field_id} field={f} value={values[f.field_id]} error={errors[f.field_id]} onChange={set} idPrefix={idPrefix} />
        ))}
        {flow.consent.required && (
          <div>
            <label className="zg-consent" htmlFor={`${idPrefix}-consent`}>
              <input
                id={`${idPrefix}-consent`}
                type="checkbox"
                checked={consent}
                onChange={(e) => {
                  setConsent(e.target.checked);
                  setErrors((x) => ({ ...x, consent: undefined }));
                }}
                aria-invalid={errors.consent ? true : undefined}
                aria-describedby={errors.consent ? `${idPrefix}-consent-err` : undefined}
              />
              <span>{flow.consent.text}</span>
            </label>
            {errors.consent && <div id={`${idPrefix}-consent-err`} className="zg-error">{errors.consent}</div>}
          </div>
        )}
        <div className="zg-actions" style={{ marginTop: 0 }}>
          <button type="submit" className="zg-btn zg-btn--primary" disabled={phase === "submitting"}>
            {phase === "submitting" ? "Sending…" : phase === "failed" ? "Try again" : "Submit request"}
          </button>
          <button type="button" className="zg-btn zg-btn--ghost" onClick={cancel} disabled={phase === "submitting"}>
            Cancel
          </button>
        </div>
        {phase === "failed" && <Actions actions={result?.actions} onAction={onAction} />}
      </form>
    </section>
  );
}
