"""Tests for custom domain duplicate conflict handling, normalization, migration guard, and repair.

Covers the 9 requirements:
1. Duplicate normalized domains block migration
2. Migration lists all duplicate conflicts
3. Case-insensitive duplicate rejection
4. Trailing-dot normalization
5. Clearing a duplicate resets all lifecycle fields
6. Legitimate owner's domain remains unchanged
7. Application returns 409 for duplicate claim
8. No stale `active` state remains after domain removal
9. Unique index prevents race-condition duplicate inserts
"""
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import domain_names
from app.models import AuditLog, Organization
from app.services import custom_domains
from create_tables import _CUSTOM_DOMAIN_STATEMENTS
from custom_domain_support import client_for, feature, fresh, host, world  # noqa: F401
from repair_custom_domain_duplicates import repair_conflict


def test_1_duplicate_normalized_domains_block_migration(world, feature):
    """1. Duplicate normalized domains block migration."""
    h = host()
    # Temporarily drop index if exists to test migration check
    world.db.execute(text("DROP INDEX IF EXISTS uq_organizations_domain"))
    world.db.commit()

    # Manually insert duplicate normalized domains
    world.db.execute(
        text("UPDATE organizations SET domain = :d WHERE id = :id"),
        {"d": h.lower(), "id": world.a.id},
    )
    world.db.execute(
        text("UPDATE organizations SET domain = :d WHERE id = :id"),
        {"d": h.upper(), "id": world.b.id},
    )
    world.db.commit()

    # Statement 1 normalizes domains
    world.db.execute(text(_CUSTOM_DOMAIN_STATEMENTS[0]))
    world.db.commit()

    # Statement 2 must fail because duplicates exist
    stmt_check_dups = _CUSTOM_DOMAIN_STATEMENTS[1]
    with pytest.raises(DBAPIError) as excinfo:
        world.db.execute(text(stmt_check_dups))
        world.db.commit()
    world.db.rollback()

    assert "Duplicate custom domains found" in str(excinfo.value)
    assert h.lower() in str(excinfo.value)

    # Re-enable unique index after cleanup
    world.db.execute(text("UPDATE organizations SET domain = NULL WHERE id IN (:a, :b)"), {"a": world.a.id, "b": world.b.id})
    world.db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_organizations_domain ON organizations (lower(domain))"))
    world.db.commit()


def test_2_migration_lists_all_duplicate_conflicts(world, feature):
    """2. Migration lists all duplicate conflicts and org IDs in one run."""
    h1 = "events-" + uuid.uuid4().hex[:8] + ".example.com"
    h2 = "stream-" + uuid.uuid4().hex[:8] + ".example.com"

    world.db.execute(text("DROP INDEX IF EXISTS uq_organizations_domain"))
    world.db.commit()

    # Create 2 extra orgs to test multiple conflicts
    org_c = Organization(name="Org C", domain=h1)
    org_d = Organization(name="Org D", domain=h2)
    world.db.add_all([org_c, org_d])
    world.db.commit()

    world.db.execute(
        text("UPDATE organizations SET domain = :d WHERE id = :id"),
        {"d": h1, "id": world.a.id},
    )
    world.db.execute(
        text("UPDATE organizations SET domain = :d WHERE id = :id"),
        {"d": h2, "id": world.b.id},
    )
    world.db.commit()

    stmt_check_dups = _CUSTOM_DOMAIN_STATEMENTS[1]
    with pytest.raises(DBAPIError) as excinfo:
        world.db.execute(text(stmt_check_dups))
        world.db.commit()
    world.db.rollback()

    err = str(excinfo.value)
    assert "Duplicate custom domains found:" in err
    assert h1 in err
    assert h2 in err
    assert f"- org_id: {world.a.id}" in err
    assert f"- org_id: {org_c.id}" in err
    assert f"- org_id: {world.b.id}" in err
    assert f"- org_id: {org_d.id}" in err
    assert "Resolve these conflicts before uq_organizations_domain can be created." in err

    # Cleanup
    world.db.execute(text("DELETE FROM organizations WHERE id IN (:c, :d)"), {"c": org_c.id, "d": org_d.id})
    world.db.execute(text("UPDATE organizations SET domain = NULL WHERE id IN (:a, :b)"), {"a": world.a.id, "b": world.b.id})
    world.db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_organizations_domain ON organizations (lower(domain))"))
    world.db.commit()


def test_3_case_insensitive_duplicate_rejection(world, feature):
    """3. Case-insensitive duplicate rejection."""
    h = host()
    r1 = client_for(world.admin_a).patch("/api/organization/domain", json={"domain": h.lower()})
    assert r1.status_code == 200

    r2 = client_for(world.admin_b).patch("/api/organization/domain", json={"domain": h.upper()})
    assert r2.status_code == 409
    assert r2.json()["detail"] == "This custom domain is already registered to another organization."


def test_4_trailing_dot_normalization(world, feature):
    """4. Trailing-dot normalization in application and migration."""
    # Application layer
    raw = "Events.Example.Com."
    norm = domain_names.normalize_custom_hostname(raw)
    assert norm == "events.example.com"

    # Migration layer (statement 1)
    world.db.execute(
        text("UPDATE organizations SET domain = :d WHERE id = :id"),
        {"d": "Events.ZoikoGroup.Com.", "id": world.a.id},
    )
    world.db.commit()

    world.db.execute(text(_CUSTOM_DOMAIN_STATEMENTS[0]))
    world.db.commit()

    a = fresh(world, world.a)
    assert a.domain == "events.zoikogroup.com"


