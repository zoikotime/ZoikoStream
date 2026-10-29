"""Documented responses and examples for every public Guide operation (API §22, App. A).

`responses(operation_id)` returns the FastAPI `responses=` mapping for one operation: its
success body (schema + a material example) and EVERY error status App. A lists for it, each
with an application/problem+json example per stable code (API §5). Example identity data is
synthetic (API §23) and no example carries a credential, host name or provider name.

The response models are documentation only (see schemas.py "Response contracts").
"""
from __future__ import annotations

from .. import schemas as S
from ..errors import CODES

PROBLEM_MEDIA = "application/problem+json"
SID = "0be3c6a2-6f0e-4b8e-9d55-3f1f4a8f2c11"
FLOW = "6b0d2a4c-7f3e-4a1b-8c9d-0e1f2a3b4c5d"
LEAD = "9a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d"

# Default detail per code, matching what the server actually says.
_DETAIL = {
    "GUIDE_BAD_REQUEST": "The request does not match the expected schema.",
    "GUIDE_SESSION_INVALID": "Guide session is not valid.",
    "GUIDE_PUBLIC_BOUNDARY_DENIED": "Request origin is not permitted.",
    "GUIDE_RESOURCE_NOT_FOUND": "Resource not found.",
    "GUIDE_STATE_CONFLICT": "That step is no longer current.",
    "GUIDE_PAYLOAD_TOO_LARGE": "Messages can be up to 4000 characters.",
    "GUIDE_VALIDATION_ERROR": "One or more submitted fields are invalid.",
    "GUIDE_RATE_LIMITED": "Too many requests.",
    "GUIDE_UNAVAILABLE": ("Guide is unavailable right now. You can still use the Help Center, "
                          "status page, or contact support directly."),
    "GUIDE_OUTCOME_UNKNOWN": ("I can't confirm whether that request was received yet. Please "
                              "don't resubmit repeatedly; use the contact page if you need "
                              "to follow up."),
    "GUIDE_INTERNAL_ERROR": "Guide could not complete the request.",
}

ACTION_CONTACT_SALES = {"action_id": "contact-sales", "type": "handoff", "label": "Talk to sales",
                        "href": "/contact"}
ACTION_STATUS = {"action_id": "status-page", "type": "open_status", "label": "Service status",
                 "href": "/status"}
ACTION_VIEWER = {"action_id": "viewer-support", "type": "handoff", "label": "Viewer support",
                 "href": "/contact"}


def problem_example(code: str, detail: str | None = None, **extra) -> dict:
    status, slug, title, retryable = CODES[code]
    body = {"type": f"/problems/{slug}", "title": title, "status": status,
            "detail": detail or _DETAIL[code], "code": code,
            "request_id": "req_5f2a9c0d1e3b4a6f7c8d9e0a", "correlation_id":
            "cor_1a2b3c4d5e6f7a8b9c0d1e2f", "retryable": retryable}
    body.update(extra)
    return body


