import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CalendarCheck, CheckCircle2, PauseCircle, XCircle } from "lucide-react";
import * as api from "../guideApi";
import { track } from "../guideAnalytics";
import Actions from "./Actions";
import { leadTitle } from "./leadUtils";

// Demo / sales request (WF-06, Conversation Flow §8). The server supplies the purpose notice,
// the field set and the consent wording; this form renders them and reports exactly the
// outcome the server confirmed: confirmed | failed | unknown (PR-014, PR-027).
//
// Steps: details (+ consent) -> review -> submit (WF-06 "Step 3: review + explicit Submit
// request", CF §8 L4). Everything the visitor typed, the step and the outcome live on the
// timeline item (via onPatch), so closing/reopening Guide or switching views never loses them
// (Figma §10.1, PR-013). Nothing is written to browser storage; cancel/submit clear it.
//
// Draft (C6, CF §3 "Preserve form draft server-side"): each field the visitor leaves is saved
// to the lead draft endpoint (debounced, best effort), so the server can hand the values back
// when the flow is resumed. Header Back pauses the form (phase "paused") without losing it.

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
const COUNTRY_NAME = Object.fromEntries(COUNTRIES);

// Light client checks (Figma §11); the server stays authoritative (§11.1).
const LENGTHS = { full_name: [2, 100], organization: [2, 160], notes: [0, 1200] };
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateField(field, raw) {
  const value = String(raw ?? "").trim();
  if (!value) return field.required ? "This field is required." : undefined;
  if (field.type === "email" && !EMAIL_RE.test(value)) return "Enter a valid email address.";
  const [min, max] = LENGTHS[field.field_id] || [0, field.max_length];
  const limit = Math.min(max || Infinity, field.max_length || Infinity);
  if (min && value.length < min) return `Enter at least ${min} characters.`;
  if (Number.isFinite(limit) && value.length > limit) return `Use ${limit} characters or fewer.`;
  return undefined;
}

function displayValue(field, value) {
  if (field.type === "country") return COUNTRY_NAME[value] || value;
  if (field.type === "select") return field.options?.find((o) => o.value === value)?.label || value;
  return value;
}

