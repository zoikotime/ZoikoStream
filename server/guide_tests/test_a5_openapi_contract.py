"""OpenAPI 3.1 contract and Postman companion (API §22-23, App. A-D).

The committed artifacts must equal what `python -m app.guide.openapi.export` generates from
the app; a drift fails here with the breaking changes listed first."""
import json

import yaml

from app.guide.errors import CODES
from app.guide.openapi import docs, export


def _committed_spec():
    return yaml.safe_load(export.OPENAPI_FILE.read_text(encoding="utf-8"))


def test_committed_openapi_has_no_breaking_changes_against_the_app():
    assert export.breaking_changes(_committed_spec(), export.build_spec()) == []


def test_committed_artifacts_match_generated():
    spec = export.build_spec()
    assert export.OPENAPI_FILE.read_text(encoding="utf-8") == export.spec_yaml(spec), \
        "public-guide.openapi.yaml drifted: run `python -m app.guide.openapi.export`"
    assert export.POSTMAN_FILE.read_text(encoding="utf-8") == export.postman_json(spec), \
        "public-guide.postman_collection.json drifted: run the export"


def test_contract_shape():
    spec = export.build_spec()
    assert spec["openapi"].startswith("3.1")
    ops = [(m, p, op) for p, item in spec["paths"].items() for m, op in item.items()]
    assert len(ops) == 14  # API §2's 13 operations + the C6 lead draft
    assert not any("/health" in p or "/internal" in p for _, p, _ in ops)  # internal routes
    scheme = spec["components"]["securitySchemes"]["GuideSessionCookie"]
    assert scheme == {**scheme, "type": "apiKey", "in": "cookie", "name": "zs_guide_sid"}
    for method, path, op in ops:
        assert op["operationId"] and op["tags"], path
        session_route = path.startswith("/sessions/")
        if session_route:
            assert op["security"] == [{"GuideSessionCookie": []}], path
            csrf = [p for p in op["parameters"] if p["name"] == "X-ZS-CSRF"]
            assert csrf and csrf[0]["in"] == "header" and csrf[0]["required"], path
        for status, resp in op["responses"].items():
            content = resp.get("content", {})
            if int(status) >= 400:
                assert list(content) == ["application/problem+json"], (path, status)
                assert content["application/problem+json"]["examples"], (path, status)
            elif content:
                media = next(iter(content.values()))
                assert "example" in media or "examples" in media, (path, status)


def test_every_documented_error_code_has_an_example():
    spec = export.build_spec()
    by_id = {op["operationId"]: op for item in spec["paths"].values() for op in item.values()}
    for op_id, codes in docs.ERRORS.items():
        for code in codes:
            status = str(CODES[code][0])
            examples = by_id[op_id]["responses"][status]["content"][
                "application/problem+json"]["examples"]
            assert examples[code]["value"]["code"] == code, (op_id, code)


def test_answer_schema_carries_clarification_status_card_and_paused():
    schemas = export.build_spec()["components"]["schemas"]
    assert "clarification" in schemas["AssistantMessage"]["properties"]
    assert "paused" in schemas["AssistantMessage"]["properties"]
    assert "status_card" in schemas["SupportFlow"]["properties"]
    assert set(schemas["Clarification"]["required"]) == {"question", "options"}


def test_postman_collection_v21_with_required_variables():
    col = json.loads(export.POSTMAN_FILE.read_text(encoding="utf-8"))
    assert col["info"]["schema"].endswith("v2.1.0/collection.json")
    names = {v["key"] for v in col["variable"]}
    assert {"baseUrl", "sessionId", "csrfToken", "supportFlowId", "leadFlowId",
            "consentReceiptId"} <= names
    items = {i["name"]: i for folder in col["item"] for i in folder["item"]}
    assert len(items) == 14
    capture = "\n".join(items["createSession"]["event"][0]["script"]["exec"])
    assert "sessionId" in capture and "csrfToken" in capture
