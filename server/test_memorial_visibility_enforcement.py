"""Funeral / Memorial is a classification, not a configuration.

── WHAT CHANGED ────────────────────────────────────────────────────────────────────────
This file used to assert the opposite: that a memorial event was FORCED to
visibility="private" with chat/Q&A/polls/raise-hand off, on create, on update, and again at
Go Live, with an `event.memorial_visibility_enforced` AuditLog recording each override.

That restriction is retired by product decision. The category now classifies an event and
nothing more, so the tests are inverted rather than deleted: every case that previously
proved an override now proves the organiser's choice SURVIVES. Coverage is wider than
before, because "does not override" has to be checked on each field independently.

Deliberately still enforced, and asserted here so a future cleanup does not take it with
the rest: the r2 COMMERCIAL risk floor (crud/commercial.py reads risk_tier for service
profiles, cancellation policy matching, order pricing and readiness gates). It never
touched visibility or features.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.crud import event as crud
from app.db import engine
from app.models import AuditLog, Event, Organization, User

MEMORIAL = "Funeral / Memorial"
INTERACTION_FLAGS = ("chat_enabled", "qa_enabled", "polls_enabled", "raise_hand_enabled")


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")


def _payload(**over):
    """An EventCreate-shaped stand-in. Defaults mirror schemas.event.EventCreate."""
    fields = {
        "title": "An event", "category": MEMORIAL, "visibility": "public",
        "chat_enabled": True, "qa_enabled": True, "polls_enabled": True,
        "raise_hand_enabled": True, "registration_required": False,
        "recording_enabled": False,
    }
    fields.update(over)
    return SimpleNamespace(model_dump=lambda exclude=None: dict(fields))


@needs_db
class TestMemorialBehavesLikeEveryOtherCategory:
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

    def _create(self, db, ids, slug, **over):
        ev = crud.create_event(db, ids.org_id, ids.user.id, _payload(**over), slug, actor=ids.user)
        ids.events.append(ev.id)
        return ev

    # ── visibility ─────────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("visibility", ["public", "unlisted", "private"])
    def test_a_memorial_can_be_created_with_any_visibility(self, ctx, visibility):
        """The headline inversion: "public" used to come back as "private"."""
        db, ids = ctx
        ev = self._create(db, ids, f"vis-{visibility}-{uuid.uuid4().hex[:6]}", visibility=visibility)

        assert ev.visibility == visibility
        assert db.get(Event, ev.id).visibility == visibility

    @pytest.mark.parametrize("visibility", ["public", "unlisted"])
    def test_a_memorial_can_be_updated_to_any_visibility(self, ctx, visibility):
        db, ids = ctx
        ev = self._create(db, ids, f"upd-{visibility}-{uuid.uuid4().hex[:6]}", visibility="private")

        crud.update_event(db, ev, {"visibility": visibility}, actor=ids.user)

        assert ev.visibility == visibility
        assert db.get(Event, ev.id).visibility == visibility

    def test_switching_an_existing_event_to_memorial_leaves_its_visibility_alone(self, ctx):
        """The case the old clamp existed for, now asserted the other way: a public webinar
        re-categorised as a memorial stays public."""
        db, ids = ctx
        ev = self._create(db, ids, f"switch-{uuid.uuid4().hex[:6]}",
                          category="Webinar", visibility="public")

        crud.update_event(db, ev, {"category": MEMORIAL}, actor=ids.user)

        assert ev.category == MEMORIAL
        assert ev.visibility == "public"
        assert db.get(Event, ev.id).visibility == "public"

    # ── audience features ──────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("flag", INTERACTION_FLAGS)
    def test_a_memorial_can_enable_each_interaction_feature(self, ctx, flag):
        db, ids = ctx
        ev = self._create(db, ids, f"feat-{flag}-{uuid.uuid4().hex[:6]}", **{flag: True})

        assert getattr(ev, flag) is True
        assert getattr(db.get(Event, ev.id), flag) is True

    def test_a_memorial_keeps_every_feature_it_was_created_with(self, ctx):
        db, ids = ctx
        ev = self._create(db, ids, f"all-{uuid.uuid4().hex[:6]}")

        stored = db.get(Event, ev.id)
        for flag in INTERACTION_FLAGS:
            assert getattr(stored, flag) is True, flag

    def test_switching_to_memorial_does_not_clear_feature_flags(self, ctx):
        db, ids = ctx
        ev = self._create(db, ids, f"keepfeat-{uuid.uuid4().hex[:6]}", category="Webinar")

        crud.update_event(db, ev, {"category": MEMORIAL}, actor=ids.user)

        stored = db.get(Event, ev.id)
        for flag in INTERACTION_FLAGS:
            assert getattr(stored, flag) is True, flag

    # ── nothing is silently overridden, and nothing is audited as an override ──────────

    def test_create_writes_back_exactly_what_was_submitted(self, ctx):
        db, ids = ctx
        ev = self._create(db, ids, f"exact-{uuid.uuid4().hex[:6]}",
                          visibility="unlisted", chat_enabled=True, qa_enabled=False,
                          polls_enabled=True, raise_hand_enabled=False)

        stored = db.get(Event, ev.id)
        assert stored.visibility == "unlisted"
        assert (stored.chat_enabled, stored.qa_enabled) == (True, False)
        assert (stored.polls_enabled, stored.raise_hand_enabled) == (True, False)

    def test_no_memorial_override_is_audited_any_more(self, ctx):
        """The override no longer happens, so the action that recorded it must never appear.
        The general audit system is untouched — only this one action is retired."""
        db, ids = ctx
        ev = self._create(db, ids, f"noaudit-{uuid.uuid4().hex[:6]}", visibility="public")

        entries = db.query(AuditLog).filter(
            AuditLog.target_type == "event", AuditLog.target_id == str(ev.id),
            AuditLog.action == "event.memorial_visibility_enforced",
        ).all()
        assert entries == []

    # ── other categories are unchanged ────────────────────────────────────────────────

    @pytest.mark.parametrize("category", ["Webinar", "Wedding / Celebration", "Other", None])
    def test_other_categories_are_unaffected(self, ctx, category):
        db, ids = ctx
        ev = self._create(db, ids, f"other-{uuid.uuid4().hex[:6]}",
                          category=category, visibility="public")

        assert ev.visibility == "public"
        assert ev.chat_enabled is True

    # ── the commercial risk floor SURVIVES ────────────────────────────────────────────

    def test_the_memorial_risk_floor_is_still_applied(self, ctx):
        """Kept on purpose. risk_tier drives service profiles, cancellation policies, order
        pricing and readiness gates in crud/commercial.py — it never gated visibility or
        features, so retiring the audience restriction must not lower it."""
        db, ids = ctx
        ev = self._create(db, ids, f"risk-{uuid.uuid4().hex[:6]}")

        assert ev.risk_tier == "r2"
        assert crud.category_min_risk_tier(MEMORIAL) == "r2"
        # And it is still a floor, not a fixed value: a higher tier is never lowered.
        assert crud.elevated_risk_tier(MEMORIAL, "r3") == "r3"

    def test_a_non_memorial_category_keeps_the_baseline_tier(self, ctx):
        db, ids = ctx
        ev = self._create(db, ids, f"risk0-{uuid.uuid4().hex[:6]}", category="Webinar")
        assert ev.risk_tier == "r0"

    def test_the_classifier_still_classifies(self, ctx):
        """is_memorial_category is kept for reporting. It must still be correct — and
        case/whitespace-insensitive — while gating nothing."""
        assert crud.is_memorial_category(MEMORIAL) is True
        assert crud.is_memorial_category("  funeral / memorial  ") is True
        assert crud.is_memorial_category("Webinar") is False
        assert crud.is_memorial_category(None) is False
