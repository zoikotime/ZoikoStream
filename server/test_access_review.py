"""ORG-007 reviewer designation and escalation (ZST-EC-001).

The previous audit marked ORG-007 PARTIAL for two reasons: every assignment went to
whichever administrator the query returned first, and `escalated_at` marked a state with no
escalation subsystem behind it. These tests cover both, plus the control that matters most —
a reviewer's silence must never become an approval.

Run with `python test_access_review.py` (or pytest).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.testclient import TestClient

import app.email as email_mod
import app.main as m
from app import ratelimit
from app.db import SessionLocal
from app.models import (
    DECISION_APPROVED,
    DECISION_EXCEPTION,
    DECISION_PENDING,
    DECISION_REMOVE,
    REVIEW_COMPLETED,
    REVIEW_OVERDUE,
    REVIEWER_OWNER,
    REVIEWER_SECURITY_ADMIN,
    AccessReview,
    AccessReviewAssignment,
    AccessReviewEscalation,
    AccountRecovery,
    AuditLog,
    ElevationSession,
    IdentityChallenge,
    Organization,
    OrgMembershipEvent,
    OrgOperationalEvent,
    SignInEvent,
    StepUpGrant,
    User,
)
from app.security import hash_password
from app.services import org_governance as gov

PASSWORD = "correct-horse-battery"


class _Resp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None


class Captured:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "payload": json or {}})
        if self.fail:
            import httpx
            raise httpx.ConnectError("simulated Resend outage")
        return _Resp()

    @property
    def subjects(self):
        return [c["payload"]["subject"] for c in self.calls]

    def of(self, subject):
        hits = [c["payload"] for c in self.calls if c["payload"]["subject"] == subject]
        assert hits, f"no message with subject {subject!r}; got {self.subjects}"
        return hits[-1]


_LEAKS: list[str] = []


def _deny(url, headers=None, json=None, timeout=None):
    _LEAKS.append((json or {}).get("subject", "?"))
    raise RuntimeError("outbound email attempted outside a capture context")


email_mod.httpx.post = _deny


def _assert_no_leak():
    assert not _LEAKS, f"email sent outside a capture context: {_LEAKS}"


def _capture(fail=False):
    cap = Captured(fail=fail)
    return cap, patch.object(email_mod.httpx, "post", cap)


def _reset_limits():
    ratelimit._HITS.clear()


def _new_email(tag="rev"):
    return f"{tag}-{uuid.uuid4().hex[:12]}@example.com"


class _Bg:
    def add_task(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class Org:
    """Owner + a second admin + two ordinary members."""

    def __init__(self):
        db = SessionLocal()
        try:
            org = Organization(name=f"Rev Co {uuid.uuid4().hex[:6]}", status="active",
                               timezone="Asia/Kolkata")
            db.add(org)
            db.flush()
            self.org_id = org.id
            self.owner_email = _new_email("owner")
            self.owner_id = self._u(db, "org_admin", self.owner_email, "Owner")
            org.owner_user_id = self.owner_id
            self.admin_email = _new_email("aadmin")     # sorts before zmember
            self.admin_id = self._u(db, "org_admin", self.admin_email, "Second Admin")
            self.member_email = _new_email("zmember")
            self.member_id = self._u(db, "viewer", self.member_email, "Member")
            db.commit()
        finally:
            db.close()

    def _u(self, db, role, email, name):
        user = User(org_id=self.org_id, full_name=name, role=role, is_active=True,
                    email=email.lower(), username=f"u{uuid.uuid4().hex[:10]}",
                    password_hash=hash_password(PASSWORD), email_verified=True,
                    email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
        return user.id

    def open_review(self, *, due_in_hours=72):
        db = SessionLocal()
        try:
            creator = db.get(User, self.owner_id)
            review = gov.open_review(
                db, org_id=self.org_id,
                due_at=datetime.now(timezone.utc) + timedelta(hours=due_in_hours),
                created_by=creator, review_period="Q3")
            return review.id
        finally:
            db.close()

    def assignments(self, review_id):
        db = SessionLocal()
        try:
            return db.scalars(
                db.query(AccessReviewAssignment).filter(
                    AccessReviewAssignment.review_id == review_id).statement
            ).all()
        finally:
            db.close()

    def escalations(self, review_id):
        db = SessionLocal()
        try:
            return db.query(AccessReviewEscalation).filter(
                AccessReviewEscalation.review_id == review_id).all()
        finally:
            db.close()

    def cleanup(self):
        db = SessionLocal()
        try:
            for review in db.query(AccessReview).filter(
                    AccessReview.org_id == self.org_id).all():
                db.query(AccessReviewEscalation).filter(
                    AccessReviewEscalation.review_id == review.id).delete()
                db.query(AccessReviewAssignment).filter(
                    AccessReviewAssignment.review_id == review.id).delete()
                db.delete(review)
            db.query(AuditLog).filter(AuditLog.org_id == self.org_id).delete()
            db.query(OrgMembershipEvent).filter(
                OrgMembershipEvent.org_id == self.org_id).delete()
            db.query(OrgOperationalEvent).filter(
                OrgOperationalEvent.org_id == self.org_id).delete()
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                org.owner_user_id = None
            db.commit()
            for user in db.query(User).filter(User.org_id == self.org_id).all():
                for model in (SignInEvent, IdentityChallenge, AccountRecovery,
                              ElevationSession, StepUpGrant):
                    db.query(model).filter(model.user_id == user.id).delete()
                db.delete(user)
            db.commit()
            org = db.get(Organization, self.org_id)
            if org is not None:
                db.delete(org)
                db.commit()
        finally:
            db.close()


# ══ reviewer designation ════════════════════════════════════════════════════════

def test_reviewers_are_explicitly_designated_not_first_admin():
    """The audit's finding: every item used to go to whichever admin came back first."""
    o = Org()
    try:
        review_id = o.open_review()
        rows = {a.member_id: a for a in o.assignments(review_id)}
        assert len(rows) == 3, "one assignment per active member"

        # An administrator's access is reviewed by the OWNER, not by a peer administrator.
        admin_item = rows[o.admin_id]
        assert admin_item.reviewer_id == o.owner_id, \
            "administrative access must be reviewed by the Organization Owner"
        assert admin_item.reviewer_source == REVIEWER_OWNER

        # An ordinary member is reviewed by an administrator — and never by themselves.
        member_item = rows[o.member_id]
        assert member_item.reviewer_source == REVIEWER_SECURITY_ADMIN
        assert member_item.reviewer_id in (o.owner_id, o.admin_id)
        assert member_item.reviewer_id != o.member_id, "nobody reviews their own access"

        # The owner's own access is never self-reviewed.
        owner_item = rows[o.owner_id]
        assert owner_item.reviewer_id != o.owner_id, "the owner must not review themselves"

        # Every assignment records WHY that reviewer was chosen.
        for a in rows.values():
            assert a.reviewer_source, "the designation basis must be recorded"
    finally:
        o.cleanup()


