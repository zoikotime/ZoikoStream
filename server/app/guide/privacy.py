"""Privacy-rights requests against Guide records (Security/Privacy §16.1, §20; PD-10;
SP-AT-26 "privacy deletion: applicable data removed/queued").

Operator CLI, run by the approved privacy-rights workflow AFTER it has verified the requester
(§20.1: the Guide itself never collects identity to service a request):

    python -m app.guide.privacy locate --session <uuid>
    python -m app.guide.privacy locate --receipt cns_...
    python -m app.guide.privacy locate --email person@example.org
    python -m app.guide.privacy erase  --email person@example.org            # dry run
    python -m app.guide.privacy erase  --email person@example.org --execute [--case PR-123]

Locating never decrypts anything: sessions are found by id, consent receipts by receipt_ref,
and leads by guide_lead_flows.email_lookup_hash (an HMAC of the normalized email, written
when the visitor entered it). A pseudonymous session the requester cannot name cannot be
linked to them — that limitation is explained through the rights process (§20.1).

Erasure is a dry run unless --execute. Executing:
  1. ends every located session and revokes its capability at once;
  2. queues every located row for deletion by setting purge_after = now() (the same
     ordered, audited purge path as routine retention — no second deletion code path);
  3. runs the purge immediately unless --queue-only.
Rows that must outlive the request are reported as retained, never silently kept: external
operations still `in_progress` (deleted by the purge as soon as they resolve), non-content
audit metadata (security retention), and consent receipts unless --include-consent-receipts
(evidence of the purpose decision; Legal decides — §16 "or longer if Legal requires").

Every run writes a `privacy_request` audit event with counts and selector TYPE only — never
the email, receipt or session value supplied.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import crypto, purge, retention
from .db import new_maintenance_session
from .leads import email_lookup_hash
from .models import (
    GuideConsent, GuideExternalOperation, GuideFeedback, GuideHandoff, GuideLeadFieldValue,
    GuideLeadFlow, GuideMessage, GuidePublicEventContext, GuideSession,
    GuideSessionContextVersion, GuideSupportFlow, GuideSupportStepEvent,
)
from .sessions import audit

_CASE_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,48}$")


@dataclass
class Located:
    selector: str
    session_ids: set[uuid.UUID] = field(default_factory=set)
    consent_ids: set[uuid.UUID] = field(default_factory=set)
    lead_flow_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not (self.session_ids or self.consent_ids or self.lead_flow_ids)


def locate(db: Session, *, session_id: uuid.UUID | str | None = None,
           receipt: str | None = None, email: str | None = None) -> Located:
    """Exactly one selector. Unknown values locate nothing (and say nothing more)."""
    given = [k for k, v in (("session", session_id), ("receipt", receipt), ("email", email))
             if v]
    if len(given) != 1:
        raise ValueError("give exactly one of session_id, receipt, email")
    found = Located(selector=given[0])
    if session_id:
        try:
            sid = uuid.UUID(str(session_id))
        except ValueError:
            return found
        if db.get(GuideSession, sid) is not None:
            found.session_ids.add(sid)
    elif receipt:
        row = db.scalar(select(GuideConsent).where(GuideConsent.receipt_ref == receipt.strip()))
        if row is not None:
            found.consent_ids.add(row.consent_id)
            if row.session_id:
                found.session_ids.add(row.session_id)
    else:
        lookup = email_lookup_hash(email or "")
        if lookup is not None:
            for flow_id, sid, consent_id in db.execute(select(
                    GuideLeadFlow.flow_id, GuideLeadFlow.session_id, GuideLeadFlow.consent_id)
                    .where(GuideLeadFlow.email_lookup_hash == lookup)).all():
                found.lead_flow_ids.add(flow_id)
                found.session_ids.add(sid)
                if consent_id:
                    found.consent_ids.add(consent_id)
    if found.session_ids:
        found.lead_flow_ids.update(db.scalars(select(GuideLeadFlow.flow_id).where(
            GuideLeadFlow.session_id.in_(found.session_ids))).all())
        found.consent_ids.update(db.scalars(select(GuideConsent.consent_id).where(
            GuideConsent.session_id.in_(found.session_ids))).all())
    return found


def _count(db: Session, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def inventory(db: Session, found: Located) -> dict[str, int]:
    """Row counts per data class — what a request would touch. Never content."""
    sids, flows = list(found.session_ids), list(found.lead_flow_ids)
    support = select(GuideSupportFlow.flow_id).where(GuideSupportFlow.session_id.in_(sids))
    return {
        "sessions": len(sids),
        "messages": _count(db, GuideMessage, GuideMessage.session_id.in_(sids)),
        "context_versions": _count(db, GuideSessionContextVersion,
                                   GuideSessionContextVersion.session_id.in_(sids)),
        "event_contexts": _count(db, GuidePublicEventContext,
                                 GuidePublicEventContext.session_id.in_(sids)),
        "support_flows": _count(db, GuideSupportFlow, GuideSupportFlow.session_id.in_(sids)),
        "support_step_events": _count(db, GuideSupportStepEvent,
                                      GuideSupportStepEvent.flow_id.in_(support)),
        "lead_flows": len(flows),
        "lead_field_values": _count(db, GuideLeadFieldValue,
                                    GuideLeadFieldValue.flow_id.in_(flows)),
        "handoffs": _count(db, GuideHandoff, GuideHandoff.session_id.in_(sids)),
        "feedback": _count(db, GuideFeedback, GuideFeedback.session_id.in_(sids)),
        "external_operations": _count(db, GuideExternalOperation,
                                      GuideExternalOperation.session_id.in_(sids)),
        "external_operations_in_progress": _count(
            db, GuideExternalOperation, GuideExternalOperation.session_id.in_(sids),
            GuideExternalOperation.outcome == "in_progress"),
        "consent_receipts": len(found.consent_ids),
    }


def _queue(db: Session, found: Located, *, include_consents: bool) -> None:
    now = retention.now()
    sids, flows = list(found.session_ids), list(found.lead_flow_ids)
    for s in db.scalars(select(GuideSession).where(GuideSession.session_id.in_(sids))):
        if s.state != "ENDED":
            s.state, s.ended_at, s.end_reason = "ENDED", now, "privacy_request"
            s.active_flow_kind, s.active_flow_id = None, None
        # Revoke: nobody holds a capability matching these random values.
        s.capability_hash = crypto.keyed_hash(secrets.token_urlsafe(32),
                                              purpose=crypto.PURPOSE_SESSION_CAPABILITY)
        s.csrf_hash = crypto.keyed_hash(secrets.token_urlsafe(32),
                                        purpose=crypto.PURPOSE_SESSION_CSRF)
        s.purge_after = now
    for model in (GuideMessage, GuideSessionContextVersion, GuidePublicEventContext,
                  GuideSupportFlow, GuideHandoff, GuideFeedback, GuideExternalOperation,
                  GuideLeadFlow):
        db.execute(update(model).where(model.session_id.in_(sids)).values(purge_after=now))
    db.execute(update(GuideFeedback).where(GuideFeedback.session_id.in_(sids))
               .values(comment_purge_after=now))
    db.execute(update(GuideSupportStepEvent).where(GuideSupportStepEvent.flow_id.in_(
        select(GuideSupportFlow.flow_id).where(GuideSupportFlow.session_id.in_(sids))))
        .values(purge_after=now))
    db.execute(update(GuideLeadFlow).where(GuideLeadFlow.flow_id.in_(flows))
               .values(purge_after=now))
    db.execute(update(GuideLeadFieldValue).where(GuideLeadFieldValue.flow_id.in_(flows))
               .values(purge_after=now))
    # Operations whose session is already gone still name their lead flow.
    db.execute(update(GuideExternalOperation).where(GuideExternalOperation.subject_id.in_(
        flows)).values(purge_after=now))
    if include_consents and found.consent_ids:
        db.execute(update(GuideConsent).where(GuideConsent.consent_id.in_(
            list(found.consent_ids))).values(purge_after=now))


def handle(db: Session, action: str, found: Located, *, execute: bool = False,
           include_consents: bool = False, queue_only: bool = False,
           case_ref: str | None = None) -> dict:
    """locate | erase. Returns a JSON-safe report of counts (never content)."""
    if action not in ("locate", "erase"):
        raise ValueError("action must be locate or erase")
    if case_ref is not None and not _CASE_REF.match(case_ref):
        raise ValueError("case reference must be 1-48 of [A-Za-z0-9_.:-]")
    counts = inventory(db, found)
    dry_run = action == "erase" and not execute
    report = {"action": action, "selector": found.selector, "dry_run": dry_run,
              "found": counts, "retained": {
                  "audit_metadata": "non-content security/audit metadata (Security §16)",
                  "external_operations_in_progress":
                      "deleted by the purge once the downstream outcome resolves",
                  "consent_receipts": ("queued for deletion" if include_consents else
                                       "kept as evidence of the purpose decision; pass "
                                       "--include-consent-receipts when Legal approves"),
              } if action == "erase" else {}}
    purged: dict[str, int] = {}
    if action == "erase" and execute and not found.empty:
        _queue(db, found, include_consents=include_consents)
        db.commit()
        if not queue_only:
            purged = purge.purge_once()
    report["purged"] = purged
    audit(db, None, "privacy_request", action=action, selector=found.selector,
          dry_run=dry_run, executed=bool(action == "erase" and execute),
          include_consents=include_consents, queue_only=queue_only, case_ref=case_ref,
          **{f"found_{k}": v for k, v in counts.items()})
    db.commit()
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.guide.privacy",
                                description="Locate or erase Guide records for a VERIFIED "
                                            "privacy-rights request.")
    p.add_argument("action", choices=("locate", "erase"))
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--session", help="Guide session id (uuid)")
    sel.add_argument("--receipt", help="consent receipt reference (cns_...)")
    sel.add_argument("--email", help="work email given in a demo/sales request")
    p.add_argument("--execute", action="store_true",
                   help="erase for real (default is a dry run)")
    p.add_argument("--queue-only", action="store_true",
                   help="queue rows for the next purge instead of purging now")
    p.add_argument("--include-consent-receipts", action="store_true")
    p.add_argument("--case", help="privacy-rights case reference recorded in the audit event")
    args = p.parse_args(argv)
    db = new_maintenance_session()
    try:
        found = locate(db, session_id=args.session, receipt=args.receipt, email=args.email)
        report = handle(db, args.action, found, execute=args.execute,
                        include_consents=args.include_consent_receipts,
                        queue_only=args.queue_only, case_ref=args.case)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
