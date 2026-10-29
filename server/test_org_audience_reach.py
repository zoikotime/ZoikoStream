import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AudienceSession, Event, Organization, User
from app.services import audience as audience_svc
from app.services import geoip
from app.services import org as org_svc

UTC = timezone.utc


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _create_org(db, name="Test Org"):
    org = Organization(name=name, status="active")
    db.add(org)
    db.commit()
    user = User(
        org_id=org.id,
        full_name="Admin",
        role="org_admin",
        is_active=True,
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        username=f"admin{uuid.uuid4().hex[:8]}",
        password_hash="hash",
        email_verified=True,
    )
    db.add(user)
    db.commit()
    return org, user


def _create_event(db, org, user):
    now = datetime.now(UTC)
    ev = Event(
        org_id=org.id,
        created_by=user.id,
        title=f"Event {uuid.uuid4().hex[:6]}",
        status="ended",
        visibility="public",
        start_time=now - timedelta(days=2),
        end_time=now - timedelta(days=2, hours=-1),
    )
    db.add(ev)
    db.commit()
    return ev


def test_audience_reach_aggregation_and_device_mix(db_session):
    org, user = _create_org(db_session, "Reach Org")
    ev = _create_event(db_session, org, user)
    now = datetime.now(UTC)

    # 3 desktop sessions, 1 mobile session
    s1 = AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="vk1",
        country_code="US", device_type="desktop", browser="chrome", os="windows", client_type="web",
        joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
    )
    s2 = AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="vk2",
        country_code="US", device_type="desktop", browser="edge", os="windows", client_type="web",
        joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
    )
    s3 = AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="vk3",
        country_code="GB", device_type="desktop", browser="firefox", os="linux", client_type="web",
        joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
    )
    s4 = AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="vk4",
        country_code=None, device_type="mobile", browser="safari", os="ios", client_type="mobile",
        joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
    )
    db_session.add_all([s1, s2, s3, s4])
    db_session.commit()

    res = org_svc.analytics(db_session, org, range_key="30d")

    assert res["audience_viewers"] == 4
    # Devices: desktop 3 (75%), mobile 1 (25%)
    devices = {d["device_type"]: d for d in res["devices"]}
    assert devices["desktop"]["viewers"] == 3
    assert devices["desktop"]["percentage"] == 75
    assert devices["mobile"]["viewers"] == 1
    assert devices["mobile"]["percentage"] == 25

    # Locations: US 2 (67%), GB 1 (33%) of known (3 known)
    locs = {l["country_code"]: l for l in res["locations"]}
    assert locs["US"]["viewers"] == 2
    assert locs["GB"]["viewers"] == 1
    assert res["locations_known"] == 3
    assert res["locations_unknown"] == 1

    # Traffic sources are intentionally not tracked
    assert res["traffic_sources"] is None
    assert "Device classes are detected" in res["breakdowns_note"]
    assert "Traffic sources are not tracked" in res["breakdowns_note"]


def test_audience_reach_tenant_isolation(db_session):
    org_a, user_a = _create_org(db_session, "Org A")
    org_b, user_b = _create_org(db_session, "Org B")
    ev_a = _create_event(db_session, org_a, user_a)
    ev_b = _create_event(db_session, org_b, user_b)
    now = datetime.now(UTC)

    # Session for Org A only
    db_session.add(AudienceSession(
        org_id=org_a.id, event_id=ev_a.id, viewer_key="vka",
        country_code="US", device_type="desktop", browser="chrome", os="windows", client_type="web",
        joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
    ))
    db_session.commit()

    res_a = org_svc.analytics(db_session, org_a, range_key="30d")
    res_b = org_svc.analytics(db_session, org_b, range_key="30d")

    assert res_a["audience_viewers"] == 1
    assert len(res_a["devices"]) == 1
    assert len(res_a["locations"]) == 1

    # Org B must see 0 viewers and no data from Org A
    assert res_b["audience_viewers"] == 0
    assert res_b["devices"] == []
    assert res_b["locations"] == []
    assert res_b["breakdowns_note"] == "No audience telemetry recorded in this window."