def test_designation_is_deterministic():
    """Re-opening a review must not silently reassign every item."""
    o = Org()
    try:
        first = {a.member_id: a.reviewer_id for a in o.assignments(o.open_review())}
        second = {a.member_id: a.reviewer_id for a in o.assignments(o.open_review())}
        assert first == second, "the same inputs must designate the same reviewers"
    finally:
        o.cleanup()


# ══ no implicit approval ════════════════════════════════════════════════════════

def test_inaction_stays_pending_and_blocks_completion():
    o = Org()
    try:
        review_id = o.open_review()
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            assert gov.outstanding(db, review) == 3
            assert gov.complete(db, review) is False, \
                "a review must not complete while anything is pending"
            assert db.get(AccessReview, review_id).status != REVIEW_COMPLETED

            # Everything is still PENDING — nothing became an approval by being ignored.
            for a in db.query(AccessReviewAssignment).filter(
                    AccessReviewAssignment.review_id == review_id).all():
                assert a.decision == DECISION_PENDING
        finally:
            db.close()
    finally:
        o.cleanup()


# ══ escalation ══════════════════════════════════════════════════════════════════

def test_overdue_creates_durable_escalation_records():
    """The audit's other finding: escalated_at marked a state with nothing behind it."""
    o = Org()
    try:
        review_id = o.open_review(due_in_hours=-1)   # already past due
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            assert gov.mark_overdue(db, review) is True
            assert db.get(AccessReview, review_id).status == REVIEW_OVERDUE
            created = gov.escalate_overdue(db, review)
            assert len(created) == 3, "one escalation per still-pending item"
        finally:
            db.close()

        rows = o.escalations(review_id)
        assert len(rows) == 3
        for row in rows:
            assert row.escalated_to_id == o.owner_id, "escalation goes up, to the owner"
            assert row.reason, "a reason is recorded"
            assert row.resolved_at is None, "and it starts unresolved"

        # High risk is derived from the snapshot, not asserted by a caller.
        high = [r for r in rows if r.high_risk]
        assert high, "an unreviewed administrative grant must be flagged high-risk"
        assert all("Administrative access" in r.reason for r in high)
    finally:
        o.cleanup()


