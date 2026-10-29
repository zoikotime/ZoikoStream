"""Generate the committed public Guide contract artifacts (API §22-23, App. C/D).

    python -m app.guide.openapi.export           # (re)write both files
    python -m app.guide.openapi.export --check   # exit 1 if the committed files drift

The OpenAPI 3.1 document is generated FROM the running app's routes (so it cannot silently
diverge from the code), then normalised for the public contract:
  * error responses are application/problem+json only (API §5) — FastAPI's default 422
    HTTPValidationError becomes the GUIDE_VALIDATION_ERROR problem;
  * X-ZS-CSRF is a REQUIRED header parameter on every session route (API §3.1, §22) — the
    app declares it optional only so a missing header answers 401, not a schema 400;
  * the server URL is the logical base path; internal routes are never included.
The Postman v2.1 collection is derived from that document (API §23).

guide_tests/test_a5_openapi_contract.py fails when either committed file differs from what
this module generates, and reports breaking changes (removed operations, removed responses,
newly required inputs) separately so a reviewer sees them called out.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OPENAPI_FILE = HERE / "public-guide.openapi.yaml"
POSTMAN_FILE = HERE / "public-guide.postman_collection.json"
BASE_PATH = "/api/v1/public-guide"
PROBLEM_REF = {"$ref": "#/components/schemas/Problem"}

DESCRIPTION = (
    "Public, anonymous ZoikoStream Guide API (ZST-API-PUB-GUIDE-001 v1.0). The browser holds "
    "an opaque HttpOnly session capability cookie (zs_guide_sid) plus a per-session CSRF token "
    "sent as X-ZS-CSRF on every mutating session route; there is no static client API key. "
    "Errors are RFC 9457 problem details with stable codes. Generated from the application by "
    "`python -m app.guide.openapi.export`; do not edit by hand.")


# ── OpenAPI ────────────────────────────────────────────────────────────────────────────────

def _app_spec() -> dict:
    from ..asgi import create_app
    return copy.deepcopy(create_app().openapi())


def build_spec() -> dict:
    spec = _app_spec()
    spec["info"] = {"title": "ZoikoStream Public Guide API", "version": "1.0.0",
                    "description": DESCRIPTION}
    spec["servers"] = [{"url": BASE_PATH, "description": "Logical base path (API §2)"}]
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            _normalise_operation(op)
    schemas = spec.get("components", {}).get("schemas", {})
    for unused in ("HTTPValidationError", "ValidationError"):
        if unused in schemas and unused not in json.dumps(spec["paths"]):
            schemas.pop(unused)
    return spec


def _normalise_operation(op: dict) -> None:
    for status, resp in list(op.get("responses", {}).items()):
        if not status.isdigit():
            continue
        if int(status) < 400:
            # Bodiless successes (202/204): FastAPI adds an empty JSON schema; drop it.
            content = resp.get("content", {})
            if content and all(m.get("schema") in ({}, None) and "example" not in m
                               and "examples" not in m for m in content.values()):
                resp.pop("content")
            continue
        content = resp.setdefault("content", {})
        json_schema = content.pop("application/json", None)
        problem = content.setdefault("application/problem+json", {})
        problem["schema"] = PROBLEM_REF
        if json_schema and "HTTPValidationError" in json.dumps(json_schema):
            resp["description"] = "GUIDE_VALIDATION_ERROR"
            problem.setdefault("examples", {"GUIDE_VALIDATION_ERROR": {
                "summary": "Request validation failed",
                "value": _validation_problem()}})
    for param in op.get("parameters", []):
        if param.get("in") == "header" and param.get("name") == "X-ZS-CSRF":
            param["required"] = True
            param["schema"] = {"type": "string", "minLength": 16,
                               "description": param.get("description", "")}


def _validation_problem() -> dict:
    from .docs import problem_example
    return problem_example("GUIDE_VALIDATION_ERROR",
                           field_errors=[{"field": "page_id", "code": "string_pattern_mismatch",
                                          "message": "Value is invalid."}])


def spec_yaml(spec: dict | None = None) -> str:
    return yaml.safe_dump(spec or build_spec(), sort_keys=False, allow_unicode=True, width=100)


# ── Breaking-change check (API §21, §22 "CI must ... detect breaking changes") ─────────────

def breaking_changes(old: dict, new: dict) -> list[str]:
    problems = []
    for path, ops in old.get("paths", {}).items():
        for method, op in ops.items():
            new_op = new.get("paths", {}).get(path, {}).get(method)
            where = f"{method.upper()} {path}"
            if new_op is None:
                problems.append(f"{where}: operation removed")
                continue
            for status in op.get("responses", {}):
                if status not in new_op.get("responses", {}):
                    problems.append(f"{where}: response {status} removed")
            old_required = {(p["in"], p["name"]) for p in op.get("parameters", [])
                            if p.get("required")}
            for p in new_op.get("parameters", []):
                if p.get("required") and (p["in"], p["name"]) not in old_required:
                    problems.append(f"{where}: new required {p['in']} parameter {p['name']}")
            if new_op.get("requestBody", {}).get("required") and not op.get(
                    "requestBody", {}).get("required"):
                problems.append(f"{where}: request body became required")
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for name, schema in old_schemas.items():
        if name not in new_schemas:
            continue
        added = set(new_schemas[name].get("required", [])) - set(schema.get("required", []))
        if added and name in _REQUEST_SCHEMAS(old):
            problems.append(f"schema {name}: newly required request fields {sorted(added)}")
    return problems


def _REQUEST_SCHEMAS(spec: dict) -> set[str]:  # noqa: N802 - reads like a constant
    names = set()
    for ops in spec.get("paths", {}).values():
        for op in ops.values():
            ref = (op.get("requestBody", {}).get("content", {}).get("application/json", {})
                   .get("schema", {}).get("$ref", ""))
            if ref:
                names.add(ref.rsplit("/", 1)[-1])
    return names


# ── Postman v2.1 ───────────────────────────────────────────────────────────────────────────

ORDER = ["getBootstrap", "createSession", "updateSessionContext", "submitMessage",
         "startSupportFlow", "submitSupportStep", "putConsent", "startLeadFlow",
         "saveLeadDraft", "updateLeadFlow", "submitLeadFlow", "createHandoff", "submitFeedback",
         "endSession"]
PATH_VARS = {"session_id": "{{sessionId}}", "lead_flow_id": "{{leadFlowId}}",
             "purpose": "sales_contact"}
BODIES = {
    "createSession": {"client_session_nonce": "{{$guid}}", "page_context": {
        "page_id": "home", "page_type": "home", "path": "/", "locale": "en-US",
        "device_class": "desktop", "entry_point": "launcher"},
        "consent_snapshot": {"analytics": "unknown", "consent_version": None}},
    "updateSessionContext": {"page_context": {"page_id": "pricing", "page_type": "pricing",
                                              "path": "/contact", "locale": "en-US"}},
    "submitMessage": {"client_message_id": "{{$guid}}",
                      "text": "Can ZoikoStream help us stream a memorial service?",
                      "intent_hint": "I-SOLUTION"},
    "startSupportFlow": {"flow_type": "viewer_access", "client_request_id": "{{$guid}}"},
    "submitSupportStep": {"client_step_id": "{{$guid}}", "step_id": "invitation_source",
                          "option_id": "email_invitation"},
    "putConsent": {"status": "granted", "notice_version": "sales-contact-v1", "source": "guide"},
    "startLeadFlow": {"flow_kind": "demo", "source_intent": "I-DEMO"},
    "saveLeadDraft": {"full_name": "Alex Example"},
    "updateLeadFlow": {"fields": {"full_name": "Alex Example",
                                  "work_email": "alex@example.com", "country_code": "US"}},
    "submitLeadFlow": {"consent_receipt_id": "{{consentReceiptId}}"},
    "createHandoff": {"target": "support", "reason_code": "user_requested"},
    "submitFeedback": {"client_feedback_id": "{{$guid}}", "target_type": "session",
                       "rating": "up", "reason_code": "helpful"},
}
CAPTURE = {
    "createSession": ["pm.collectionVariables.set('sessionId', pm.response.json().session_id);",
                      "pm.collectionVariables.set('csrfToken', pm.response.json().csrf_token);"],
    "startSupportFlow": ["pm.collectionVariables.set('supportFlowId', "
                         "pm.response.json().flow_id);"],
    "startLeadFlow": ["pm.collectionVariables.set('leadFlowId', pm.response.json().flow_id);"],
    "putConsent": ["pm.collectionVariables.set('consentReceiptId', "
                   "pm.response.json().receipt_id);"],
}


def _url(path: str, op_id: str) -> dict:
    flow_var = "{{supportFlowId}}" if "support-flows" in path else "{{leadFlowId}}"
    segments = []
    for seg in path.strip("/").split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            name = seg[1:-1]
            seg = PATH_VARS.get(name, flow_var if name == "flow_id" else f"{{{{{name}}}}}")
        segments.append(seg)
    raw = "{{baseUrl}}/" + "/".join(segments)
    query = []
    if op_id == "getBootstrap":
        query = [{"key": "page_id", "value": "home"}, {"key": "page_type", "value": "home"},
                 {"key": "locale", "value": "en-US"}]
        raw += "?page_id=home&page_type=home&locale=en-US"
    url = {"raw": raw, "host": ["{{baseUrl}}"], "path": segments}
    if query:
        url["query"] = query
    return url


def build_postman(spec: dict | None = None) -> dict:
    spec = spec or build_spec()
    ops = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            ops[op["operationId"]] = (path, method, op)
    folders: dict[str, list] = {}
    for op_id in ORDER + sorted(set(ops) - set(ORDER)):
        path, method, op = ops[op_id]
        headers = [{"key": "Accept", "value": "application/json"}]
        if op_id != "getBootstrap":
            headers.append({"key": "Origin", "value": "{{origin}}"})
        if any(p.get("name") == "X-ZS-CSRF" for p in op.get("parameters", [])):
            headers.append({"key": "X-ZS-CSRF", "value": "{{csrfToken}}"})
        if any(p.get("name") == "Idempotency-Key" for p in op.get("parameters", [])):
            headers.append({"key": "Idempotency-Key", "value": "{{$guid}}"})
        request = {"method": method.upper(), "header": headers, "url": _url(path, op_id),
                   "description": op.get("summary", "")}
        if op_id in BODIES:
            headers.append({"key": "Content-Type", "value": "application/json"})
            request["body"] = {"mode": "raw", "raw": json.dumps(BODIES[op_id], indent=2),
                               "options": {"raw": {"language": "json"}}}
        success = next(s for s in op["responses"] if s.startswith("2"))
        tests = [f"pm.test('{op_id} succeeds', function () {{ "
                 f"pm.response.to.have.status({int(success)}); }});"]
        tests += CAPTURE.get(op_id, [])
        item = {"name": op_id, "request": request,
                "event": [{"listen": "test", "script": {"type": "text/javascript",
                                                        "exec": tests}}]}
        folders.setdefault(op["tags"][0], []).append(item)
    return {
        "info": {"name": "ZoikoStream Public Guide API v1.0",
                 "description": "Executable companion to public-guide.openapi.yaml (API §23). "
                                "Run in order; the cookie jar keeps the HttpOnly session "
                                "cookie. Example identity data is synthetic.",
                 "schema": "https://schema.getpostman.com/json/collection/v2.1.0/"
                           "collection.json"},
        "variable": [{"key": k, "value": v} for k, v in (
            ("baseUrl", "http://localhost:8000" + BASE_PATH), ("origin", "http://localhost:5173"),
            ("sessionId", ""), ("csrfToken", ""), ("supportFlowId", ""), ("leadFlowId", ""),
            ("consentReceiptId", ""))],
        "item": [{"name": tag, "item": items} for tag, items in folders.items()],
    }


def postman_json(spec: dict | None = None) -> str:
    return json.dumps(build_postman(spec), indent=2, ensure_ascii=False) + "\n"


# ── CLI ────────────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed artifacts differ from the generated ones")
    args = parser.parse_args(argv)
    spec = build_spec()
    generated = {OPENAPI_FILE: spec_yaml(spec), POSTMAN_FILE: postman_json(spec)}
    if not args.check:
        for path, text in generated.items():
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.name}")
        return 0
    status = 0
    if OPENAPI_FILE.exists():
        for problem in breaking_changes(yaml.safe_load(OPENAPI_FILE.read_text("utf-8")), spec):
            print(f"BREAKING: {problem}")
            status = 1
    for path, text in generated.items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            print(f"DRIFT: {path.name} differs from the generated contract; run "
                  "`python -m app.guide.openapi.export`")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
