"""Event Workspace Activity & Audit (ZST-ENG-DASH-001 EVT-07) and its read models.

  * Every management action on an event leaves a receipt with the event, the organization, the
    actor's role at the time, the server's decision and the request reference the response
    carried. A refused attempt is recorded as refused and changes nothing (EVT-07 QA B).
  * The reader is scoped by the token's organization: another organization's event, entry or
    filter value reads as not found (QA A); a non-admin is refused.
  * The reader shows codes, counts and statuses — never emails, IPs, tokens, URLs or text a
    person typed (notes 4) — and never a staff member's identity.
  * A refused attempt is never titled as the change it did not make (QA D), nothing is
    fabricated for an event that never went live (QA E), and integrity is reported as not
    established rather than passed (QA F).
  * Insights, Live Control and Preparation report unmeasured figures as null, not 0.
  * The one-off backfill gives rows written before the columns existed their event and outcome.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, insert, select, text
from starlette.testclient import TestClient

import app.main as m
from app import email as email_mod
from app.db import SessionLocal
from app.models import (
    AudienceSession,
    AuditLog,
    BroadcastSession,
    Event,
    EventAccessLink,
    EventFeedback,
    Organization,
    User,
)
from app.models.audit_log import outcome_for_action
from app.request_context import bind_request_id, reset_request_id
from app.security import create_access_token, hash_password
from app.services import activity_log, bus

UTC = timezone.utc
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def no_mail_and_in_process_bus(monkeypatch):
    # .env carries a live Resend key; nothing in this file may send real mail.
    monkeypatch.setattr(email_mod.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(bus.settings, "REDIS_URL", "")


@pytest.fixture
def world():
    db = SessionLocal()
    orgs, users, events = [], [], []

    def org(name="Org"):
        o = Organization(name=f"{name} {uuid.uuid4().hex[:6]}", status="active")
        db.add(o)
        db.flush()
        orgs.append(o.id)
        return o

    def user(o, role="org_admin", name="Admin Person"):
        u = User(org_id=o.id, full_name=name, role=role, is_active=True,
                 email=f"audit-{uuid.uuid4().hex[:10]}@example.com",
                 username=f"aud{uuid.uuid4().hex[:10]}", password_hash=hash_password("x"),
                 email_verified=True)
        db.add(u)
        db.flush()
        users.append(u.id)
        return u

    def event(o, u, status="draft", title="Quarterly town hall", **kw):
        e = Event(org_id=o.id, created_by=u.id, title=title, status=status, visibility="public",
                  timezone="Europe/London", **kw)
        db.add(e)
        db.flush()
        events.append(e.id)
        return e

    ns = type("W", (), {})()
    ns.db, ns.org, ns.user, ns.event = db, org, user, event
    try:
        yield ns
    finally:
        db.rollback()
        ids = events or [uuid.uuid4()]
        db.execute(delete(AuditLog).where(AuditLog.org_id.in_(orgs or [uuid.uuid4()])))
        db.execute(delete(AuditLog).where(AuditLog.event_id.in_(ids)))
        db.execute(delete(AuditLog).where(AuditLog.actor_id.in_(users or [uuid.uuid4()])))
        for model in (EventAccessLink, AudienceSession, EventFeedback, BroadcastSession):
            db.execute(delete(model).where(model.event_id.in_(ids)))
        db.execute(delete(Event).where(Event.id.in_(ids)))
        db.execute(delete(User).where(User.id.in_(users or [uuid.uuid4()])))
        db.execute(delete(Organization).where(Organization.id.in_(orgs or [uuid.uuid4()])))
        db.commit()
        db.close()


def client_for(u):
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_access_token(u, remember=False)}"
    return c


def rows_for(db, event_id):
    db.expire_all()
    return db.scalars(select(AuditLog).where(AuditLog.event_id == event_id)
                      .order_by(AuditLog.created_at)).all()


# ── the row itself ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action,outcome", [
    ("commercial.golive.blocked", "denied"),
    ("support_access.denied_out_of_scope", "denied"),
    ("subscription.transition_rejected", "rejected"),
    ("commercial.lifecycle.illegal_transition", "rejected"),
    ("subscription.invoice_payment_failed", "failed"),
    ("retention.extension_request", "pending"),
    ("commercial.override.requested", "pending"),
    ("event.update", "applied"),
])
def test_outcome_is_derived_from_the_action_when_a_writer_does_not_say(action, outcome):
    assert outcome_for_action(action) == outcome


def test_insert_listener_derives_event_outcome_and_request_reference(world):
    o = world.org()
    u = world.user(o)
    ev = world.event(o, u)
    token = bind_request_id("req_testreference0001")
    try:
        row = AuditLog(actor_id=u.id, action="live.recording.start", org_id=o.id,
                       meta={"event_id": str(ev.id)})
        world.db.add(row)
        world.db.commit()
    finally:
        reset_request_id(token)
    world.db.refresh(row)
    assert row.event_id == ev.id, "the event comes from meta when the writer predates the column"
    assert row.outcome == "applied"
    assert row.correlation_id == "req_testreference0001"

    by_target = AuditLog(actor_id=u.id, action="commercial.golive.blocked", org_id=o.id,
                         target_type="event", target_id=str(ev.id))
    world.db.add(by_target)
    world.db.commit()
    world.db.refresh(by_target)
    assert by_target.event_id == ev.id and by_target.outcome == "denied"


def test_backfill_completes_rows_written_before_the_columns_existed(world):
    import create_tables

    o = world.org()
    u = world.user(o)
    ev = world.event(o, u)
    # Core inserts bypass the ORM listener: these are rows as an older build wrote them.
    ids = [uuid.uuid4(), uuid.uuid4()]
    world.db.execute(insert(AuditLog.__table__), [
        {"id": ids[0], "actor_id": u.id, "action": "live.broadcast.golive", "org_id": o.id,
         "meta": {"event_id": str(ev.id)}, "target_type": None, "target_id": None,
         "event_id": None, "outcome": None},
        {"id": ids[1], "actor_id": u.id, "action": "commercial.golive.blocked", "org_id": o.id,
         "meta": None, "target_type": "event", "target_id": str(ev.id),
         "event_id": None, "outcome": None},
    ])
    world.db.commit()
    for stmt in create_tables._AUDIT_LOG_STATEMENTS:   # noqa: SLF001 - the migration under test
        world.db.execute(text(stmt))
    world.db.commit()
    world.db.expire_all()
    got = {r.id: r for r in world.db.scalars(select(AuditLog).where(AuditLog.id.in_(ids)))}
    assert got[ids[0]].event_id == ev.id and got[ids[0]].outcome == "applied"
    assert got[ids[1]].event_id == ev.id and got[ids[1]].outcome == "denied"
    # Idempotent: a second run changes nothing and does not fail.
    for stmt in create_tables._AUDIT_LOG_STATEMENTS:   # noqa: SLF001
        world.db.execute(text(stmt))
    world.db.commit()


def test_console_state_reports_the_role_and_gates_developers_on_actual_use(world):
    o = world.org()
    u = world.user(o)
    world.db.commit()
    body = client_for(u).get("/api/organization/console-state").json()
    assert body["user"]["role"] == "org_admin"
    assert body["features"] == {"developers": False}, "off by default (ZST-ENG-DASH-001 §3.2)"

    o.api_keys = [{"id": "k1", "name": "CI", "prefix": "zk_live_ab"}]
    world.db.commit()
    assert client_for(u).get("/api/organization/console-state").json()["features"]["developers"] is True


def test_every_response_carries_a_server_generated_request_reference(world):
    o = world.org()
    u = world.user(o)
    world.db.commit()
    c = client_for(u)
    res = c.get("/api/organization/console-state", headers={"X-Request-Id": "attacker-chosen"})
    ref = res.headers.get("x-request-id")
    assert ref and ref.startswith("req_") and ref != "attacker-chosen"


# ── receipts and refusals from the events router ─────────────────────────────────────────

def test_a_refused_edit_is_recorded_with_its_reason_and_changes_nothing(world):
    o = world.org()
    admin = world.user(o)
    host = world.user(o, role="host", name="Unassigned Host")
    ev = world.event(o, admin)
    world.db.commit()

    res = client_for(host).patch(f"/api/events/{ev.id}", json={"title": "Hijacked"})
    assert res.status_code == 403
    world.db.refresh(ev)
    assert ev.title == "Quarterly town hall"

    [row] = [r for r in rows_for(world.db, ev.id) if r.action == "event.update"]
    assert row.outcome == "denied"
    assert row.actor_id == host.id and row.actor_role == "host"
    assert row.org_id == o.id
    assert row.meta == {"reason_code": "not_event_host"}
    assert row.correlation_id == res.headers["x-request-id"], "the receipt names the request"


def test_an_applied_edit_records_field_names_never_values(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin)
    world.db.commit()

    res = client_for(admin).patch(f"/api/events/{ev.id}", json={"title": "Renamed town hall"})
    assert res.status_code == 200, res.text
    [row] = [r for r in rows_for(world.db, ev.id) if r.action == "event.update"]
    assert row.outcome == "applied" and row.actor_role == "org_admin"
    assert row.meta == {"fields": ["title"]}
    assert "Renamed town hall" not in str(row.meta)


def test_an_illegal_transition_is_recorded_as_rejected_with_both_statuses(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="draft")
    world.db.commit()

    res = client_for(admin).patch(f"/api/events/{ev.id}", json={"status": "live"})
    assert res.status_code == 400
    world.db.refresh(ev)
    assert ev.status == "draft"
    [row] = [r for r in rows_for(world.db, ev.id) if r.action == "event.status.change"]
    assert row.outcome == "rejected"
    assert row.meta["from_status"] == "draft" and row.meta["to_status"] == "live"
    assert row.meta["reason_code"] == "transition_not_allowed"


def test_archive_records_once_and_a_repeated_click_records_nothing(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="ended")
    world.db.commit()
    c = client_for(admin)

    assert c.post(f"/api/events/{ev.id}/archive").status_code == 200
    assert c.post(f"/api/events/{ev.id}/archive").status_code == 200   # duplicate submission
    archived = [r for r in rows_for(world.db, ev.id) if r.action == "event.archive"]
    assert len(archived) == 1
    assert archived[0].meta == {"from_status": "ended", "to_status": "archived"}


def test_access_link_receipts_never_carry_the_token_url_or_label(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="scheduled", start_time=NOW + timedelta(days=3))
    world.db.commit()
    c = client_for(admin)

    issued = c.post(f"/api/events/{ev.id}/access-links",
                    json={"label": "Board members only", "expires_in_days": 7})
    assert issued.status_code == 201, issued.text
    link = issued.json()
    assert c.post(f"/api/events/{ev.id}/access-links/{link['id']}/revoke").status_code == 200

    rows = [r for r in rows_for(world.db, ev.id) if r.action.startswith("event.access_link.")]
    assert [r.action for r in rows] == ["event.access_link.create", "event.access_link.revoke"]
    stored = str([r.meta for r in rows])
    assert "Board members only" not in stored and link["url"] not in stored

    ledger = c.get(f"/api/organization/events/{ev.id}/activity").json()
    shown = str(ledger)
    assert "Board members only" not in shown and link["url"] not in shown
    assert {i["action"] for i in ledger["items"]} >= {"event.access_link.create", "event.access_link.revoke"}


# ── the reader: scope and permission ─────────────────────────────────────────────────────

def test_another_organizations_event_entry_and_filter_all_read_as_not_found(world):
    a, b = world.org("A"), world.org("B")
    admin_a, admin_b = world.user(a), world.user(b)
    ev = world.event(a, admin_a)
    world.db.commit()
    ca, cb = client_for(admin_a), client_for(admin_b)
    assert ca.patch(f"/api/events/{ev.id}", json={"title": "A only"}).status_code == 200
    entry_id = ca.get(f"/api/organization/events/{ev.id}/activity").json()["items"][0]["id"]

    assert cb.get(f"/api/organization/events/{ev.id}/activity").status_code == 404
    assert cb.get(f"/api/organization/events/{ev.id}/activity/{entry_id}").status_code == 404
    assert cb.get(f"/api/organization/activity?event_id={ev.id}").status_code == 404
    assert cb.get(f"/api/organization/activity/{entry_id}").status_code == 404
    assert all(i["id"] != entry_id for i in cb.get("/api/organization/activity").json()["items"])
    # Cross-org probes write no receipt into either organization.
    assert all(r.org_id == a.id for r in rows_for(world.db, ev.id))


def test_audit_read_is_organization_admin_only_and_rechecked_per_request(world):
    o = world.org()
    admin = world.user(o)
    host = world.user(o, role="host", name="Host Person")
    ev = world.event(o, admin)
    world.db.commit()
    assert client_for(host).get(f"/api/organization/events/{ev.id}/activity").status_code == 403

    c = client_for(admin)
    assert c.get(f"/api/organization/events/{ev.id}/activity").status_code == 200
    # Permission revoked mid-session (EVT-07 QA G): the next detail/list request is refused.
    admin.role = "host"
    world.db.commit()
    assert c.get(f"/api/organization/events/{ev.id}/activity").status_code == 403


# ── the reader: filters, pagination, redaction, identity ─────────────────────────────────

def _seed(world, o, ev, actor, rows):
    for action, kw in rows:
        world.db.add(AuditLog(actor_id=actor.id if actor else None, actor_role=getattr(actor, "role", None),
                              action=action, org_id=o.id, event_id=ev.id, target_type="event",
                              target_id=str(ev.id), **kw))
    world.db.commit()


def test_filters_pagination_and_summary_are_computed_on_the_server(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="ended")
    _seed(world, o, ev, admin, [
        ("event.access_link.create", {"meta": {"access_link_id": "x"}}),
        ("event.update", {"outcome": "denied", "meta": {"reason_code": "not_event_host"}}),
        ("live.broadcast.golive", {}),
        ("report.generate", {}),
    ])
    _seed(world, o, ev, None, [("live.broadcast.emergency_stop", {})])
    c = client_for(admin)
    base = f"/api/organization/events/{ev.id}/activity"

    body = c.get(base).json()
    assert body["total"] == 5
    assert body["summary"]["total"] == 5
    assert body["summary"]["high_impact"] == 2           # golive + emergency stop
    assert body["summary"]["refused"] == 1
    assert body["summary"]["by_category"]["access"] == 1
    assert body["summary"]["by_category"]["evidence"] == 1
    assert body["scope"] == {"kind": "event", "event_id": str(ev.id), "title": "Quarterly town hall",
                             "status": "ended", "timezone": "Europe/London"}

    assert [i["action"] for i in c.get(base, params={"category": "access"}).json()["items"]] == \
        ["event.access_link.create"]
    assert [i["action"] for i in c.get(base, params={"outcome": "refused"}).json()["items"]] == ["event.update"]
    assert [i["action"] for i in c.get(base, params={"actor": "platform"}).json()["items"]] == \
        ["live.broadcast.emergency_stop"]
    assert {i["action"] for i in c.get(base, params={"high_impact": "true"}).json()["items"]} == \
        {"live.broadcast.golive", "live.broadcast.emergency_stop"}
    assert [i["action"] for i in c.get(base, params={"q": "report"}).json()["items"]] == ["report.generate"]

    p1 = c.get(base, params={"page_size": 2}).json()
    p3 = c.get(base, params={"page_size": 2, "page": 3}).json()
    assert len(p1["items"]) == 2 and p1["total"] == 5 and len(p3["items"]) == 1
    assert c.get(base, params={"category": "nonsense"}).status_code == 422
    assert c.get(base, params={"since": NOW.isoformat(), "until": (NOW - timedelta(days=1)).isoformat()}).status_code == 422
    assert c.get(base, params={"since": (NOW + timedelta(days=1)).isoformat()}).json()["total"] == 0


def test_a_refused_attempt_is_titled_as_an_attempt_with_its_reason(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin)
    _seed(world, o, ev, admin, [("event.end", {"outcome": "rejected",
                                               "meta": {"reason_code": "not_on_air", "from_status": "draft"}})])
    item = client_for(admin).get(f"/api/organization/events/{ev.id}/activity").json()["items"][0]
    assert item["title"] == "End event — rejected"
    assert item["outcome"] == "rejected" and item["changed_state"] is False
    assert item["reason"] == "The event was not live, so there was nothing to end."
    assert activity_log.title_for("event.end", "applied") == "Event ended"


def test_meta_is_whitelisted_and_identities_are_role_limited(world):
    o, platform_org = world.org(), world.org("Platform")
    admin = world.user(o, name="Dana Admin")
    staff = world.user(platform_org, role="super_admin", name="Sam Staffer")
    ev = world.event(o, admin)
    secret_meta = {"email": "viewer@example.com", "ip": "203.0.113.9", "reason": "Because Bob said so",
                   "url": "https://example.com/watch?token=abc", "token": "tok_live_secret",
                   "label": "Free text label", "fields": ["title", "Some Free Text"], "count": 3}
    _seed(world, o, ev, admin, [("event.viewers.invite", {"meta": secret_meta, "ip": "198.51.100.7"})])
    world.db.add(AuditLog(actor_id=staff.id, actor_email=staff.email, actor_role="super_admin",
                          action="support_access.approve", org_id=o.id, event_id=ev.id))
    world.db.commit()

    body = client_for(admin).get(f"/api/organization/events/{ev.id}/activity").json()
    dumped = str(body)
    for leaked in ("viewer@example.com", "203.0.113.9", "198.51.100.7", "Because Bob", "token=abc",
                   "tok_live_secret", "Free text label", "Some Free Text", staff.email, admin.email,
                   "Sam Staffer"):
        assert leaked not in dumped, leaked

    by_action = {i["action"]: i for i in body["items"]}
    invite = by_action["event.viewers.invite"]
    assert {"label": "Recipients", "value": "3"} in invite["details"]
    assert {"label": "Changed", "value": "Title"} in invite["details"]
    assert invite["actor"] == {"kind": "member", "id": str(admin.id), "name": "Dana Admin",
                               "role": "org_admin", "role_label": "Organization Owner"}
    assert by_action["support_access.approve"]["actor"]["kind"] == "staff"
    assert by_action["support_access.approve"]["actor"]["name"] == "ZoikoStream staff"


def test_platform_internal_bookkeeping_is_not_the_organizations_to_read(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin)
    _seed(world, o, ev, None, [("commercial.settlement.matched", {}), ("commercial.period.confirm_close", {}),
                               ("commercial.golive.blocked", {"meta": {"blocking_reasons": ["billing_hold"]}})])
    c = client_for(admin)
    actions = [i["action"] for i in c.get("/api/organization/activity").json()["items"]]
    assert actions == ["commercial.golive.blocked"]
    gate = c.get(f"/api/organization/events/{ev.id}/activity").json()["items"][0]
    assert gate["category"] == "production" and gate["outcome"] == "denied"
    assert {"label": "Blocked by", "value": "Billing hold"} in gate["details"]


def test_nothing_is_fabricated_and_integrity_is_not_overstated(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="scheduled", start_time=NOW + timedelta(days=2))
    world.db.commit()
    body = client_for(admin).get(f"/api/organization/events/{ev.id}/activity").json()
    assert body["items"] == [] and body["summary"]["high_impact"] == 0
    assert body["integrity"]["hash_verification"]["state"] == "not_established"
    assert body["integrity"]["immutability"]["state"] == "not_asserted"
    assert body["integrity"]["time_anchoring"]["state"] == "not_verified"
    assert body["export"]["available"] is False
    assert body["as_of"]


def test_org_ledger_names_the_event_each_row_concerns(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, title="Investor day")
    _seed(world, o, ev, admin, [("event.create", {"meta": {"status": "draft"}})])
    item = client_for(admin).get("/api/organization/activity").json()["items"][0]
    assert item["event"] == {"id": str(ev.id), "title": "Investor day"}
    assert {"label": "Initial status", "value": "Draft"} in item["details"]


def test_live_now_filter_lists_every_event_on_air_and_only_those(world):
    o = world.org()
    admin = world.user(o)
    live = world.event(o, admin, status="live", title="On air")
    degraded = world.event(o, admin, status="degraded", title="Struggling")
    world.event(o, admin, status="scheduled", title="Later", start_time=NOW + timedelta(days=1))
    world.db.commit()
    body = client_for(admin).get("/api/events", params={"display_status": "on_air"}).json()
    assert {i["id"] for i in body["items"]} == {str(live.id), str(degraded.id)}
    assert body["total"] == 2
    # A filter, never a status anything can be set to.
    assert client_for(admin).patch(f"/api/events/{live.id}", json={"status": "on_air"}).status_code == 422


def test_events_list_carries_readiness_for_upcoming_events_only(world):
    o = world.org()
    admin = world.user(o)
    upcoming = world.event(o, admin, status="scheduled", title="Soon", start_time=NOW + timedelta(days=3))
    ended = world.event(o, admin, status="ended", title="Done", start_time=NOW - timedelta(days=3))
    world.db.commit()
    rows = {r["id"]: r for r in client_for(admin).get("/api/events", params={"page_size": 50}).json()["items"]}
    assert rows[str(upcoming.id)]["readiness_verdict"] in ("passed", "conditional", "blocked")
    assert isinstance(rows[str(upcoming.id)]["readiness_failing"], int)
    assert rows[str(ended.id)]["readiness_verdict"] is None, "readiness means nothing after the event"


def test_upcoming_filter_and_attention_counts(world):
    o = world.org()
    admin = world.user(o)
    soon = world.event(o, admin, status="scheduled", title="Soon", start_time=NOW + timedelta(days=3))
    armed = world.event(o, admin, status="armed", title="Armed", start_time=NOW + timedelta(hours=2))
    world.event(o, admin, status="draft", title="Draft")
    world.event(o, admin, status="scheduled", title="Missed", start_time=NOW - timedelta(days=2))
    world.db.commit()
    c = client_for(admin)
    ids = {i["id"] for i in c.get("/api/events", params={"display_status": "upcoming"}).json()["items"]}
    assert ids == {str(soon.id), str(armed.id)}
    attention = c.get("/api/events/status-counts").json()["attention"]
    assert attention["blocked"] + attention["conditional"] <= 2
    assert all(e["id"] in ids for e in attention["events"])


def test_insights_timeline_and_outcomes_come_from_recorded_data(world):
    from app.models import AnalyticsSnapshot

    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="ended", start_time=NOW - timedelta(days=1))
    started = NOW - timedelta(days=1)
    world.db.add(BroadcastSession(event_id=ev.id, org_id=o.id, status="ended", started_at=started,
                                  ended_at=started + timedelta(minutes=40), peak_viewers=3))
    for i, viewers in enumerate((1, 3, 2)):
        world.db.add(AnalyticsSnapshot(event_id=ev.id, org_id=o.id, viewers=viewers,
                                       created_at=started + timedelta(minutes=10 * i)))
    for i, watched in enumerate((300, 700, 1300, 2300)):
        world.db.add(AudienceSession(org_id=o.id, event_id=ev.id, viewer_key=f"t{i}{uuid.uuid4().hex[:6]}",
                                     joined_at=started, last_seen_at=started, watch_seconds=watched))
    world.db.commit()
    try:
        ins = client_for(admin).get(f"/api/organization/events/{ev.id}/insights").json()
        assert [p["viewers"] for p in ins["timeline"]] == [1, 3, 2]
        assert ins["outcomes"] == {"viewers": 4, "watched_10_min": 3, "reached_half": 2, "reached_ninety": 1,
                                   "programme_seconds": 2400}
    finally:
        world.db.execute(delete(AnalyticsSnapshot).where(AnalyticsSnapshot.event_id == ev.id))
        world.db.commit()


def test_an_event_nobody_watched_has_no_outcomes_and_an_empty_timeline(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="scheduled", start_time=NOW + timedelta(days=1))
    world.db.commit()
    ins = client_for(admin).get(f"/api/organization/events/{ev.id}/insights").json()
    assert ins["timeline"] == [] and ins["outcomes"] is None


# ── read models: Preparation, Live Control, Insights ─────────────────────────────────────

def test_read_models_are_org_scoped(world):
    a, b = world.org("A"), world.org("B")
    admin_a, admin_b = world.user(a), world.user(b)
    ev = world.event(a, admin_a)
    world.db.commit()
    cb = client_for(admin_b)
    for path in ("readiness", "live-status", "insights"):
        assert cb.get(f"/api/organization/events/{ev.id}/{path}").status_code == 404, path


def test_an_event_that_never_went_live_reports_unmeasured_not_zero(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="scheduled", start_time=NOW + timedelta(days=1))
    world.db.commit()
    c = client_for(admin)

    ins = c.get(f"/api/organization/events/{ev.id}/insights").json()
    assert ins["measured"] is False
    assert ins["audience"]["unique_viewers"] == 0
    assert ins["audience"]["peak_viewers"] is None
    assert ins["audience"]["avg_watch_seconds"] is None
    assert ins["feedback"]["average_rating"] is None
    assert ins["broadcast"]["on_air_seconds"] is None

    live = c.get(f"/api/organization/events/{ev.id}/live-status").json()
    assert live["on_air"] is False and live["audience_now"] is None
    assert live["current_session"] is None and live["sessions"] == []

    ready = c.get(f"/api/organization/events/{ev.id}/readiness").json()
    assert ready["verdict"] in ("passed", "conditional", "blocked")
    assert {g["key"] for g in ready["gates"]} >= {"title", "schedule", "host", "recording"}
    assert ready["readiness_state"]


def test_insights_report_the_whole_event_audience(world):
    o = world.org()
    admin = world.user(o)
    ev = world.event(o, admin, status="ended", start_time=NOW - timedelta(days=400))
    started = NOW - timedelta(days=400)
    world.db.add(BroadcastSession(event_id=ev.id, org_id=o.id, status="ended", started_at=started,
                                  ended_at=started + timedelta(hours=1), peak_viewers=42))
    for i, watched in enumerate((600, 1200)):
        world.db.add(AudienceSession(org_id=o.id, event_id=ev.id, viewer_key=f"k{i}{uuid.uuid4().hex[:6]}",
                                     joined_at=started, last_seen_at=started + timedelta(minutes=30),
                                     watch_seconds=watched, join_count=1 + i))
    world.db.add(EventFeedback(event_id=ev.id, org_id=o.id, role="viewer", identity="v1", rating=4))
    world.db.add(EventFeedback(event_id=ev.id, org_id=o.id, role="viewer", identity="v2", rating=5))
    world.db.commit()

    ins = client_for(admin).get(f"/api/organization/events/{ev.id}/insights").json()
    assert ins["measured"] is True
    # More than a year ago: the Audience page's 12-month window would have said 0.
    assert ins["audience"]["unique_viewers"] == 2
    assert ins["audience"]["peak_viewers"] == 42
    assert ins["audience"]["avg_watch_seconds"] == 900
    assert ins["audience"]["returning_viewers"] == 1
    assert ins["broadcast"]["on_air_seconds"] == 3600
    assert ins["feedback"] == {"responses": 2, "ratings": 2, "average_rating": 4.5}
