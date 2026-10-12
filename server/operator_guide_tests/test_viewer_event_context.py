"""The public Guide's event-context issuer (routers/guide_event_context.py).

Lives in this suite because it needs the platform database: the issuer applies the watch
page's access rules to real events, registrations and access links.
"""
import pytest

from app.guide import event_context
from app.guide.settings import get_settings as guide_settings

PATH = "/api/guide/event-context"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("GUIDE_EVENT_CONTEXT_KEY", "test-event-context-key-0123456789abcdef")
    guide_settings.cache_clear()
    yield
    guide_settings.cache_clear()


def test_public_event_issues_only_the_spec_claim_set(client, world):
    from datetime import timedelta
    org = world.org()
    admin = world.user(org, full_name="Priya Host")
    ev = world.event(org, admin, title="Town Hall", visibility="public",
                     description="Quarterly update.", timezone="Asia/Kolkata")
    ev.end_time = ev.start_time + timedelta(minutes=90)
    world.db.commit()
    res = client.get(f"{PATH}/{ev.id}")
    assert res.status_code == 200, res.text
    claims = event_context.verify(res.json()["token"]).safe_claims
    # Conversation Flow §16: public name, start time/zone, status, replay state — nothing else.
    assert set(claims) <= {"event_display_name", "public_start_at", "public_timezone",
                           "public_status", "replay_public_state", "locale"}
    assert claims["event_display_name"] == "Town Hall"
    assert claims["public_timezone"] == "Asia/Kolkata"
    assert claims["replay_public_state"] == "unavailable"  # recording is off


def test_private_event_is_refused_to_strangers(client, world):
    org = world.org()
    ev = world.event(org, world.user(org), visibility="private")
    assert client.get(f"{PATH}/{ev.id}").status_code == 403
    # A forged invite token does not help either.
    assert client.get(f"{PATH}/{ev.id}", params={"reg": "not-a-token"}).status_code == 403


def test_private_event_is_issued_to_organization_members(client, world):
    org = world.org()
    host = world.user(org, "host")
    ev = world.event(org, host, visibility="private")
    assert client.get(f"{PATH}/{ev.id}", headers=world.headers(host)).status_code == 200


def test_drafts_and_unknown_events_do_not_exist(client, world):
    import uuid
    org = world.org()
    draft = world.event(org, world.user(org), status="draft")
    assert client.get(f"{PATH}/{draft.id}").status_code == 404
    assert client.get(f"{PATH}/{uuid.uuid4()}").status_code == 404