def test_escalation_is_idempotent_and_resolves_on_decision():
    o = Org()
    try:
        review_id = o.open_review(due_in_hours=-1)
        db = SessionLocal()
        try:
            review = db.get(AccessReview, review_id)
            gov.mark_overdue(db, review)
            gov.escalate_overdue(db, review)
            again = gov.escalate_overdue(db, review)
            assert again == [], "a repeated pass must not duplicate escalations"

            # Deciding an item closes its escalation.
            item = db.query(AccessReviewAssignment).filter(
                AccessReviewAssignment.review_id == review_id,
                AccessReviewAssignment.member_id == o.member_id).one()
            owner = db.get(User, o.owner_id)
            gov.record_decision(db, item, decision=DECISION_APPROVED, decided_by=owner)
            open_rows = gov.open_escalations(db, db.get(AccessReview, review_id))
            assert len(open_rows) == 2, "the decided item's escalation is resolved"
        finally:
            db.close()
    finally:
        o.cleanup()


def test_overdue_notice_reports_a_real_obligation():
    o = Org()
    try:
        review_id = o.open_review(due_in_hours=-1)
        _reset_limits()
        cap, ctx = _capture()
        with ctx:
            db = SessionLocal()
            try:
                stats = gov.sweep(db, _Bg())
            finally:
                db.close()
        assert stats["overdue"] >= 1
        payload = cap.of(email_mod.ORG_007_OVERDUE_SUBJECT)
        assert payload["html"] and payload["text"]
        text = payload["text"]
        assert "escalated to the Organization Owner" in text, \
            "the notice must describe the escalation that actually happened"
        assert "No response is not approval" in text
        # And the escalation rows really exist behind that sentence.
        assert len(o.escalations(review_id)) >= 1
    finally:
        o.cleanup()


def test_completion_counts_are_accurate():
    o = Org()
    try:
        review_id = o.open_review()
        db = SessionLocal()
        try:
            owner = db.get(User, o.owner_id)
            items = db.query(AccessReviewAssignment).filter(
                AccessReviewAssignment.review_id == review_id).order_by(
                AccessReviewAssignment.member_email).all()
            gov.record_decision(db, items[0], decision=DECISION_APPROVED, decided_by=owner)
            gov.record_decision(db, items[1], decision=DECISION_REMOVE, decided_by=owner)
            gov.record_decision(db, items[2], decision=DECISION_EXCEPTION,
                                decided_by=owner, reason="Temporary project access",
                                exception_owner_email=o.owner_email)

            review = db.get(AccessReview, review_id)
            counts = gov.tally(db, review)
            assert counts["approved"] == 1 and counts["removed"] == 1
            assert counts["exceptions"] == 1 and counts["pending"] == 0
            assert gov.complete(db, review) is True
            assert db.get(AccessReview, review_id).status == REVIEW_COMPLETED

            # An exception must name an accountable owner.
            exc = db.query(AccessReviewAssignment).filter(
                AccessReviewAssignment.review_id == review_id,
                AccessReviewAssignment.decision == DECISION_EXCEPTION).one()
            assert exc.exception_owner_email == o.owner_email.lower() or \
                exc.exception_owner_email == o.owner_email
        finally:
            db.close()
    finally:
        o.cleanup()


try:
    import pytest

    @pytest.fixture(autouse=True)
    def _no_unmocked_sends():
        yield
        _assert_no_leak()
except ImportError:                     # pragma: no cover
    pass


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                _assert_no_leak()
                passed += 1
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