# Per-operation special problem examples (code -> example body), beyond the default one.
_SPECIAL = {
    ("createSession", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT",
        "This session request was already processed. Start a new session."),
    ("startSupportFlow", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT",
        "You're partway through playback help. Starting a different viewer issue will "
        "replace that progress.",
        confirm_required=True, active_flow_type="playback", active_flow_id=FLOW),
    ("startSupportFlow", "GUIDE_UNAVAILABLE"): problem_example(
        "GUIDE_UNAVAILABLE",
        "Viewer help in Guide is paused right now. Viewer support, the Help Center and the "
        "status page are still available directly.",
        paused={"feature": "viewer_support",
                "message": "Viewer help in Guide is paused right now. Viewer support, the "
                           "Help Center and the status page are still available directly.",
                "routes": [ACTION_VIEWER, ACTION_STATUS]}),
    ("startLeadFlow", "GUIDE_UNAVAILABLE"): problem_example(
        "GUIDE_UNAVAILABLE",
        "Requests through Guide are paused right now. You can still reach the ZoikoStream "
        "team through the contact page.",
        paused={"feature": "leads",
                "message": "Requests through Guide are paused right now. You can still reach "
                           "the ZoikoStream team through the contact page.",
                "routes": [ACTION_CONTACT_SALES]}),
    ("saveLeadDraft", "GUIDE_UNAVAILABLE"): problem_example(
        "GUIDE_UNAVAILABLE",
        "Requests through Guide are paused right now. You can still reach the ZoikoStream "
        "team through the contact page.",
        paused={"feature": "leads",
                "message": "Requests through Guide are paused right now. You can still reach "
                           "the ZoikoStream team through the contact page.",
                "routes": [ACTION_CONTACT_SALES]}),
    ("updateLeadFlow", "GUIDE_VALIDATION_ERROR"): problem_example(
        "GUIDE_VALIDATION_ERROR", "One or more fields need attention.",
        field_errors=[{"field": "work_email", "code": "invalid",
                       "message": "Enter a valid email address."}]),
    ("submitLeadFlow", "GUIDE_VALIDATION_ERROR"): problem_example(
        "GUIDE_VALIDATION_ERROR", "Please confirm we may contact you about this request.",
        field_errors=[{"field": "consent", "code": "consent_required",
                       "message": "Consent is required to submit."}]),
    ("submitLeadFlow", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT",
        "This Idempotency-Key was already used for a different request."),
    ("createHandoff", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT",
        "This Idempotency-Key was already used for a different request."),
    ("submitLeadFlow", "GUIDE_OUTCOME_UNKNOWN"): problem_example(
        "GUIDE_OUTCOME_UNKNOWN", flow_id=LEAD, outcome="unknown",
        message=_DETAIL["GUIDE_OUTCOME_UNKNOWN"], submission_reference=None, replayed=False,
        actions=[ACTION_CONTACT_SALES]),
    ("submitSupportStep", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT", "That step is no longer current.",
        current={"flow_id": FLOW, "flow_type": "playback", "status": "in_progress",
                 "step": {"step_id": "refresh"}}),
    ("submitMessage", "GUIDE_STATE_CONFLICT"): problem_example(
        "GUIDE_STATE_CONFLICT", "Guide is still answering your previous message."),
}

# Error statuses per operation (API App. A; plus the routes this build adds).
ERRORS = {
    "getBootstrap": ("GUIDE_BAD_REQUEST", "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE"),
    "createSession": ("GUIDE_BAD_REQUEST", "GUIDE_PUBLIC_BOUNDARY_DENIED",
                      "GUIDE_STATE_CONFLICT", "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE"),
    "updateSessionContext": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                             "GUIDE_PUBLIC_BOUNDARY_DENIED", "GUIDE_RATE_LIMITED",
                             "GUIDE_UNAVAILABLE"),
    "submitMessage": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                      "GUIDE_PUBLIC_BOUNDARY_DENIED", "GUIDE_STATE_CONFLICT",
                      "GUIDE_PAYLOAD_TOO_LARGE", "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE",
                      "GUIDE_OUTCOME_UNKNOWN"),
    "startSupportFlow": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                         "GUIDE_PUBLIC_BOUNDARY_DENIED", "GUIDE_STATE_CONFLICT",
                         "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE"),
    "submitSupportStep": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                          "GUIDE_RESOURCE_NOT_FOUND", "GUIDE_STATE_CONFLICT",
                          "GUIDE_VALIDATION_ERROR", "GUIDE_RATE_LIMITED"),
    "putConsent": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID", "GUIDE_VALIDATION_ERROR"),
    "startLeadFlow": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID", "GUIDE_RATE_LIMITED",
                      "GUIDE_UNAVAILABLE"),
    "updateLeadFlow": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                       "GUIDE_RESOURCE_NOT_FOUND", "GUIDE_STATE_CONFLICT",
                       "GUIDE_VALIDATION_ERROR", "GUIDE_RATE_LIMITED"),
    "saveLeadDraft": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                      "GUIDE_RESOURCE_NOT_FOUND", "GUIDE_STATE_CONFLICT",
                      "GUIDE_PAYLOAD_TOO_LARGE", "GUIDE_VALIDATION_ERROR",
                      "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE"),
    "submitLeadFlow": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID", "GUIDE_STATE_CONFLICT",
                       "GUIDE_VALIDATION_ERROR", "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE",
                       "GUIDE_OUTCOME_UNKNOWN"),
    "createHandoff": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID",
                      "GUIDE_PUBLIC_BOUNDARY_DENIED", "GUIDE_STATE_CONFLICT",
                      "GUIDE_RATE_LIMITED", "GUIDE_UNAVAILABLE"),
    "submitFeedback": ("GUIDE_BAD_REQUEST", "GUIDE_SESSION_INVALID", "GUIDE_RATE_LIMITED"),
    "endSession": ("GUIDE_SESSION_INVALID",),
}

