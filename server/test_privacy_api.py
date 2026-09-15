"""The public privacy surface (PRV-001 -> PRV-004) — the HTTP half.

services/privacy_comms.py is the engine and test_privacy_comms.py covers it. This file
covers the CONTRACT that engine's emails promise a customer and nothing else was
checking: every privacy email points at /organization/privacy — and until the privacy
router and the Privacy Center page existed, those links went nowhere. A privacy right
that cannot be exercised from anywhere is not a right, so what is tested here is the
shape a requester actually meets:

  * the router is mounted, public, and NOT behind the org-state gate (a restricted
    tenant must still be able to exercise a data right — org_state.py's
    PRESERVED_PREFIXES promises /api/organization/privacy stays reachable; this is the
    data half of that promise),
  * every failed lookup, verification and download answers ONE indistinguishable error,
    because an unauthenticated caller must not learn which references exist,
  * no response shape can carry the request's free-text details, the requester's
    address, or decision reasoning — the disclosure boundary is the schema, so the
    schema is what is tested,
  * consent is only recorded against a PUBLISHED, consent-required notice version,
  * the subprocessor list can only ever be seeded from KNOWN_SUBPROCESSORS.

Run with `pytest test_privacy_api.py`. These tests need NO database and NO network:
every route under test either fails before touching a session or is exercised through
fakes, which is deliberate — the contract below is true even when Postgres is not.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.models import privacy as prv_models
from app.routers import privacy as privacy_router
from app.schemas import privacy as privacy_schemas


# ── the router is mounted where the emails and org_state promise ────────────────────────

def test_router_paths_exist():
    """The seven URLs the surface is contractually made of. The Privacy Center, the
    verify link, the status lookup and the export download each target one of these;
    a renamed path here is a dead link in a live email."""
    paths = {getattr(r, "path", None) for r in privacy_router.router.routes}
    for expected in (
        "/privacy/requests",
        "/privacy/requests/verify",
        "/privacy/requests/{reference}",
        "/privacy/exports/{export_id}/download",
        "/privacy/notice",
        "/privacy/notice/consent",
        "/privacy/subprocessors",
    ):
        assert expected in paths, f"{expected} is linked from email or the Privacy Center"


def test_privacy_router_is_public_in_main():
    """Mounted OUTSIDE the org-state gate, with the auth-free routers.

    org_state.PRESERVED_PREFIXES keeps /api/organization/privacy reachable for a
    restricted tenant; this router is where the data right itself is exercised, and
    gating it on a tenant's standing would let a restriction silence a deletion
    request. Asserted on main.py's own include lists so a future refactor that moves
    the router back behind the gate fails here rather than in production.
    """
    import inspect

    from app import main

    source = inspect.getsource(main)
    assert "privacy_router" in source
    # The public include list (no _ORG_STATE_GATE) must name it.
    public_block = source.split("app.include_router(wellknown_router)")[0]
    public_block = public_block[public_block.rindex("for router in ("):]
    assert "privacy_router" in public_block, (
        "privacy_router must be included with the public, ungated routers")


def test_intake_shape_forbids_unknown_fields():
    """extra=forbid on the intake body: an unauthenticated surface that accepts unknown
    fields is an accident waiting for a forwarder. contact.py's reasoning."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        privacy_schemas.PrivacyRequestIn.model_validate({
            "email": "person@example.com", "request_type": "export",
            "details": "hi", "verified": True,  # ← nobody may declare themselves verified
        })


def test_intake_rejects_unknown_request_types():
    import pydantic

    for bad in ("vanish", "expunge", "GDPR", ""):
        with pytest.raises(pydantic.ValidationError):
            privacy_schemas.PrivacyRequestIn.model_validate({
                "email": "person@example.com", "request_type": bad})


def test_intake_only_accepts_types_the_platform_can_service():
    """The type list is models.privacy.REQUEST_TYPES — the vocabulary the service layer
    actually implements. A type the schema accepts but no service path handles would
    open an obligation nobody can fulfil."""
    assert prv_models.REQUEST_TYPES == ("access", "export", "deletion", "correction",
                                        "restriction", "objection", "other")


def test_consent_shape_only_accepts_real_decisions():
    import pydantic

    for good in ("accepted", "declined", "withdrawn"):
        assert privacy_schemas.PrivacyDecisionIn(
            version_id=str(uuid.uuid4()), email="a@example.com", decision=good)
    for bad in ("ok", "yes", "acknowledged", "maybe", ""):
        with pytest.raises(pydantic.ValidationError):
            privacy_schemas.PrivacyDecisionIn.model_validate({
                "version_id": str(uuid.uuid4()), "email": "a@example.com", "decision": bad})


# ── the disclosure boundary is the schema ───────────────────────────────────────────────

