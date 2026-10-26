"""Lead form drafts (C6; Conversation Flow §3 "Pause lead flow ... Preserve form draft
server-side"; PR-013 "never re-ask data already given").

The widget saves what the visitor typed on field blur, so a paused or resumed lead flow keeps
it. A draft is held in the SAME encrypted per-field store as the flow's values
(guide_lead_field_values, envelope-encrypted under the lead value context), so it shares
their retention and is purged with the flow and its session — there is no second copy of
contact data anywhere.

Only approved schema fields are accepted and each value goes through the lead field
validator: a value that is not valid yet (a half-typed email, a pasted secret) is simply not
held — the visitor sees the inline error when they continue the form. Holding a draft needs
no consent: nothing leaves the Guide until the consented submit.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from . import leads
from .errors import GuideProblem
from .models import GuideLeadFlow, GuideSession

MAX_DRAFT_FIELDS = len(leads.FIELDS)


def save_draft(db: Session, request: Request, session: GuideSession, flow: GuideLeadFlow,
               fields: dict) -> None:
    if not isinstance(fields, dict) or not fields:
        raise GuideProblem("GUIDE_BAD_REQUEST", "Send at least one form field.")
    if len(fields) > MAX_DRAFT_FIELDS:
        raise GuideProblem("GUIDE_PAYLOAD_TOO_LARGE", "Too many fields.")
    allowed = set(flow.required_fields) | set(flow.optional_fields)
    unknown = sorted(f for f in fields if f not in allowed)
    if unknown:
        raise GuideProblem("GUIDE_VALIDATION_ERROR", "One or more fields are not part of the "
                                                     "form.",
                           field_errors=[{"field": f[:32], "code": "unknown_field",
                                          "message": "This field is not part of the form."}
                                         for f in unknown[:8]])
    if any(not isinstance(v, str) for v in fields.values()):
        raise GuideProblem("GUIDE_BAD_REQUEST", "Field values must be text.")
    # Invalid values are not stored (errors ignored here by design, see module docstring);
    # valid ones are encrypted exactly like a regular field update.
    leads.update_fields(db, request, session, flow, fields)