# ── Success examples ───────────────────────────────────────────────────────────────────────

_ACTIONS_DIRECT = [{"action_id": "help-center", "type": "open_help", "label": "Contact ZoikoStream",
                    "href": "/contact"}, ACTION_STATUS]
BOOTSTRAP = {
    "api_version": "1.0", "assistant": {"name": "Guide", "descriptor": "Public AI Assistant"},
    "enabled": True, "locale": "en-US",
    "welcome": "Welcome to ZoikoStream Guide. I can help with product questions, pricing and "
               "demos, or viewer support.",
    "suggested_intents": [{"intent_id": "I-PRICING", "label": "Pricing & plans", "priority": 10},
                          {"intent_id": "I-DEMO", "label": "Request a demo", "priority": 9}],
    "limits": {"max_message_chars": 4000, "max_concurrent_requests": 1},
    "privacy": {"privacy_notice_href": "/privacy", "terms_href": "/trust"},
    "direct_routes": _ACTIONS_DIRECT,
}
BOOTSTRAP_DISABLED = {**BOOTSTRAP, "enabled": False, "welcome": None, "suggested_intents": [],
                      "unavailable_reason": "disabled"}
SESSION_BODY = {"session_id": SID, "session_state": "ACTIVE",
                "expires_at": "2026-10-24T14:00:00Z", "idle_timeout_seconds": 1800,
                "context_version": 1, "event_context": None}
SESSION_CREATED = {**SESSION_BODY, "csrf_token": "csrf-example-token-not-real",
                   "assistant": BOOTSTRAP["assistant"], "welcome": BOOTSTRAP["welcome"],
                   "suggested_intents": BOOTSTRAP["suggested_intents"],
                   "privacy": {"privacy_notice_href": "/privacy"}}
ANSWER = {
    "request_id": "req_5f2a9c0d1e3b4a6f7c8d9e0a", "session_id": SID,
    "user_message_id": "2f6f0c1a-1b2c-4d3e-8f4a-5b6c7d8e9f0a",
    "assistant_message": {
        "message_id": "71b5a0c2-3d4e-4f5a-9b6c-7d8e9f0a1b2c",
        "text": "Yes. ZoikoStream supports secure live events with private audience access, "
                "recording and replay.",
        "answer_class": "discovery", "grounded": True,
        "sources": [{"source_id": "product-live-events", "title": "ZoikoStream Live Events",
                     "source_type": "product", "version": "2026.09", "updated_at": None,
                     "canonical_path": "/"}],
        "actions": [{"action_id": "request-demo", "type": "start_lead_flow",
                     "label": "Request a demo"}],
        "uncertainty": {"state": "none", "message": None}},
    "session_state": "ACTIVE",
}
CLARIFY = {**ANSWER, "assistant_message": {
    **ANSWER["assistant_message"], "text": "Happy to help you find the right setup.",
    "grounded": False, "sources": [], "actions": [],
    "clarification": {"question": "What kind of event are you planning?",
                      "options": [{"id": "live_event", "label": "A live event"},
                                  {"id": "webinar", "label": "A webinar or town hall"},
                                  {"id": "memorial", "label": "A memorial service"}]}}}
SSE_EXAMPLE = (
    'event: response.started\ndata: {"request_id":"req_5f2a9c0d1e3b4a6f7c8d9e0a",'
    '"assistant_message_id":null}\n\n'
    'event: response.progress\ndata: {"stage":"retrieving"}\n\n'
    'event: response.progress\ndata: {"stage":"drafting"}\n\n'
    ': keep-alive\n\n'
    'event: response.progress\ndata: {"stage":"checking"}\n\n'
    'event: response.text.delta\ndata: {"delta":"Yes. ZoikoStream supports secure live "}\n\n'
    'event: response.source\ndata: {"source_id":"product-live-events","title":'
    '"ZoikoStream Live Events","version":"2026.09"}\n\n'
    'event: response.action\ndata: {"action_id":"request-demo","type":"start_lead_flow",'
    '"label":"Request a demo"}\n\n'
    'event: response.completed\ndata: {"assistant_message_id":'
    '"71b5a0c2-3d4e-4f5a-9b6c-7d8e9f0a1b2c","grounded":true,"answer_class":"discovery",'
    '"session_state":"ACTIVE"}\n\n')
