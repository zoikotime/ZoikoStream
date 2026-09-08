"""Postgres-specific concurrency primitives, verified against a real server.

Two mechanisms the codebase depends on for correctness are asserted here rather than taken on
trust from code review, because neither can be exercised without a real Postgres:

  1. Advisory-lock ticker leadership (app/db.py) — elects exactly one process to run the
     background tickers. Its safety property is that the lock is bound to the CONNECTION, so a
     crashed leader releases it with no lease to expire.

  2. `SELECT ... FOR UPDATE` on a capacity pool (crud.commercial._claim_pool_capacity) — makes
     capacity oversell structurally impossible rather than merely unlikely (doc C4), by
     serializing the read-check-insert of two transactions competing for the same pool.

Everything here skips when no database is reachable. SQLite/MySQL are deliberately not
substituted: an advisory lock and a row lock are exactly the semantics under test, and a
different engine would assert nothing about the engine actually deployed.
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.db as db_mod
from app.crud import commercial as crud
from app.db import engine
from app.models import (
    CapacityPool, CatalogVersion, CommercialAccount, Event, EventOrder, Organization, User,
)


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


DB_UP = _db_reachable()
needs_db = pytest.mark.skipif(not DB_UP, reason="DATABASE_URL not reachable")

pytestmark = needs_db


# ══════════════════════════════════════════════════════════════════════════════════════
# Advisory-lock ticker leadership (app/db.py)
# ══════════════════════════════════════════════════════════════════════════════════════

class TestAdvisoryLockLeadership:
    @pytest.fixture(autouse=True)
    def _clean_leadership(self):
        """Each test starts and ends with this process holding nothing, so ordering between
        tests can never make one of them pass for the wrong reason."""
        db_mod.release_ticker_leadership()
        yield
        db_mod.release_ticker_leadership()

    def test_a_process_can_acquire_leadership(self):
        assert db_mod.try_acquire_ticker_leadership() is True
        assert db_mod.holds_ticker_leadership() is True

    def test_acquisition_is_reentrant_for_the_same_process(self):
        """Called on every ticker iteration, so it must not fail or double-count when the
        holder asks again."""
        assert db_mod.try_acquire_ticker_leadership() is True
        assert db_mod.try_acquire_ticker_leadership() is True
        assert db_mod.holds_ticker_leadership() is True

    def test_a_second_connection_is_refused_while_the_lock_is_held(self):
        """The core mutual-exclusion property: a second 'process' (a separate connection, which
        is what pg_try_advisory_lock actually arbitrates on) must be told no, not queued."""
        assert db_mod.try_acquire_ticker_leadership() is True
        with engine.connect() as other:
            granted = other.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": db_mod.TICKER_LOCK_KEY}
            ).scalar()
            assert granted is False, "a second connection must not also become leader"

    def test_releasing_lets_another_connection_take_over(self):
        assert db_mod.try_acquire_ticker_leadership() is True
        db_mod.release_ticker_leadership()
        assert db_mod.holds_ticker_leadership() is False
        with engine.connect() as other:
            granted = other.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": db_mod.TICKER_LOCK_KEY}
            ).scalar()
            assert granted is True, "leadership must be available again after release"
            other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": db_mod.TICKER_LOCK_KEY})

    def test_a_crashed_leader_releases_the_lock_without_any_lease_expiring(self):
        """Crash promotion. The lock is bound to the connection, so losing the connection
        WITHOUT calling release (what a crash/OOM-kill looks like) must free it immediately —
        this is the property that lets a follower take over with no timeout to agree on.

        `invalidate()`, not `close()`: close() hands the connection back to SQLAlchemy's pool
        with the socket still open, so a session-scoped advisory lock correctly survives it
        (and would keep surviving a pool rollback, which does not clear advisory locks).
        invalidate() discards the underlying DBAPI connection, which is what a crashed process
        actually does to its socket.
        """
        crashed = engine.connect()
        granted = crashed.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": db_mod.TICKER_LOCK_KEY}
        ).scalar()
        assert granted is True
        crashed.invalidate()  # no unlock call — the connection is destroyed under it

        assert db_mod.try_acquire_ticker_leadership() is True, \
            "a follower must be able to take over from a crashed leader"

    def test_leadership_is_verified_against_postgres_not_a_cached_flag(self):
        """holds_ticker_leadership() must report reality: if the underlying connection is gone,
        a stale True would mean two processes running the same ticker."""
        assert db_mod.try_acquire_ticker_leadership() is True
        db_mod._leader_connection.invalidate()      # the connection dies underneath it
        assert db_mod.holds_ticker_leadership() is False


# ══════════════════════════════════════════════════════════════════════════════════════
# Row locking: capacity cannot be oversold under concurrency
# ══════════════════════════════════════════════════════════════════════════════════════

class TestCapacityRowLocking:
    @pytest.fixture
    def ctx(self):
        window_start = datetime.now(timezone.utc) + timedelta(days=1)
        window_end = window_start + timedelta(hours=4)
        with Session(engine) as db:
            org = Organization(name=f"cap-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.flush()
            user = User(org_id=org.id, full_name="Cap", email=f"cap-{uuid.uuid4().hex[:8]}@t.test",
                       username=f"cap{uuid.uuid4().hex[:8]}", password_hash="x", role="super_admin")
            db.add(user)
            db.flush()
            account = CommercialAccount(org_id=org.id)
            catalog = CatalogVersion(version_label="v1", vertical=f"cap-{uuid.uuid4().hex[:6]}",
                                     status="published")
            db.add_all([account, catalog])
            db.flush()
            resource_type = f"captest_{uuid.uuid4().hex[:8]}"
            pool = CapacityPool(
                resource_type=resource_type, total_capacity=1, status="active",
                window_start=window_start, window_end=window_end,
            )
            db.add(pool)
            db.flush()
            events = []
            for _ in range(2):
                ev = Event(org_id=org.id, created_by=user.id, title="Capacity race",
                          status="published", billing_classification="commercial")
                db.add(ev)
                db.flush()
                order = EventOrder(event_id=ev.id, commercial_account_id=account.id,
                                   catalog_version_id=catalog.id, currency="USD",
                                   subtotal=Decimal("10.00"), total_amount=Decimal("10.00"),
                                   status="accepted", idempotency_key=str(uuid.uuid4()))
                db.add(order)
                db.flush()
                events.append((ev.id, order.id))
            db.commit()
            ids = dict(org_id=org.id, user_id=user.id, account_id=account.id,
                       catalog_id=catalog.id, pool_id=pool.id, resource_type=resource_type,
                       events=events, window_start=window_start, window_end=window_end)
        yield ids
        with Session(engine) as db:
            for sql, params in [
                ("DELETE FROM capacity_reservations WHERE capacity_pool_id = :p", {"p": ids["pool_id"]}),
                ("DELETE FROM audit_logs WHERE org_id = :o", {"o": ids["org_id"]}),
            ]:
                db.execute(text(sql), params)
            for ev_id, order_id in ids["events"]:
                db.execute(text("DELETE FROM payment_schedules WHERE event_order_id=:o"), {"o": order_id})
                db.execute(text("DELETE FROM commercial_state_transitions WHERE event_order_id=:o"), {"o": order_id})
                db.execute(text("DELETE FROM event_orders WHERE id=:o"), {"o": order_id})
                db.execute(text("DELETE FROM events WHERE id=:e"), {"e": ev_id})
            for sql, params in [
                ("DELETE FROM capacity_pools WHERE id = :p", {"p": ids["pool_id"]}),
                ("DELETE FROM catalog_versions WHERE id = :c", {"c": ids["catalog_id"]}),
                ("DELETE FROM commercial_accounts WHERE id = :a", {"a": ids["account_id"]}),
                ("DELETE FROM users WHERE id = :u", {"u": ids["user_id"]}),
                ("DELETE FROM organizations WHERE id = :o", {"o": ids["org_id"]}),
            ]:
                db.execute(text(sql), params)
            db.commit()

    def test_for_update_blocks_a_second_reader_until_the_first_commits(self, ctx):
        """The lock is real, and it is per-row: a second transaction selecting the SAME pool
        FOR UPDATE must wait. Proven with NOWAIT, which turns "would block" into an immediate
        error instead of a hang — a test that merely waited could pass on an unlocked row."""
        from sqlalchemy.exc import OperationalError

        with Session(engine) as first:
            first.execute(
                text("SELECT id FROM capacity_pools WHERE id = :p FOR UPDATE"),
                {"p": ctx["pool_id"]},
            ).fetchone()

            with Session(engine) as second:
                with pytest.raises(OperationalError):
                    second.execute(
                        text("SELECT id FROM capacity_pools WHERE id = :p FOR UPDATE NOWAIT"),
                        {"p": ctx["pool_id"]},
                    ).fetchone()
            first.rollback()

        # And once released, the same statement succeeds — the block was the lock, not a
        # permanently unavailable row.
        with Session(engine) as third:
            row = third.execute(
                text("SELECT id FROM capacity_pools WHERE id = :p FOR UPDATE NOWAIT"),
                {"p": ctx["pool_id"]},
            ).fetchone()
            assert row is not None
            third.rollback()

    def test_two_concurrent_holds_against_one_seat_cannot_both_succeed(self, ctx):
        """End-to-end oversell guard: a pool with total_capacity=1, two threads racing to hold
        it. Exactly one must win; the other must be refused with the doc C4 message — never
        both granted, and never both refused."""
        results, errors = [], []
        barrier = threading.Barrier(2)

        def claim(index):
            ev_id, order_id = ctx["events"][index]
            try:
                with Session(engine) as db:
                    event = db.get(Event, ev_id)
                    order = db.get(EventOrder, order_id)
                    barrier.wait(timeout=10)      # maximize the overlap
                    reservation = crud.soft_hold_capacity(
                        db, event, resource_type=ctx["resource_type"],
                        window_start=ctx["window_start"], window_end=ctx["window_end"],
                        quantity=1, event_order=order,
                    )
                    results.append(reservation.id)
            except ValueError as exc:
                errors.append(str(exc))
            except Exception as exc:                  # pragma: no cover - surfaced below
                errors.append(f"UNEXPECTED {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 1, f"exactly one hold must succeed; got {len(results)} (errors: {errors})"
        assert len(errors) == 1, f"exactly one hold must be refused; got {errors}"
        assert "Insufficient capacity" in errors[0], f"refusal must be the capacity guard: {errors[0]}"

    def test_utilisation_reflects_exactly_one_held_seat_after_the_race(self, ctx):
        """Follow-through on the race above: the pool's own accounting must agree that one
        seat is taken, not two — the ledger and the lock must not disagree."""
        ev_id, order_id = ctx["events"][0]
        with Session(engine) as db:
            event = db.get(Event, ev_id)
            order = db.get(EventOrder, order_id)
            crud.soft_hold_capacity(
                db, event, resource_type=ctx["resource_type"],
                window_start=ctx["window_start"], window_end=ctx["window_end"],
                quantity=1, event_order=order,
            )
        with Session(engine) as db:
            pool = db.get(CapacityPool, ctx["pool_id"])
            util = crud.pool_utilisation(db, pool)
            assert util["available_capacity"] == 0
            assert util["reserved_capacity"] == 1