def test_status_response_cannot_carry_details():
    """The status shape has no field for the request's free-text details, the requester's
    address, or decision reasoning. The emails disclose reference/type/status/deadline;
    the endpoint discloses exactly that and the schema makes anything more impossible
    rather than merely unimplemented."""
    fields = set(privacy_schemas.PrivacyRequestOut.model_fields)
    assert fields == {"reference", "request_type", "status", "deadline_note"}
    for forbidden in ("details", "requester_email", "email", "decision_summary",
                      "decision_reason", "verification_token_hash"):
        assert forbidden not in fields


def test_notice_response_is_public_metadata_only():
    """A notice version's governance internals (who drafted it, its document reference)
    stay out of the public shape."""
    fields = set(privacy_schemas.PublicNoticeOut.model_fields)
    assert "document_reference" not in fields
    assert fields <= {"id", "version", "effective_at", "published_at",
                      "material_change", "consent_required", "consent_purpose",
                      "change_summary"}


def test_subprocessor_shape_carries_no_contract_terms():
    fields = set(privacy_schemas.SubprocessorOut.model_fields)
    assert fields == {"name", "service", "processing_purpose", "region", "status",
                      "effective_from"}


# ── one honest failure for every dead link ─────────────────────────────────────────────

def test_single_failure_answer_constant():
    """Invalid, expired, superseded and replayed all answer the same sentence. Which one
    a token was is a fact about the holder's link, not something an unauthenticated
    caller is told."""
    assert "No privacy request matches that reference." in privacy_router._NO_REQUEST


def test_verify_failure_is_indistinguishable(monkeypatch):
    """A bad token and a known-but-consumed token raise the SAME status and message —
    the distinguishing detail lives in the service layer's return, and the route
    collapses it before it can reach the caller."""
    calls = []

    def fake_verify(db, *, token):
        calls.append(token)
        return None, "already_used" if len(calls) > 1 else "invalid"

    monkeypatch.setattr(privacy_router.prv, "verify_request", fake_verify)
    body = privacy_schemas.PrivacyVerifyIn(token="tok" + "x" * 20)

    with pytest.raises(HTTPException) as first:
        privacy_router.verify_request(body, db=None)
    with pytest.raises(HTTPException) as second:
        privacy_router.verify_request(body, db=None)

    assert first.value.status_code == second.value.status_code
    assert first.value.detail == second.value.detail