SSE_ERROR_EXAMPLE = (
    'event: response.started\ndata: {"request_id":"req_5f2a9c0d1e3b4a6f7c8d9e0a",'
    '"assistant_message_id":null}\n\n'
    'event: response.error\ndata: {"code":"GUIDE_UNAVAILABLE","retryable":true}\n\n')
SUPPORT_FLOW = {
    "flow_id": FLOW, "flow_type": "viewer_access", "title": "Event access",
    "status": "in_progress", "resolution_code": None,
    "step": {"step_id": "invitation_source", "prompt": "How did you receive the event invitation?",
             "input_type": "choice",
             "options": [{"option_id": "email_invitation", "label": "Email invitation"},
                         {"option_id": "message_link", "label": "Message or shared link"},
                         {"option_id": "other", "label": "Other"}],
             "allow_skip": False, "allow_back": False},
    "message": None, "notice": "Never share your event PIN, password, or invitation link here.",
    "actions": [],
}
SUPPORT_STATUS_STEP = {
    "flow_id": FLOW, "flow_type": "playback", "title": "Playback help",
    "status": "in_progress", "resolution_code": None,
    "step": {"step_id": "public_status",
             "prompt": "You can check the public ZoikoStream status page for any reported "
                       "service issue. A playback problem on one device doesn't by itself "
                       "mean there's an outage. Is the video working now?",
             "input_type": "boolean",
             "options": [{"option_id": "yes", "label": "Yes"}, {"option_id": "no", "label": "No"}],
             "allow_skip": False, "allow_back": True},
    "message": None, "notice": None, "actions": [ACTION_STATUS],
    "status_card": {"state": "operational",
                    "summary": "The public status page currently reports: No customer impact "
                               "(checked 2:05 PM UTC). The status page has the latest updates.",
                    "checked_at": "2026-10-23T14:05:00Z", "status_url": "/status"},
}
CONSENT = {"purpose": "sales_contact", "status": "granted", "notice_version": "sales-contact-v1",
           "recorded_at": "2026-10-23T14:06:00Z", "receipt_id": "cns_0123456789abcdef01234567"}
LEAD_FLOW = {
    "flow_id": LEAD, "flow_kind": "demo", "status": "collecting", "schema_version": "lead-v1",
    "purpose_notice": {"text": "Tell us how to reach you. We'll use these details only to "
                               "respond to your demo request.",
                       "notice_version": "sales-contact-v1", "privacy_href": "/privacy"},
    "consent": {"purpose": "sales_contact", "required": True,
                "notice_version": "sales-contact-v1",
                "text": "I agree that ZoikoStream may contact me about this request using the "
                        "details I provide."},
    "fields": [{"field_id": "full_name", "label": "Full name", "type": "text", "required": True,
                "max_length": 100, "autocomplete": "name"},
               {"field_id": "work_email", "label": "Work email", "type": "email",
                "required": True, "max_length": 254, "autocomplete": "email"}],
    "missing_required": ["full_name", "work_email", "country_code"],
}
LEAD_OUTCOME = {"flow_id": LEAD, "outcome": "confirmed",
                "message": "Thanks — your request was received. The ZoikoStream team will use "
                           "the details you shared to respond.",
                "submission_reference": "GD-1A2B3C4D", "replayed": False, "actions": []}
HANDOFF = {"handoff_id": "3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f", "target": "support",
           "outcome": "route_available",
           "message": "ZoikoStream support can help with this. Use the contact page and choose "
                      "the topic that fits.",
           "actions": [{"action_id": "contact-support", "type": "handoff",
                        "label": "Contact support", "href": "/contact"}],
           "replayed": False}
DRAFT_EXAMPLES = {
    "partial": {"summary": "Fields typed so far",
                "value": {"full_name": "Alex Example", "organization": "Example Events Co"}},
}