def test_audience_reach_date_window_filtering(db_session):
    org, user = _create_org(db_session, "Window Org")
    ev = _create_event(db_session, org, user)
    now = datetime.now(UTC)

    # In 7d window (2 days ago)
    db_session.add(AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="recent",
        country_code="DE", device_type="desktop", browser="firefox", os="linux", client_type="web",
        joined_at=now - timedelta(days=2), last_seen_at=now - timedelta(days=2),
    ))
    # In 30d window (20 days ago)
    db_session.add(AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="mid",
        country_code="FR", device_type="tablet", browser="safari", os="ios", client_type="web",
        joined_at=now - timedelta(days=20), last_seen_at=now - timedelta(days=20),
    ))
    # In 90d window (50 days ago)
    db_session.add(AudienceSession(
        org_id=org.id, event_id=ev.id, viewer_key="older",
        country_code="JP", device_type="mobile", browser="safari", os="ios", client_type="mobile",
        joined_at=now - timedelta(days=50), last_seen_at=now - timedelta(days=50),
    ))
    db_session.commit()

    res_7d = org_svc.analytics(db_session, org, range_key="7d")
    assert res_7d["audience_viewers"] == 1
    assert res_7d["locations"][0]["country_code"] == "DE"

    res_30d = org_svc.analytics(db_session, org, range_key="30d")
    assert res_30d["audience_viewers"] == 2

    res_90d = org_svc.analytics(db_session, org, range_key="90d")
    assert res_90d["audience_viewers"] == 3


def test_record_join_deduplication(db_session):
    org, user = _create_org(db_session, "Dedupe Org")
    ev = _create_event(db_session, org, user)

    # Multiple join calls for the same viewer identity
    identity = "user@example.com"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
    audience_svc.record_join(db_session, ev, identity, user_agent=ua)
    audience_svc.record_join(db_session, ev, identity, user_agent=ua)

    rows = db_session.query(AudienceSession).filter_by(event_id=ev.id).all()
    assert len(rows) == 1
    assert rows[0].device_type == "desktop"
    assert rows[0].join_count == 1


def test_missing_or_unknown_geographic_data(db_session):
    org, user = _create_org(db_session, "Unknown Geo Org")
    ev = _create_event(db_session, org, user)
    now = datetime.now(UTC)

    # 2 viewers, neither provided a country
    db_session.add_all([
        AudienceSession(
            org_id=org.id, event_id=ev.id, viewer_key="u1",
            country_code=None, device_type="desktop", browser="chrome", os="windows", client_type="web",
            joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
        ),
        AudienceSession(
            org_id=org.id, event_id=ev.id, viewer_key="u2",
            country_code=None, device_type="mobile", browser="safari", os="ios", client_type="mobile",
            joined_at=now - timedelta(days=1), last_seen_at=now - timedelta(days=1),
        ),
    ])
    db_session.commit()

    res = org_svc.analytics(db_session, org, range_key="30d")
    assert res["audience_viewers"] == 2
    assert res["locations"] == []
    assert res["locations_known"] == 0
    assert res["locations_unknown"] == 2
    assert len(res["devices"]) == 2


def test_geoip_resolution_and_untrusted_header_handling(monkeypatch):
    # Disabled GeoIP does not resolve raw IP or untrusted headers
    monkeypatch.setattr(settings, "GEOIP_ENABLED", False)
    assert geoip.resolve_country("8.8.8.8", {"cf-ipcountry": "GB"}) is None
    assert geoip.resolve_country("8.8.8.8") is None

    # Valid trusted headers resolve country code ONLY when enabled and configured
    monkeypatch.setattr(settings, "GEOIP_ENABLED", True)
    monkeypatch.setattr(settings, "GEOIP_TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    assert geoip.resolve_country("8.8.8.8", {"cf-ipcountry": "GB"}) == "GB"
    assert geoip.resolve_country("8.8.8.8", {"x-country-code": "CA"}) == "CA"

    # Untrusted/invalid codes fail safely to None
    assert geoip.resolve_country("8.8.8.8", {"cf-ipcountry": "XX"}) is None
    assert geoip.resolve_country("8.8.8.8", {"cf-ipcountry": "malicious<script>"}) is None

    # Private IP addresses are ignored
    assert geoip.resolve_country("192.168.1.1") is None
    assert geoip.resolve_country("10.0.0.1") is None
    assert geoip.resolve_country("127.0.0.1") is None