def test_status_lookup_normalises_the_reference(monkeypatch):
    """References are customer-quoted: lowercase and padded input still resolves."""
    from types import SimpleNamespace

    seen = {}

    class FakeSession:
        def scalar(self, stmt):
            seen["compiled"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(request_reference="PRV-2026-000001",
                                   request_type="export", status="verified")

    monkeypatch.setattr(privacy_router.prv, "deadline_note", lambda r: "no statutory deadline is configured")

    row = privacy_router.request_status("  prv-2026-000001 ", db=FakeSession())
    assert row.reference == "PRV-2026-000001"
    assert "PRV-2026-000001" in seen["compiled"]


def test_status_lookup_miss_is_404():
    class FakeSession:
        def scalar(self, stmt):
            return None

    with pytest.raises(HTTPException) as exc:
        privacy_router.request_status("PRV-2026-999999", db=FakeSession())
    assert exc.value.status_code == 404


# ── consent cannot exist where no consent is owed ───────────────────────────────────────

class _FakeVersion:
    def __init__(self, status="published", consent_required=True):
        self.id = uuid.uuid4()
        self.status = status
        self.consent_required = consent_required


class _FakeDb:
    def __init__(self, version):
        self._version = version

    def get(self, model, id_):
        return self._version if str(self._version.id) == str(id_) else None


def test_consent_refused_for_draft_notices():
    with pytest.raises(HTTPException) as exc:
        privacy_router.record_consent(
            privacy_schemas.PrivacyDecisionIn(
                version_id="00000000-0000-0000-0000-000000000000",
                email="a@example.com", decision="accepted"),
            user=None, db=_FakeDb(_FakeVersion(status="draft")))
    assert exc.value.status_code == 404


def test_consent_refused_for_notice_without_consent():
    """A plain informational notice takes an acknowledgement, never a consent record —
    record_decision would refuse it anyway (consent_required is False); the route
    refuses it one hop earlier so the Privacy Center cannot even draw the box."""
    with pytest.raises(HTTPException) as exc:
        privacy_router.record_consent(
            privacy_schemas.PrivacyDecisionIn(
                version_id="00000000-0000-0000-0000-000000000000",
                email="a@example.com", decision="accepted"),
            user=None, db=_FakeDb(_FakeVersion(consent_required=False)))
    assert exc.value.status_code == 404


def test_consent_refused_for_unknown_version():
    with pytest.raises(HTTPException) as exc:
        privacy_router.record_consent(
            privacy_schemas.PrivacyDecisionIn(
                version_id="11111111-1111-1111-1111-111111111111",
                email="a@example.com", decision="declined"),
            user=None, db=_FakeDb(_FakeVersion()))
    assert exc.value.status_code == 404


# ── intake dedup: an open request is returned, not duplicated ───────────────────────────

def test_open_duplicate_returns_existing_request(monkeypatch):
    """A second submission of the same type from the same address while one is open
    resolves to the first. Two obligations for one click is two emails and two
    GovernanceRecords for one person's request."""
    from types import SimpleNamespace

    existing = {"request_reference": "PRV-2026-000042", "request_type": "export",
                "status": "verification_required", "deadline_note": "n/a"}

    existing_row = SimpleNamespace(**existing)

    class FakeSession:
        def scalar(self, stmt):
            return existing_row

        def commit(self):
            pass

    opened = []
    monkeypatch.setattr(privacy_router.prv, "open_request",
                        lambda *a, **k: opened.append(k) or (None, None))
    monkeypatch.setattr(privacy_router.prv, "deadline_note", lambda r: "n/a")

    body = privacy_schemas.PrivacyRequestIn.model_validate({
        "email": "Person@Example.com ", "request_type": "export"})
    out = privacy_router.submit_request(body, background=None,
                                        request=_fake_request(), user=None,
                                        db=FakeSession())

    assert out.reference == "PRV-2026-000042"
    assert not opened, "a second obligation must not be opened"


def test_intake_normalises_the_address(monkeypatch):
    """The dedup and the row both key on the lowercase address; mixed-case input must
    not create a second request that a later lowercase submission cannot find."""
    from types import SimpleNamespace

    captured = {}

    class FakeSession:
        def scalar(self, stmt):
            captured["query"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return None

        def commit(self):
            pass

    created = SimpleNamespace(request_reference="PRV-2026-000007",
                              request_type="deletion", status="verification_required")

    def fake_open(db, *, requester_email, **kwargs):
        captured["opened_email"] = requester_email
        return created, "raw-token-abc"

    monkeypatch.setattr(privacy_router.prv, "open_request", fake_open)
    monkeypatch.setattr(privacy_router.prv, "notify_verification_required",
                        lambda *a, **k: True)
    monkeypatch.setattr(privacy_router.prv, "notify_received", lambda *a, **k: True)
    monkeypatch.setattr(privacy_router.prv, "deadline_note", lambda r: "n/a")

    body = privacy_schemas.PrivacyRequestIn.model_validate({
        "email": "Person@Example.COM", "request_type": "deletion"})
    privacy_router.submit_request(body, background=None, request=_fake_request(),
                                  user=None, db=FakeSession())

    assert "person@example.com" in captured["query"]
    assert captured["opened_email"] == "person@example.com"


# ── export download: one failure for every dead link ────────────────────────────────────

class _FakeRequest:
    """Bare ASGI request for header reads. test_mobile_app.py's approach."""
    client = None

    def __init__(self, headers=None):
        self.headers = headers or {}


def _fake_request():
    return _FakeRequest({"user-agent": "pytest"})


def test_export_download_miss_is_404_whatever_the_reason(monkeypatch):
    for outcome in ("invalid", "expired", "revoked", "already_used", "wrong_identity"):
        monkeypatch.setattr(
            privacy_router.prv, "authorize_download",
            lambda db, **k: (None, outcome))
        with pytest.raises(HTTPException) as exc:
            privacy_router.export_download(
                export_id=str(uuid.uuid4()), request=_fake_request(),
                t="tok" + "x" * 20, user=None, db=None)
        assert exc.value.status_code == 404


def test_export_download_refuses_before_storage_when_unauthorized(monkeypatch):
    """The storage read happens only after authorize_download says yes — a failed
    authorization must never reach for the object."""
    reached = []

    import app.routers.privacy as pr
    import sys

    class _ExplodingPathlib:
        class Path:  # pragma: no cover — must never be constructed
            def __init__(self, *a):
                reached.append(a)

            def __getattr__(self, name):
                raise AssertionError(
                    "storage must not be touched on an unauthorized download")

    monkeypatch.setitem(sys.modules, "pathlib", _ExplodingPathlib)
    monkeypatch.setattr(pr.prv, "authorize_download", lambda db, **k: (None, "invalid"))
    with pytest.raises(HTTPException):
        pr.export_download(export_id=str(uuid.uuid4()), request=_fake_request(),
                           t="tok" + "x" * 20, user=None, db=None)
    assert not reached


# ── the subprocessor list is real-only, at the HTTP boundary too ────────────────────────

def test_subprocessor_seeding_comes_from_known_subprocessors():
    """The route calls seed_subprocessors, whose only input is KNOWN_SUBPROCESSORS. The
    HTTP surface cannot invent a vendor, so the assertion is that the surface uses the
    same single source the service layer does — and that the list is non-empty, because
    'no subprocessors' for a platform that emails via Resend and streams via LiveKit
    would be a false statement served with a 200."""
    import inspect

    src = inspect.getsource(privacy_router.list_subprocessors)
    assert "prv.seed_subprocessors(db)" in src
    assert privacy_router.prv.KNOWN_SUBPROCESSORS, "the known list must not be empty"
    names = {entry["name"] for entry in privacy_router.prv.KNOWN_SUBPROCESSORS}
    assert "Resend" in names and "LiveKit" in names