def _ok(model, example=None, *, description: str, examples: dict | None = None,
        media: str = "application/json") -> dict:
    content = {"examples": examples} if examples else {"example": example}
    return {"model": model, "description": description, "content": {media: content}}


SUCCESS = {
    "getBootstrap": {200: _ok(S.Bootstrap, description="Public presentation config.",
                              examples={"enabled": {"value": BOOTSTRAP},
                                        "kill_switch": {"summary": "Guide disabled",
                                                        "value": BOOTSTRAP_DISABLED}}),
                     304: {"description": "Not modified (If-None-Match matched the ETag)."}},
    "createSession": {201: _ok(S.SessionCreated, SESSION_CREATED,
                               description="Session created (or the same session returned to "
                                           "the client that already holds its cookie). Sets "
                                           "the zs_guide_sid HttpOnly cookie.")},
    "updateSessionContext": {200: _ok(S.SessionBody, {**SESSION_BODY, "context_version": 2,
                                                       "suggested_intents": BOOTSTRAP[
                                                           "suggested_intents"]},
                                      description="Updated session context.")},
    "submitMessage": {200: {
        "model": S.MessageResult,
        "description": "Validated answer as JSON, or the SSE stream when Accept: "
                       "text/event-stream (API §9).",
        "content": {"application/json": {"examples": {
                        "answer": {"value": ANSWER},
                        "clarification": {"summary": "One clarifying question (C4)",
                                          "value": CLARIFY}}},
                    "text/event-stream": {"schema": {"type": "string"}, "examples": {
                        "stream": {"value": SSE_EXAMPLE},
                        "error": {"summary": "Failure after the stream started",
                                  "value": SSE_ERROR_EXAMPLE}}}}}},
    "startSupportFlow": {201: _ok(S.SupportFlow, SUPPORT_FLOW,
                                  description="The first step of the flow (or, for a replayed "
                                              "client_request_id, the same flow's current "
                                              "state).")},
    "submitSupportStep": {200: _ok(S.SupportFlow, description="The flow after the step.",
                                   examples={"next_step": {"value": SUPPORT_FLOW},
                                             "status_card": {"summary": "PB-06 with D1 status",
                                                             "value": SUPPORT_STATUS_STEP}})},
    "putConsent": {200: _ok(S.Consent, CONSENT, description="Consent receipt.")},
    "startLeadFlow": {201: _ok(S.LeadFlow, LEAD_FLOW,
                               description="Purpose notice and allowed fields, before any "
                                           "field is requested.")},
    "updateLeadFlow": {200: _ok(S.LeadFlow, LEAD_FLOW, description="The flow after the update.")},
    "saveLeadDraft": {204: {"description": "Draft held."}},
    "submitLeadFlow": {200: _ok(S.LeadOutcome, description="Known outcome.",
                                examples={"confirmed": {"value": LEAD_OUTCOME},
                                          "failed": {"value": {
                                              **LEAD_OUTCOME, "outcome": "failed",
                                              "submission_reference": None,
                                              "message": "Your request didn't go through. You "
                                                         "can try again, or use the contact "
                                                         "page directly.",
                                              "actions": [ACTION_CONTACT_SALES]}},
                                          "replayed": {"value": {**LEAD_OUTCOME,
                                                                 "replayed": True}}})},
    "createHandoff": {200: _ok(S.HandoffResult, description="Resolved route.",
                               examples={"route": {"value": HANDOFF},
                                         "replayed": {"summary": "Same Idempotency-Key",
                                                      "value": {**HANDOFF, "replayed": True}}})},
    "submitFeedback": {202: {"description": "Feedback accepted."}},
    "endSession": {204: {"description": "Session ended; the cookie is cleared."}},
}


def responses(operation_id: str) -> dict:
    out: dict = dict(SUCCESS[operation_id])
    by_status: dict[int, list[str]] = {}
    for code in ERRORS[operation_id]:
        by_status.setdefault(CODES[code][0], []).append(code)
    for status, codes in sorted(by_status.items()):
        examples = {code: {"summary": CODES[code][2],
                           "value": _SPECIAL.get((operation_id, code)) or problem_example(code)}
                    for code in codes}
        out[status] = {"model": S.Problem, "description": " / ".join(codes),
                       "content": {PROBLEM_MEDIA: {"examples": examples}}}
    return out