function Field({ field, value, error, onChange, onBlur, idPrefix }) {
  const id = `${idPrefix}-${field.field_id}`;
  const describedBy = [error ? `${id}-err` : null, field.hint ? `${id}-hint` : null].filter(Boolean).join(" ") || undefined;
  const limit = LENGTHS[field.field_id]?.[1] || field.max_length;
  const common = {
    id,
    name: field.field_id,
    value: value ?? "",
    "aria-invalid": error ? true : undefined,
    "aria-describedby": describedBy,
    "aria-required": field.required || undefined,
    onChange: (e) => onChange(field.field_id, e.target.value),
    onBlur: () => onBlur(field.field_id),
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
    control = <textarea {...common} maxLength={limit} rows={3} />;
  } else {
    control = (
      <input
        {...common}
        type={field.type === "email" ? "email" : "text"}
        inputMode={field.type === "email" ? "email" : undefined}
        autoComplete={field.autocomplete}
        maxLength={limit}
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

const DRAFT_DEBOUNCE_MS = 600;

// Debounced, best-effort draft saves of the fields the visitor has left (C6). Only values that
// pass the light client checks are sent; the server stays authoritative.
function useLeadDraft(flow, values) {
  const valuesRef = useRef(values);
  const dirty = useRef(new Set());
  const timer = useRef(null);
  useEffect(() => {
    valuesRef.current = values;
  });
  const flush = () => {
    const fields = {};
    for (const fid of dirty.current) {
      const field = flow.fields.find((f) => f.field_id === fid);
      const raw = String(valuesRef.current?.[fid] ?? "").trim();
      if (field && raw && !validateField(field, raw)) fields[fid] = raw;
    }
    dirty.current.clear();
    if (Object.keys(fields).length) api.saveLeadDraft(flow.flow_id, fields).catch(() => {});
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  return (fid) => {
    dirty.current.add(fid);
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, DRAFT_DEBOUNCE_MS);
  };
}

export default function LeadForm({ item, onAction, onPatch = () => {} }) {
  const { flow } = item;
  const idPrefix = `zg-lead-${flow.flow_id.slice(0, 8)}`;
  // Values the server echoed back (a resumed flow) seed the draft; the visitor's own edits win.
  const serverValues = Object.fromEntries(flow.fields.filter((f) => f.value !== undefined).map((f) => [f.field_id, f.value]));
  const values = item.values || serverValues;
  const consent = !!item.consent;
  const phase = item.phase || "collecting"; // collecting | checking | reviewing | submitting | confirmed | failed | unknown | cancelled
  const result = item.result || null;
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState(null);
  const [summary, setSummary] = useState(false); // error summary when >1 field fails (Figma §13)
  const save = (patch) => onPatch(item.id, patch);
  const saveDraft = useLeadDraft(flow, values);
  const headingId = `${idPrefix}-title`;
  const summaryId = `${idPrefix}-errors`;

  // Lead flow opened or resumed: focus the flow heading (Figma §13.2 "Demo: Flow heading;
  // first field after purpose text").
  useEffect(() => {
    if (!item.focusPending || phase !== "collecting") return;
    const el = document.getElementById(headingId);
    if (el) {
      el.focus();
      el.scrollIntoView?.({ block: "nearest" });
    }
    onPatch(item.id, { focusPending: false });
  }, [item.focusPending, item.id, phase, headingId, onPatch]);

  const set = (fid, v) => {
    save({ values: { ...values, [fid]: v } });
    if (errors[fid]) setErrors((e) => ({ ...e, [fid]: undefined }));
  };
  // Validate after interaction, never on untouched fields (Figma §11.1).
  const blur = (fid) => {
    const field = flow.fields.find((f) => f.field_id === fid);
    if (!field || values[fid] === undefined) return;
    const err = validateField(field, values[fid]);
    setErrors((e) => ({ ...e, [fid]: err }));
    saveDraft(fid);
  };

  const focusId = (id) => requestAnimationFrame(() => document.getElementById(id)?.focus());
  // One error: focus that field. More than one: an error summary linking to each field, which
  // takes focus (Figma §11.1 "move focus to error summary or first invalid field", §13).
  const focusFirstError = (errs) => {
    const count = Object.values(errs).filter(Boolean).length;
    setSummary(count > 1);
    if (count > 1) {
      focusId(summaryId);
      return;
    }
    const f = flow.fields.find((x) => errs[x.field_id]);
    if (f) focusId(`${idPrefix}-${f.field_id}`);
    else if (errs.consent) focusId(`${idPrefix}-consent`);
  };

  // Step 1 -> 2: client checks, then the server validates the fields (authoritative).
  const review = async (e) => {
    e.preventDefault();
    setFormError(null);
    const local = {};
    flow.fields.forEach((f) => {
      const err = validateField(f, values[f.field_id]);
      if (err) local[f.field_id] = err;
    });
    if (flow.consent.required && !consent) local.consent = "Please confirm we may contact you about this request.";
    if (Object.keys(local).length) {
      setErrors(local);
      focusFirstError(local);
      return;
    }
    save({ phase: "checking" });
    try {
      const payload = Object.fromEntries(flow.fields.map((f) => [f.field_id, String(values[f.field_id] ?? "").trim()]));
      await api.updateLeadFlow(flow.flow_id, payload);
      save({ phase: "reviewing" });
      focusId(`${idPrefix}-review`);
    } catch (err) {
      save({ phase: "collecting" });
      const fe = err?.problem?.field_errors;
      if (err?.code === "GUIDE_VALIDATION_ERROR" && fe?.length) {
        const mapped = Object.fromEntries(fe.map((x) => [x.field, x.message]));
        setErrors(mapped);
        focusFirstError(mapped);
        return;
      }
      setFormError(err?.message || "Your details couldn't be checked. Please try again.");
    }
  };

  // Step 3: consent receipt + idempotent submit. The key survives close/reopen (it lives on
  // the item), so a retry of the same attempt can never create a second lead.
  const submit = async () => {
    setFormError(null);
    const idemKey = item.idemKey || api.uuid();
    save({ phase: "submitting", idemKey });
    try {
      let receipt = null;
      if (flow.consent.required) {
        const c = await api.putConsent("sales_contact", "granted", flow.consent.notice_version);
        receipt = c.receipt_id;
      }
      const res = await api.submitLead(flow.flow_id, receipt, idemKey);
      if (res.outcome === "confirmed") {
        track("lead_submitted", { flow_kind: flow.flow_kind, outcome: "confirmed", has_reference: !!res.submission_reference, consent_given: !!receipt });
        // Submitted: the draft is no longer needed in memory.
        save({ phase: "confirmed", result: res, values: undefined, consent: false });
      } else {
        track("error", { error_class: `lead_${res.outcome}`, flow_kind: flow.flow_kind });
        // A failed attempt gets a new key; an unknown outcome keeps it (no blind resubmit).
        save({ phase: res.outcome, result: res, ...(res.outcome === "failed" ? { idemKey: api.uuid() } : {}) });
      }
    } catch (err) {
      if (err?.code === "GUIDE_OUTCOME_UNKNOWN" || err?.problem?.outcome === "unknown") {
        track("error", { error_class: "lead_unknown", flow_kind: flow.flow_kind });
        save({ phase: "unknown", result: { message: err.problem?.detail || err.message, actions: err.problem?.actions || [] } });
        return;
      }
      if (err?.code === "GUIDE_VALIDATION_ERROR") {
        const fe = err.problem?.field_errors || [];
        const mapped = Object.fromEntries(fe.map((x) => [x.field, x.message]));
        if (fe.some((x) => x.field === "consent")) mapped.consent = "Please confirm we may contact you about this request.";
        setErrors(mapped);
        save({ phase: "collecting" });
        focusFirstError(mapped);
        return;
      }
      save({ phase: "reviewing" });
      setFormError(err?.message || "Your request couldn't be sent. Please try again.");
    }
  };

  const cancel = async () => {
    try {
      await api.cancelLeadFlow(flow.flow_id);
    } catch {
      /* cancelling is best-effort */
    }
    save({ phase: "cancelled", values: undefined, consent: false });
  };

  const title = leadTitle(flow);
  const submitLabel = flow.flow_kind === "sales" ? "Submit request" : "Submit demo request"; // WF-06 A4

  if (phase === "confirmed") {
    return (
      <section className="zg-card" aria-labelledby={headingId}>
        <div className="zg-notice zg-notice--success" role="status">
          <CheckCircle2 aria-hidden="true" />
          <div>
            <strong id={headingId}>Request received</strong>
            <div>{result?.message}</div>
            {result?.submission_reference && <div className="zg-meta">Reference: {result.submission_reference}</div>}
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
            <strong id={headingId}>We can&apos;t confirm this yet</strong>
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
  if (phase === "paused") {
    // Header Back left the form: nothing is lost, and it can be picked up again (CF §3).
    return (
      <section className="zg-card" aria-labelledby={headingId}>
        <p className="zg-eyebrow"><PauseCircle aria-hidden="true" /> {title}</p>
        <h3 id={headingId} tabIndex={-1}>Your request is paused. Your details are kept.</h3>
        <div className="zg-actions">
          <button type="button" className="zg-btn zg-btn--secondary" onClick={() => save({ phase: "collecting", focusPending: true })}>
            Continue request
          </button>
          <button type="button" className="zg-btn zg-btn--ghost" onClick={cancel}>
            Cancel
          </button>
        </div>
      </section>
    );
  }

  const errorBanner = (
    <>
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
    </>
  );

  if (phase === "reviewing" || phase === "failed" || phase === "submitting") {
    const filled = flow.fields.filter((f) => String(values[f.field_id] ?? "").trim());
    return (
      <section className="zg-card" aria-labelledby={`${idPrefix}-review`}>
        <p className="zg-eyebrow"><CalendarCheck aria-hidden="true" /> {title}</p>
        <h3 id={`${idPrefix}-review`} tabIndex={-1}>Review your request</h3>
        {errorBanner}
        <dl className="zg-review">
          {filled.map((f) => (
            <div key={f.field_id}>
              <dt>{f.label}</dt>
              <dd>{displayValue(f, String(values[f.field_id]).trim())}</dd>
            </div>
          ))}
          {flow.consent.required && (
            <div>
              <dt>Contact consent</dt>
              <dd>Given</dd>
            </div>
          )}
        </dl>
        <div className="zg-actions">
          <button type="button" className="zg-btn zg-btn--primary" onClick={submit} disabled={phase === "submitting"}>
            {phase === "submitting" ? "Sending…" : phase === "failed" ? "Try again" : submitLabel}
          </button>
          <button type="button" className="zg-btn zg-btn--ghost" onClick={() => save({ phase: "collecting" })} disabled={phase === "submitting"}>
            Edit
          </button>
          <button type="button" className="zg-btn zg-btn--ghost" onClick={cancel} disabled={phase === "submitting"}>
            Cancel
          </button>
        </div>
        {phase === "failed" && <Actions actions={result?.actions} onAction={onAction} />}
      </section>
    );
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

      {errorBanner}

      {summary && Object.values(errors).filter(Boolean).length > 1 && (
        <div className="zg-error-summary" id={summaryId} tabIndex={-1} role="alert" aria-labelledby={`${summaryId}-h`}>
          <p id={`${summaryId}-h`}>
            <strong>Please fix {Object.values(errors).filter(Boolean).length} fields:</strong>
          </p>
          <ul>
            {[...flow.fields.map((f) => [f.field_id, f.label]), ["consent", "Contact consent"]]
              .filter(([fid]) => errors[fid])
              .map(([fid, label]) => (
                <li key={fid}>
                  <a
                    className="zg-link"
                    href={`#${idPrefix}-${fid}`}
                    onClick={(e) => {
                      e.preventDefault();
                      document.getElementById(`${idPrefix}-${fid}`)?.focus();
                    }}
                  >
                    {label}: {errors[fid]}
                  </a>
                </li>
              ))}
          </ul>
        </div>
      )}

      <form className="zg-form" onSubmit={review} noValidate>
        {flow.fields.map((f) => (
          <Field key={f.field_id} field={f} value={values[f.field_id]} error={errors[f.field_id]} onChange={set} onBlur={blur} idPrefix={idPrefix} />
        ))}
        {flow.consent.required && (
          <div>
            <label className="zg-consent" htmlFor={`${idPrefix}-consent`}>
              <input
                id={`${idPrefix}-consent`}
                type="checkbox"
                checked={consent}
                onChange={(e) => {
                  save({ consent: e.target.checked });
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
          <button type="submit" className="zg-btn zg-btn--primary" disabled={phase === "checking"}>
            {phase === "checking" ? "Checking…" : "Continue"}
          </button>
          <button type="button" className="zg-btn zg-btn--ghost" onClick={cancel} disabled={phase === "checking"}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}