def test_5_clearing_a_duplicate_resets_all_lifecycle_fields(world, feature):
    """5. Clearing a duplicate resets all lifecycle fields and writes audit log."""
    target_domain = "events.zoikogroup.com"

    # Temporarily drop unique index to simulate pre-migration duplicate condition
    world.db.execute(text("DROP INDEX IF EXISTS uq_organizations_domain"))
    world.db.commit()

    # Configure duplicate state on B
    b = fresh(world, world.b)
    b.domain = target_domain
    b.domain_status = "active"
    b.domain_verified = True
    b.custom_domain_enabled = True
    b.domain_verification_token = "tok123"
    b.domain_verified_at = datetime.now(timezone.utc)
    b.domain_last_checked_at = datetime.now(timezone.utc)
    b.domain_check_started_at = datetime.now(timezone.utc)
    b.domain_failing_since = datetime.now(timezone.utc)
    b.domain_error = "dns_changed"
    b.domain_check = {"cname": True}
    b.custom_hostname_id = "ch_duplicate_999"
    b.custom_hostname_status = "active"
    b.certificate_status = "active"
    world.db.commit()

    # Configure owner state on A
    a = fresh(world, world.a)
    a.domain = target_domain
    a.domain_status = "active"
    a.domain_verified = True
    a.custom_domain_enabled = True
    world.db.commit()

    # Run repair script logic
    res = repair_conflict(world.db.bind, target_domain, keep_org_id=str(world.a.id), actor_email="admin@zoikogroup.com")
    assert res["repaired"] == 1
    assert str(world.b.id) in res["reset_org_ids"]

    # Verify all lifecycle fields on non-owner are cleared
    b_fresh = fresh(world, world.b)
    assert b_fresh.domain is None
    assert b_fresh.domain_verified is False
    assert b_fresh.custom_domain_enabled is False
    assert b_fresh.domain_status == "not_configured"
    assert b_fresh.domain_verification_token is None
    assert b_fresh.domain_verified_at is None
    assert b_fresh.domain_last_checked_at is None
    assert b_fresh.domain_check_started_at is None
    assert b_fresh.domain_failing_since is None
    assert b_fresh.domain_error is None
    assert b_fresh.domain_check is None
    assert b_fresh.custom_hostname_id is None
    assert b_fresh.custom_hostname_status is None
    assert b_fresh.certificate_status is None

    # Verify audit log
    audit_row = world.db.query(AuditLog).filter(
        AuditLog.org_id == world.b.id,
        AuditLog.action == "custom_domain.conflict_repaired",
    ).first()
    assert audit_row is not None
    assert audit_row.meta["hostname"] == target_domain
    assert audit_row.meta["reason"] == "duplicate ownership conflict"
    assert audit_row.meta["preserved_owner_id"] == str(world.a.id)

    # Re-enable unique index to verify migration now succeeds
    world.db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_organizations_domain ON organizations (lower(domain))"))
    world.db.commit()


def test_6_legitimate_owner_domain_remains_unchanged(world, feature):
    """6. Legitimate owner's domain and verified state remains intact."""
    target_domain = "events.zoikogroup.com"
    v_time = datetime.now(timezone.utc)

    a = fresh(world, world.a)
    a.domain = target_domain
    a.domain_status = "active"
    a.domain_verified = True
    a.custom_domain_enabled = True
    a.domain_verified_at = v_time
    world.db.commit()

    repair_conflict(world.db.bind, target_domain, keep_org_id=str(world.a.id))

    a_fresh = fresh(world, world.a)
    assert a_fresh.domain == target_domain
    assert a_fresh.domain_status == "active"
    assert a_fresh.domain_verified is True
    assert a_fresh.custom_domain_enabled is True
    assert a_fresh.domain_verified_at == v_time


def test_7_application_returns_409_for_duplicate_claim(world, feature):
    """7. Application returns 409 for duplicate claim with clean business error."""
    h = host()
    # Org A registers domain
    r1 = client_for(world.admin_a).patch("/api/organization/domain", json={"domain": h})
    assert r1.status_code == 200

    # Org B attempts to claim same domain
    r2 = client_for(world.admin_b).patch("/api/organization/domain", json={"domain": h.upper()})
    assert r2.status_code == 409
    assert r2.json()["detail"] == "This custom domain is already registered to another organization."


def test_8_no_stale_active_state_remains_after_domain_removal(world, feature):
    """8. No stale active/enabled state remains when domain is removed."""
    h = host()
    r1 = client_for(world.admin_a).patch("/api/organization/domain", json={"domain": h})
    assert r1.status_code == 200

    r2 = client_for(world.admin_a).delete("/api/organization/domain")
    assert r2.status_code == 200
    assert r2.json()["status"] == "not_configured"
    assert r2.json()["domain"] is None

    a = fresh(world, world.a)
    assert a.domain is None
    assert a.domain_status == "not_configured"
    assert a.custom_domain_enabled is False
    assert a.domain_verified is False

    # Also verify migration statement 5 cleans up any legacy inconsistent rows
    world.db.execute(
        text("UPDATE organizations SET domain = NULL, domain_status = 'active', custom_domain_enabled = TRUE WHERE id = :id"),
        {"id": world.b.id},
    )
    world.db.commit()

    world.db.execute(text(_CUSTOM_DOMAIN_STATEMENTS[4]))
    world.db.commit()

    b = fresh(world, world.b)
    assert b.domain_status == "not_configured"
    assert b.custom_domain_enabled is False
    assert b.domain_verified is False


def test_9_unique_index_prevents_race_condition_duplicate_inserts(world, feature):
    """9. Unique index prevents race-condition duplicate inserts."""
    h = host()
    a = fresh(world, world.a)
    a.domain = h.lower()
    world.db.commit()

    # Direct database insert/update bypassing application layer
    b = fresh(world, world.b)
    b.domain = h.upper()
    with pytest.raises(IntegrityError):
        world.db.commit()
    world.db.rollback()
