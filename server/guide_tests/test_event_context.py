"""Signed public event context (API §18, ARCH-AT-06/07, DB-AT-03)."""
import uuid

from jose import jwt
from sqlalchemy import select, text

from app.guide import event_context
from app.guide.models import GuidePublicEventContext
from app.guide.settings import get_settings

from conftest import create_session

CLAIMS = {"event_display_name": "Spring Memorial Service",
          "public_start_at": "2026-10-10T18:00:00Z", "public_timezone": "America/New_York",
          "public_status": "scheduled", "caption_languages": ["en", "es"],
          "replay_public_state": "available", "organizer_contact_route": "org-help-1"}


def _viewer_session(client, token):
    return create_session(client, page_id="viewer", page_type="viewer", path="/watch",
                          public_event_context_token=token)


def test_valid_token_stores_only_allowlisted_claims(client, db):
    token = event_context.issue({**CLAIMS, "guest_list": ["a@b.c"], "org_id": 42,
                                 "producer_notes": "secret"})
    g = _viewer_session(client, token)
    # WF-10 "Public context" card: event, status and public start time (with its zone).
    assert g.body["event_context"] == {"event_display_name": "Spring Memorial Service",
                                       "public_status": "scheduled",
                                       "public_start_at": "2026-10-10T18:00:00Z",
                                       "public_timezone": "America/New_York"}
    row = db.scalar(select(GuidePublicEventContext))
    assert set(row.safe_claims) == set(CLAIMS)
    assert "guest_list" not in row.safe_claims and "org_id" not in row.safe_claims
    dump = db.execute(text("SELECT row_to_json(e)::text FROM guide_public_event_contexts e")
                      ).scalar()
    assert token not in dump  # raw token never stored


def test_bad_signature_falls_back_to_generic(client, db):
    s = get_settings()
    forged = jwt.encode({**CLAIMS, "iss": s.GUIDE_EVENT_CONTEXT_ISSUER,
                         "aud": s.GUIDE_EVENT_CONTEXT_AUDIENCE, "iat": 1, "exp": 4102444800},
                        "wrong-key", algorithm="HS256")
    g = _viewer_session(client, forged)
    assert g.body["event_context"] is None
    assert db.scalar(select(GuidePublicEventContext)) is None


def test_expired_wrong_audience_and_overlong_tokens_are_ignored():
    s = get_settings()
    assert event_context.verify(event_context.issue(CLAIMS, ttl_seconds=-10)) is None
    wrong_aud = jwt.encode({**CLAIMS, "iss": s.GUIDE_EVENT_CONTEXT_ISSUER, "aud": "other",
                            "iat": 1, "exp": 4102444800}, s.GUIDE_EVENT_CONTEXT_KEY)
    assert event_context.verify(wrong_aud) is None
    assert event_context.verify(event_context.issue(CLAIMS, ttl_seconds=48 * 3600)) is None
    assert event_context.verify("not-a-jwt") is None


def test_invalid_claim_values_are_dropped():
    v = event_context.verify(event_context.issue({
        "event_display_name": "Ok", "public_status": "producer_ready",
        "public_start_at": "not-a-date", "organizer_contact_route": "javascript:alert(1)"}))
    assert v.safe_claims == {"event_display_name": "Ok"}


def test_session_without_token_has_no_event_context(guide):
    assert guide.body["event_context"] is None
    assert uuid.UUID(guide.session_id)


# ── Platform issuer: claims from real event data (PR-023; API §18) ────────────────────────

def _event(**kw):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    base = dict(id=uuid.uuid4(), title="Spring Memorial Service",
                start_time=datetime(2026, 10, 10, 18, tzinfo=timezone.utc),
                timezone="America/New_York", recording_enabled=True,
                organization=SimpleNamespace(support_email="help@example.org"))
    base.update(kw)
    return SimpleNamespace(**base)


def _issuer(monkeypatch, viewer_status="ended", entitlement=None, expired=False):
    from app.routers import guide_event_context as gec
    monkeypatch.setattr(gec.crud, "viewer_status_of", lambda ev: viewer_status)
    monkeypatch.setattr(gec.commercial_crud, "get_replay_entitlement",
                        lambda db, eid, scope="audience": entitlement)
    monkeypatch.setattr(gec.commercial_crud, "replay_access_expired", lambda ent: expired)
    return gec


def _ent(publish_state, watermark_status="not_applicable"):
    from types import SimpleNamespace
    return SimpleNamespace(publish_state=publish_state, watermark_status=watermark_status)


def test_issuer_claims_survive_guide_verification_and_omit_unknowns(monkeypatch):
    gec = _issuer(monkeypatch, entitlement=_ent("published", "ready"))
    claims = gec.claims_for(_event(), db=object())
    assert claims["replay_public_state"] == "available"
    assert claims["organizer_contact_route"] == "organizer-support"
    assert "help@example.org" not in str(claims)  # the address itself never leaves
    # No persisted caption setting exists, so caption claims are omitted, not guessed.
    assert "captions_available" not in claims and "caption_languages" not in claims
    verified = event_context.verify(event_context.issue(claims))
    assert verified.safe_claims == claims  # every issued claim passes Guide's allowlist


def test_issuer_replay_state_mapping(monkeypatch):
    cases = [(dict(entitlement=_ent("published", "pending")), {}, "pending"),
             (dict(entitlement=_ent("validating")), {}, "pending"),
             (dict(entitlement=_ent("published", "ready"), expired=True), {}, "unavailable"),
             (dict(entitlement=None), {}, "unavailable"),               # ended, nothing
             (dict(entitlement=None, viewer_status="scheduled"), {}, "unknown"),
             (dict(entitlement=None), {"recording_enabled": False}, "unavailable")]
    for issuer_kw, event_kw, expected in cases:
        gec = _issuer(monkeypatch, **issuer_kw)
        assert gec.claims_for(_event(**event_kw), db=object())["replay_public_state"] \
            == expected, (issuer_kw, event_kw)


def test_issuer_omits_organizer_route_without_public_contact(monkeypatch):
    from types import SimpleNamespace
    gec = _issuer(monkeypatch)
    for org in (None, SimpleNamespace(support_email=None), SimpleNamespace(support_email=" ")):
        assert "organizer_contact_route" not in gec.claims_for(_event(organization=org),
                                                               db=object())
