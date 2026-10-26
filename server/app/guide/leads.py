"""Consent receipts and the demo/sales lead flow (API §11–12; Conversation Flow §8; PR-009..016).

Value first, data second: a lead flow exists only after the visitor explicitly chose Request a
demo / Talk to sales. The purpose notice is returned BEFORE any field is requested, fields are
limited to the approved schema, consent is purpose-specific, never pre-checked and required
before submit, and the outcome reported is exactly what the contact flow confirmed.

Field values are encrypted per field (guide_lead_field_values.value_ciphertext) and kept only
for the short retention window; the platform's contact inbox becomes the system of record once
a submission is confirmed.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid

from email_validator import EmailNotValidError, validate_email
from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import crypto, ratelimit, registry, retention
from .adapters import lead_adapter
from .errors import GuideProblem
from .models import (
    GuideConsent, GuideExternalOperation, GuideLeadFieldValue, GuideLeadFlow, GuideSession,
)
from .secrets_detect import detect
from .sessions import audit, on_secret_detected
from .settings import get_settings

SCHEMA_VERSION = "lead-v1"
NOTICE_VERSION = "sales-contact-v1"
VALUE_CONTEXT = "guide_lead_field_values.value"
CONTACT_TOPIC = "Platform evaluation & enterprise sales"  # an existing /contact topic label

PURPOSE_NOTICE = {
    "demo": ("Tell us how to reach you. We'll use these details only to respond to your demo "
             "request."),
    "sales": ("Tell us how to reach you. We'll use these details only to respond to your sales "
              "inquiry."),
}
CONSENT_TEXT = ("I agree that ZoikoStream may contact me about this request using the details "
                "I provide.")

USE_CASES = [("live_events", "Live events"), ("webinars", "Webinars or town halls"),
             ("worship", "Worship services"), ("memorials", "Memorial services"),
             ("education", "Education"), ("enterprise", "Internal company events"),
             ("other", "Other")]
TIMINGS = [("within_month", "Within a month"), ("one_to_three", "In 1-3 months"),
           ("later", "Later than 3 months"), ("exploring", "Just exploring")]

# field_id -> spec. The vocabulary is fixed by the API (§12.1); this is the approved subset.
FIELDS = {
    "full_name": {"label": "Full name", "type": "text", "required": True, "min": 2, "max": 100,
                  "data_class": "PII", "autocomplete": "name"},
    "work_email": {"label": "Work email", "type": "email", "required": True, "max": 254,
                   "data_class": "BUSINESS_CONTACT", "autocomplete": "email"},
    "country_code": {"label": "Country or region", "type": "country", "required": True,
                     "data_class": "BUSINESS_CONTEXT", "autocomplete": "country"},
    "organization": {"label": "Organization", "type": "text", "required": False, "min": 2,
                     "max": 160, "data_class": "BUSINESS_CONTEXT",
                     "autocomplete": "organization"},
    "use_case": {"label": "Use case", "type": "select", "required": False,
                 "options": USE_CASES, "data_class": "BUSINESS_CONTEXT"},
    "event_timing": {"label": "When is your event?", "type": "select", "required": False,
                     "options": TIMINGS, "data_class": "BUSINESS_CONTEXT"},
    "notes": {"label": "Anything else we should know?", "type": "textarea", "required": False,
              "max": 1200, "data_class": "BUSINESS_CONTEXT",
              "hint": "Please don't include passwords, PINs, or event links."},
}
REQUIRED = [f for f, spec in FIELDS.items() if spec["required"]]
OPTIONAL = [f for f, spec in FIELDS.items() if not spec["required"]]
_HEADER_FIELDS = {"full_name", "work_email", "organization", "country_code"}


# ── Consent ────────────────────────────────────────────────────────────────────────────────

def current_consent(db: Session, session_id: uuid.UUID, purpose: str) -> GuideConsent | None:
    return db.scalar(select(GuideConsent).where(
        GuideConsent.session_id == session_id, GuideConsent.purpose == purpose)
        .order_by(GuideConsent.recorded_at.desc(), GuideConsent.consent_id.desc()).limit(1))


def record_consent(db: Session, request: Request, session: GuideSession, purpose: str, *,
                   status: str, notice_version: str, source: str) -> GuideConsent:
    expected = NOTICE_VERSION if purpose == "sales_contact" else "analytics-v1"
    if notice_version != expected:
        raise GuideProblem("GUIDE_VALIDATION_ERROR", "The privacy notice has changed. Please "
                           "review it again.",
                           field_errors=[{"field": "notice_version", "code": "stale_notice",
                                          "message": "Review the current notice."}])
    previous = current_consent(db, session.session_id, purpose)
    if previous and previous.status == status and previous.notice_version == notice_version:
        return previous  # idempotent PUT: same state -> same receipt (API §16)
    if previous is None and status == "withdrawn":
        raise GuideProblem("GUIDE_VALIDATION_ERROR", "There is no consent to withdraw.")
    row = GuideConsent(
        session_id=session.session_id, receipt_ref=f"cns_{secrets.token_hex(12)}",
        purpose=purpose, status=status, notice_version=notice_version, source=source,
        supersedes_consent_id=previous.consent_id if previous else None,
        recorded_at=retention.now(), purge_after=retention.purge_after("consent"))
    db.add(row)
    if purpose == "optional_analytics":
        session.analytics_consent = "granted" if status == "granted" else "denied"
    audit(db, request, "consent_recorded", session_id=session.session_id, purpose=purpose,
          status=status, notice_version=notice_version)
    return row


def consent_body(c: GuideConsent) -> dict:
    return {"purpose": c.purpose, "status": c.status, "notice_version": c.notice_version,
            "recorded_at": c.recorded_at.isoformat().replace("+00:00", "Z"),
            "receipt_id": c.receipt_ref}


# ── Lead flow ──────────────────────────────────────────────────────────────────────────────

def _values(db: Session, flow: GuideLeadFlow) -> dict[str, str]:
    rows = db.scalars(select(GuideLeadFieldValue).where(
        GuideLeadFieldValue.flow_id == flow.flow_id)).all()
    return {r.field_id: crypto.decrypt(r.value_ciphertext, context=VALUE_CONTEXT) for r in rows}


def render(db: Session, flow: GuideLeadFlow, *, include_values: bool = True) -> dict:
    values = _values(db, flow) if include_values else {}
    fields = []
    for fid in flow.required_fields + flow.optional_fields:
        spec = FIELDS[fid]
        f = {"field_id": fid, "label": spec["label"], "type": spec["type"],
             "required": fid in flow.required_fields}
        for k in ("max", "hint", "autocomplete"):
            if k in spec:
                f["max_length" if k == "max" else k] = spec[k]
        if "options" in spec:
            f["options"] = [{"value": v, "label": label} for v, label in spec["options"]]
        # Echo the visitor's own values back to their own form (so a reload or a paused flow
        # never makes them retype — PR-013). Never sent anywhere else.
        if fid in values:
            f["value"] = values[fid]
        fields.append(f)
    privacy = registry.action_for(db, "privacy-notice")
    return {
        "flow_id": str(flow.flow_id), "flow_kind": flow.flow_kind, "status": flow.status,
        "schema_version": flow.schema_version,
        "purpose_notice": {"text": PURPOSE_NOTICE[flow.flow_kind],
                           "notice_version": flow.notice_version,
                           "privacy_href": privacy["href"] if privacy else "/privacy"},
        "consent": {"purpose": "sales_contact", "required": flow.consent_required,
                    "notice_version": flow.notice_version, "text": CONSENT_TEXT},
        "fields": fields,
        "missing_required": [f for f in flow.required_fields if f not in values],
    }


def start_flow(db: Session, request: Request, session: GuideSession, flow_kind: str,
               source_intent: str | None) -> GuideLeadFlow:
    active = db.scalar(select(GuideLeadFlow).where(
        GuideLeadFlow.session_id == session.session_id,
        GuideLeadFlow.status.in_(("collecting", "ready_to_submit", "failed")))
        .order_by(GuideLeadFlow.started_at.desc()).limit(1).with_for_update())
    if active is not None:
        # Resume rather than start over: data already given is never re-asked (PR-013).
        flow = active
    else:
        flow = GuideLeadFlow(
            session_id=session.session_id, flow_kind=flow_kind, status="collecting",
            schema_version=SCHEMA_VERSION, required_fields=REQUIRED, optional_fields=OPTIONAL,
            notice_version=NOTICE_VERSION, consent_required=True, source_intent=source_intent,
            started_at=retention.now(), purge_after=retention.purge_after("lead"))
        db.add(flow)
        db.flush()
        audit(db, request, "lead_flow_started", session_id=session.session_id,
              flow_id=str(flow.flow_id), flow_kind=flow_kind, source_intent=source_intent,
              schema_version=SCHEMA_VERSION)
    session.state = "LEAD_FLOW"
    session.active_flow_kind, session.active_flow_id = "lead", flow.flow_id
    return flow


def load_flow(db: Session, session: GuideSession, flow_id: uuid.UUID) -> GuideLeadFlow:
    flow = db.scalar(select(GuideLeadFlow).where(
        GuideLeadFlow.flow_id == flow_id, GuideLeadFlow.session_id == session.session_id)
        .with_for_update())
    if flow is None:
        raise GuideProblem("GUIDE_RESOURCE_NOT_FOUND")
    return flow


def _validate_field(fid: str, raw) -> tuple[str | None, str | None]:
    """-> (normalized value, error message)."""
    spec = FIELDS[fid]
    if not isinstance(raw, str):
        return None, "Enter a valid value."
    value = raw.strip()
    if not value:
        return ("", None) if not spec["required"] else (None, "This field is required.")
    if fid in _HEADER_FIELDS and ("\r" in value or "\n" in value):
        return None, "Remove line breaks from this field."
    if spec["type"] == "email":
        try:
            value = validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError:
            return None, "Enter a valid email address."
    elif spec["type"] == "country":
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{2}", value):
            return None, "Choose a country or region."
    elif spec["type"] == "select":
        if value not in {v for v, _ in spec["options"]}:
            return None, "Choose one of the listed options."
    if "min" in spec and len(value) < spec["min"]:
        return None, f"Enter at least {spec['min']} characters."
    if "max" in spec and len(value) > spec["max"]:
        return None, f"Use {spec['max']} characters or fewer."
    if detect(value).suspected:
        return None, "Please don't include passwords, PINs, card numbers, or event links."
    return value, None


def email_lookup_hash(email: str) -> bytes | None:
    """Keyed lookup hash of a work email (guide_lead_flows.email_lookup_hash), used only to
    locate records for a verified privacy-rights request (Security/Privacy §20). Normalized
    the way the form normalizes it, then case-folded, so "Jamie@Example.org " matches."""
    try:
        normalized = validate_email(email.strip(), check_deliverability=False).normalized
    except (EmailNotValidError, AttributeError):
        return None
    return crypto.keyed_hash(normalized.casefold(), purpose=crypto.PURPOSE_LEAD_EMAIL_LOOKUP)


def update_fields(db: Session, request: Request, session: GuideSession, flow: GuideLeadFlow,
                  values: dict) -> list[dict]:
    """Saves every valid field and returns field errors for the rest — one bad field never
    discards the others (Conversation Flow §8.2)."""
    if flow.status not in ("collecting", "ready_to_submit", "failed"):
        raise GuideProblem("GUIDE_STATE_CONFLICT", "This request can no longer be edited.",
                           extra={"current": render(db, flow, include_values=False)})
    errors = []
    secret_classes: set[str] = set()
    allowed = set(flow.required_fields) | set(flow.optional_fields)
    for fid, raw in values.items():
        if fid not in allowed:
            errors.append({"field": fid, "code": "unknown_field",
                           "message": "This field is not part of the form."})
            continue
        value, error = _validate_field(fid, raw)
        if error:
            if isinstance(raw, str):
                secret_classes.update(detect(raw).classes)
            errors.append({"field": fid, "code": "invalid", "message": error})
            continue
        if fid == "work_email":
            flow.email_lookup_hash = email_lookup_hash(value) if value else None
        row = db.get(GuideLeadFieldValue, (flow.flow_id, fid))
        if value == "":
            if row is not None:
                db.delete(row)
            continue
        cipher = crypto.encrypt(value, context=VALUE_CONTEXT)
        if row is None:
            db.add(GuideLeadFieldValue(flow_id=flow.flow_id, field_id=fid,
                                       value_ciphertext=cipher,
                                       data_class=FIELDS[fid]["data_class"],
                                       purge_after=flow.purge_after))
        else:
            row.value_ciphertext, row.updated_at = cipher, retention.now()
    db.flush()
    present = set(_values(db, flow))
    flow.status = "ready_to_submit" if set(flow.required_fields) <= present else "collecting"
    flow.row_version += 1
    flow.updated_at = retention.now()
    audit(db, request, "lead_fields_updated", session_id=session.session_id,
          flow_id=str(flow.flow_id), fields=sorted(values), error_fields=sorted(
              e["field"] for e in errors), status=flow.status)
    # A credential typed into the form is refused above and is a security event (SEC §7).
    on_secret_detected(db, request, session, sorted(secret_classes))
    return errors


def cancel_flow(db: Session, request: Request, session: GuideSession,
                flow: GuideLeadFlow) -> None:
    if flow.status in ("submitted", "submitting", "unknown"):
        raise GuideProblem("GUIDE_STATE_CONFLICT", "This request was already sent.")
    flow.status = "cancelled"
    db.query(GuideLeadFieldValue).filter(GuideLeadFieldValue.flow_id == flow.flow_id).delete()
    if session.active_flow_id == flow.flow_id:
        session.active_flow_kind, session.active_flow_id = None, None
    session.state = "ACTIVE"
    audit(db, request, "lead_flow_cancelled", session_id=session.session_id,
          flow_id=str(flow.flow_id))


# ── Submission ─────────────────────────────────────────────────────────────────────────────

OUTCOME_COPY = {
    "confirmed": "Thanks — your request was received. The ZoikoStream team will use the "
                 "details you shared to respond.",
    "failed": "Your request didn't go through. You can try again, or use the contact page "
              "directly.",
    "unknown": "I can't confirm whether that request was received yet. Please don't resubmit "
               "repeatedly; use the contact page if you need to follow up.",
}


def _contact_payload(flow: GuideLeadFlow, values: dict[str, str]) -> dict:
    name = values["full_name"].strip()
    first, _, last = name.rpartition(" ")
    first, last = (first, last) if first else (name, "-")
    labels = {fid: dict(FIELDS[fid].get("options", [])) for fid in ("use_case", "event_timing")}
    lines = [f"{'Demo request' if flow.flow_kind == 'demo' else 'Sales inquiry'} submitted "
             "through ZoikoStream Guide (public website assistant)."]
    if values.get("use_case"):
        lines.append(f"Use case: {labels['use_case'].get(values['use_case'])}")
    if values.get("event_timing"):
        lines.append(f"Event timing: {labels['event_timing'].get(values['event_timing'])}")
    if values.get("notes"):
        lines += ["", "Notes:", values["notes"]]
    return {"first": first[:80], "last": last[:80], "email": values["work_email"],
            "org": values.get("organization", "")[:120], "country": values["country_code"],
            "topic": CONTACT_TOPIC, "message": "\n".join(lines)[:4000]}


def submit(db: Session, request: Request, session: GuideSession, flow_id: uuid.UUID, *,
           idempotency_key: str, consent_receipt_id: str | None) -> tuple[int, dict]:
    if not idempotency_key or not 16 <= len(idempotency_key) <= 128:
        raise GuideProblem("GUIDE_BAD_REQUEST", "A valid Idempotency-Key header is required.")
    flow = load_flow(db, session, flow_id)
    key_hash = crypto.keyed_hash(idempotency_key, purpose=crypto.PURPOSE_IDEMPOTENCY_LEAD)
    values = _values(db, flow)
    # Keyed: an unkeyed digest of lead values would let anyone holding a dump test guesses
    # of a visitor's email against it.
    fingerprint = crypto.keyed_hash(json.dumps(
        {"flow": str(flow.flow_id), "consent": consent_receipt_id,
         "values": {k: hashlib.sha256(v.encode()).hexdigest()
                    for k, v in sorted(values.items())}},
        sort_keys=True), purpose=crypto.PURPOSE_LEAD_FINGERPRINT)

    prior = db.scalar(select(GuideExternalOperation).where(
        GuideExternalOperation.operation_type == "lead_submit",
        GuideExternalOperation.idempotency_key_hash == key_hash))
    if prior is not None:
        if prior.request_fingerprint != fingerprint or prior.subject_id != flow.flow_id:
            raise GuideProblem("GUIDE_STATE_CONFLICT",
                               "This Idempotency-Key was already used for a different request.")
        if prior.outcome == "in_progress":
            raise GuideProblem("GUIDE_STATE_CONFLICT", "This request is still being sent.")
        db.commit()
        return _outcome_response(db, flow, prior, replayed=True)

    if flow.status == "unknown":
        # No blind resubmission while a prior attempt may have arrived (API §12.2).
        raise GuideProblem("GUIDE_STATE_CONFLICT", OUTCOME_COPY["unknown"],
                           extra={"outcome": "unknown"})
    if flow.status in ("submitted", "submitting"):
        raise GuideProblem("GUIDE_STATE_CONFLICT", "This request was already sent.")
    if flow.status not in ("ready_to_submit", "failed"):
        raise GuideProblem("GUIDE_VALIDATION_ERROR", "Some required details are missing.",
                           field_errors=[{"field": f, "code": "required",
                                          "message": "This field is required."}
                                         for f in flow.required_fields if f not in values])
    consent_id = None
    if flow.consent_required:
        consent = current_consent(db, session.session_id, "sales_contact")
        if (consent is None or consent.status != "granted"
                or consent.receipt_ref != consent_receipt_id
                or consent.notice_version != flow.notice_version):
            raise GuideProblem("GUIDE_VALIDATION_ERROR",
                               "Please confirm we may contact you about this request.",
                               field_errors=[{"field": "consent", "code": "consent_required",
                                              "message": "Consent is required to submit."}])
        consent_id = consent.consent_id

    s = get_settings()
    ratelimit.enforce(request, "lead_submit", str(session.session_id),
                      s.GUIDE_RL_LEAD_SUBMIT_PER_HOUR, window=3600)

    op = GuideExternalOperation(
        session_id=session.session_id, operation_type="lead_submit", subject_id=flow.flow_id,
        idempotency_key_hash=key_hash, request_fingerprint=fingerprint, outcome="in_progress",
        attempt_count=0, purge_after=retention.purge_after("lead"))
    db.add(op)
    flow.status, flow.consent_id = "submitting", consent_id
    payload = _contact_payload(flow, values)
    db.commit()  # durable BEFORE the network call; no transaction held across it

    # Only a validated IP, derived per GUIDE_TRUSTED_PROXY_HOPS — never raw header text.
    result = lead_adapter().submit(payload, visitor_ip=ratelimit.client_ip(request))

    op = db.get(GuideExternalOperation, op.operation_id, with_for_update=True)
    flow = db.get(GuideLeadFlow, flow.flow_id, with_for_update=True)
    session = db.get(GuideSession, session.session_id, with_for_update=True)
    now = retention.now()
    op.outcome, op.attempt_count, op.last_attempt_at = result.outcome, op.attempt_count + 1, now
    flow.status = {"confirmed": "submitted", "failed": "failed",
                   "unknown": "unknown"}[result.outcome]
    flow.updated_at = now
    if result.outcome == "confirmed":
        op.public_reference = f"GD-{secrets.token_hex(4).upper()}"
        # The contact inbox is now the system of record; the local copy is kept only for the
        # short recovery window (Security/Privacy §16: 7 days after confirmation; configured
        # by GUIDE_RETENTION_LEAD_CONFIRMED_DAYS).
        short = retention.purge_after("lead_confirmed", at=now)
        db.execute(update(GuideLeadFieldValue).where(
            GuideLeadFieldValue.flow_id == flow.flow_id).values(purge_after=short))
        flow.purge_after = min(flow.purge_after, short)
        session.state = "SALES_HANDOFF"
        if session.active_flow_id == flow.flow_id:
            session.active_flow_kind, session.active_flow_id = None, None
    audit(db, request, "lead_submitted", session_id=session.session_id,
          flow_id=str(flow.flow_id), outcome=result.outcome, reason=result.reason,
          operation_id=str(op.operation_id), consent_receipt=consent_receipt_id)
    db.commit()
    return _outcome_response(db, flow, op, replayed=False)


def _outcome_response(db: Session, flow: GuideLeadFlow, op: GuideExternalOperation, *,
                      replayed: bool) -> tuple[int, dict]:
    # A dead-lettered operation is still UNCONFIRMED to the visitor (ARCH §12.2): same copy,
    # same direct contact route, still no blind resubmission.
    outcome = "unknown" if op.outcome == "dead_letter" else op.outcome
    body = {"flow_id": str(flow.flow_id), "outcome": outcome,
            "message": OUTCOME_COPY.get(outcome, OUTCOME_COPY["unknown"]),
            "submission_reference": op.public_reference if op.outcome == "confirmed" else None,
            "replayed": replayed, "actions": []}
    if outcome != "confirmed":
        a = registry.action_for(db, "contact-sales")
        if a:
            body["actions"].append(a)
    if outcome == "unknown":
        raise GuideProblem("GUIDE_OUTCOME_UNKNOWN", OUTCOME_COPY["unknown"], extra=body)
    return 200, body
