"""End-to-end DB coverage for P0.11 (audit 2026-08-27): memorial-category events must never be
publicly visible, enforced at the backend authoritative layer on both create and update.

The pure-logic coverage (`_enforce_memorial_features` return values, the audit-stub tests) lives
in test_events.py, which is deliberately DB-free. This file exercises the real crud.create_event
/ crud.update_event paths against Postgres, including the audit trail, and skips when no
database is reachable (see conftest.py — TEST_DATABASE_URL is required to run this suite at all).
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import event as crud
from app.db import engine
from app.models import AuditLog, Event, Organization, User


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


@needs_db
class TestMemorialVisibilityEnforcement:
    @pytest.fixture
    def ctx(self):
        with Session(engine) as db:
            org = Organization(name=f"mem-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Memorial Host", email=f"mem-{uuid.uuid4().hex[:8]}@t.test",
                       username=f"mem{uuid.uuid4().hex[:8]}", password_hash="x", role="org_admin")
            db.add(user)
            db.flush()
            ids = SimpleNamespace(org_id=org.id, user=user, events=[])
            yield db, ids
            for eid in ids.events:
                db.execute(text("DELETE FROM audit_logs WHERE target_type='event' AND target_id=:e"), {"e": str(eid)})
                db.execute(text("DELETE FROM events WHERE id=:e"), {"e": eid})
            db.execute(text("DELETE FROM users WHERE id=:u"), {"u": user.id})
            db.execute(text("DELETE FROM organizations WHERE id=:o"), {"o": org.id})
            db.commit()

    def test_memorial_create_with_public_visibility_is_forced_private(self, ctx):
        db, ids = ctx
        data = SimpleNamespace(model_dump=lambda exclude=None: {
            "title": "A memorial", "category": "Funeral / Memorial", "visibility": "public",
        })
        ev = crud.create_event(db, ids.org_id, ids.user.id, data, "a-memorial", actor=ids.user)
        ids.events.append(ev.id)

        assert ev.visibility == "private"
        stored = db.get(Event, ev.id)
        assert stored.visibility == "private"

        entry = db.query(AuditLog).filter(
            AuditLog.target_type == "event", AuditLog.target_id == str(ev.id),
            AuditLog.action == "event.memorial_visibility_enforced",
        ).one()
        assert entry.meta["attempted_visibility"] == "public"
        assert entry.actor_id == ids.user.id

    def test_memorial_update_attempting_public_visibility_is_rejected(self, ctx):
        db, ids = ctx
        data = SimpleNamespace(model_dump=lambda exclude=None: {
            "title": "A memorial", "category": "Funeral / Memorial", "visibility": "private",
        })
        ev = crud.create_event(db, ids.org_id, ids.user.id, data, "a-memorial-2", actor=ids.user)
        ids.events.append(ev.id)

        updated = crud.update_event(db, ev, {"visibility": "public"}, actor=ids.user)
        assert updated.visibility == "private"

        entries = db.query(AuditLog).filter(
            AuditLog.target_type == "event", AuditLog.target_id == str(ev.id),
            AuditLog.action == "event.memorial_visibility_enforced",
        ).all()
        assert any(e.meta["attempted_visibility"] == "public" for e in entries)

    def test_switching_an_existing_event_to_memorial_forces_visibility_without_touching_it(self, ctx):
        """The category switch never mentions `visibility` at all — update_event must still
        catch it via the row's current value, not just the payload."""
        db, ids = ctx
        data = SimpleNamespace(model_dump=lambda exclude=None: {
            "title": "Just a webinar", "category": "Webinar", "visibility": "public",
        })
        ev = crud.create_event(db, ids.org_id, ids.user.id, data, "just-a-webinar", actor=ids.user)
        ids.events.append(ev.id)
        assert ev.visibility == "public"  # not memorial yet — unaffected

        updated = crud.update_event(db, ev, {"category": "Funeral / Memorial"}, actor=ids.user)
        assert updated.visibility == "private"
        assert updated.chat_enabled is False

    def test_valid_private_memorial_update_is_not_audited_as_a_correction(self, ctx):
        db, ids = ctx
        data = SimpleNamespace(model_dump=lambda exclude=None: {
            "title": "A memorial", "category": "Funeral / Memorial", "visibility": "private",
        })
        ev = crud.create_event(db, ids.org_id, ids.user.id, data, "a-memorial-3", actor=ids.user)
        ids.events.append(ev.id)

        crud.update_event(db, ev, {"title": "A memorial, updated"}, actor=ids.user)
        count = db.query(AuditLog).filter(
            AuditLog.target_type == "event", AuditLog.target_id == str(ev.id),
            AuditLog.action == "event.memorial_visibility_enforced",
        ).count()
        assert count == 0  # create was already private — never anything to correct

    def test_non_memorial_events_are_unaffected(self, ctx):
        db, ids = ctx
        data = SimpleNamespace(model_dump=lambda exclude=None: {
            "title": "A webinar", "category": "Webinar", "visibility": "public",
        })
        ev = crud.create_event(db, ids.org_id, ids.user.id, data, "a-webinar", actor=ids.user)
        ids.events.append(ev.id)
        assert ev.visibility == "public"

        updated = crud.update_event(db, ev, {"visibility": "unlisted"}, actor=ids.user)
        assert updated.visibility == "unlisted"  # a non-memorial event may pick any value

        count = db.query(AuditLog).filter(
            AuditLog.target_type == "event", AuditLog.target_id == str(ev.id),
            AuditLog.action == "event.memorial_visibility_enforced",
        ).count()
        assert count == 0
