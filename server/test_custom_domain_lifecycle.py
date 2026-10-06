"""Custom domain lifecycle through the real API and the real database.

Before this, PATCH /organization/domain stored a string, nothing ever set domain_verified,
two organizations could hold the same hostname, and nothing proved ownership. Pinned here:

  save + normalize + per-claim token     duplicate refused (app check AND unique index)
  invalid hostname refused               feature unavailable -> 503, no broken instructions
  TXT + CNAME -> verified                wrong TXT / missing CNAME / NXDOMAIN / timeout
  verified -> active ONLY after TLS      certificate refused -> failed
  change resets everything               remove disables and links fall back
  super-admin editor uses the same path  staff can disable, never mark verified
  automatic re-checks, expiry, grace     every step audited, no token in any audit row
"""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Organization
from app.services import custom_domains
from app.services import custom_domain_routing as routing
from custom_domain_support import (TARGET, activate, audit_actions, client_for, elevate, feature, fresh,  # noqa: F401
                                   host, world)


def save(w, user, hostname):
    return client_for(user).patch("/api/organization/domain", json={"domain": hostname})


def verify(w, user):
    return client_for(user).post("/api/organization/domain/verify")


# ── save ────────────────────────────────────────────────────────────────────────────────

def test_save_normalizes_and_returns_dns_instructions(world, feature):
    h = host()
    r = save(world, world.admin_a, h.upper() + ".")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domain"] == h and body["status"] == "pending_dns" and body["available"] is True
    assert body["domain_verified"] is False and body["public_url"] is None
    o = fresh(world, world.a)
    assert len(o.domain_verification_token) == 64
    assert body["dns_records"] == [
        {"type": "CNAME", "name": h, "value": TARGET, "purpose": "Routes your event pages to ZoikoStream"},
        {"type": "TXT", "name": f"_zoikostream.{h}",
         "value": f"zoikostream-domain-verification={o.domain_verification_token}",
         "purpose": "Proves your organization owns this domain"},
    ]
    assert [a.action for a in audit_actions(world, world.a)] == ["custom_domain.requested"]


def test_every_claim_gets_its_own_token(world, feature):
    save(world, world.admin_a, host())
    save(world, world.admin_b, host())
    ta, tb = fresh(world, world.a).domain_verification_token, fresh(world, world.b).domain_verification_token
    assert ta != tb
    save(world, world.admin_a, host())                        # a new hostname -> a new token
    assert fresh(world, world.a).domain_verification_token != ta


def test_resaving_the_same_hostname_changes_nothing(world, feature):
    h = host()
    save(world, world.admin_a, h)
    before = fresh(world, world.a).domain_verification_token
    r = save(world, world.admin_a, h.upper())
    assert r.status_code == 200 and fresh(world, world.a).domain_verification_token == before
    assert len(audit_actions(world, world.a)) == 1


@pytest.mark.parametrize("bad", ["https://events.example.com", "events.example.com/x", "10.0.0.1",
                                 "*.example.com", "localhost", "get.zoikostream.com", "a..b.com"])
def test_invalid_hostname_is_refused_and_changes_nothing(world, feature, bad):
    r = save(world, world.admin_a, bad)
    assert r.status_code == 422
    assert ("body", "domain") in {tuple(e["loc"]) for e in r.json()["detail"]}
    assert fresh(world, world.a).domain is None


def test_a_hostname_held_by_another_organization_is_refused(world, feature):
    h = host()
    assert save(world, world.admin_a, h).status_code == 200
    r = save(world, world.admin_b, h.upper())
    assert r.status_code == 409
    assert "already registered to another" in r.json()["detail"]
    assert fresh(world, world.b).domain is None
    assert fresh(world, world.a).domain == h


def test_the_database_itself_refuses_a_duplicate(world, feature):
    """Two saves racing past the application check still cannot both land."""
    h = host()
    save(world, world.admin_a, h)
    b = world.db.get(Organization, world.b.id)
    b.domain = h.upper()                                      # case-insensitive index
    with pytest.raises(IntegrityError):
        world.db.commit()
    world.db.rollback()


def test_unavailable_feature_refuses_new_domains_and_shows_no_instructions(world, feature, monkeypatch):
    monkeypatch.setattr(custom_domains.domain_provider, "get_provider", lambda: None)
    r = save(world, world.admin_a, host())
    assert r.status_code == 503 and r.json()["detail"] == "Custom domains are temporarily unavailable."
    g = client_for(world.admin_a).get("/api/organization/domain").json()
    assert g["available"] is False and g["dns_records"] == [] and g["cname_target"] is None
    assert verify(world, world.admin_a).status_code == 503


def test_unresolvable_cname_target_means_unavailable(world, feature, monkeypatch):
    monkeypatch.setattr(custom_domains, "_target_resolves", lambda target: False)
    assert custom_domains.availability().reason == "cname_target_unresolved"
    assert save(world, world.admin_a, host()).status_code == 503


# ── verification ────────────────────────────────────────────────────────────────────────

def test_full_lifecycle_to_active_only_after_tls(world, feature):
    h = host()
    save(world, world.admin_a, h)
    token = fresh(world, world.a).domain_verification_token
    feature.publish(h, token)

    # DNS proven, certificate still being issued -> verified, not active.
    body = verify(world, world.admin_a).json()
    assert body["status"] == "verified" and body["domain_verified"] is True and body["error"] is None
    assert body["certificate_status"] == "pending_validation" and body["public_url"] is None
    assert feature.provider.created == [h]
    assert feature.probe["calls"] == 0                       # no probe before the certificate

    # Certificate live but HTTPS not reaching us yet -> still verified, with the reason.
    feature.provider.ready = True
    feature.probe["code"] = "certificate_invalid"
    body = verify(world, world.admin_a).json()
    assert body["status"] == "verified" and body["error"]["code"] == "certificate_invalid"

    # Everything proven -> active.
    feature.probe["code"] = None
    body = verify(world, world.admin_a).json()
    assert body["status"] == "active" and body["public_url"] == f"https://{h}"
    o = fresh(world, world.a)
    assert o.custom_domain_enabled and o.domain_verified and o.domain_activated_at and o.domain_verified_at
    assert [a.action for a in audit_actions(world, world.a)] == [
        "custom_domain.requested",
        "custom_domain.verification_started", "custom_domain.verified",
        "custom_domain.verification_started", "custom_domain.verification_started",
        "custom_domain.activated",
    ]
    for row in audit_actions(world, world.a):
        assert token not in str(row.meta), "the verification token never goes into the audit trail"


def test_a_cname_alone_never_verifies(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.publish(h, "unused", txt=False)
    body = verify(world, world.admin_a).json()
    assert body["status"] == "pending_dns" and body["error"]["code"] == "txt_missing"
    assert f"_zoikostream.{h}" in body["error"]["message"]


def test_another_claims_token_does_not_verify(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.publish(h, "f" * 64)                              # a token that is not A's
    body = verify(world, world.admin_a).json()
    assert body["status"] == "pending_dns" and body["error"]["code"] == "txt_mismatch"
    assert fresh(world, world.a).domain_verified is False


def test_missing_and_wrong_cname_are_explained(world, feature):
    h = host()
    save(world, world.admin_a, h)
    token = fresh(world, world.a).domain_verification_token
    feature.publish(h, token, cname=None)
    feature.records[(h, "A")] = ("ok", ["1.2.3.4"])
    body = verify(world, world.admin_a).json()
    assert body["error"]["code"] in ("cname_missing", "nxdomain")
    feature.publish(h, token, cname="other.example.net")
    body = verify(world, world.admin_a).json()
    assert body["error"]["code"] == "cname_mismatch" and "other.example.net" in body["error"]["message"]


def test_nxdomain(world, feature):
    save(world, world.admin_a, host())
    body = verify(world, world.admin_a).json()
    assert body["status"] == "pending_dns" and body["error"]["code"] == "nxdomain"


def test_a_resolver_timeout_changes_no_state(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.records[(h, "CNAME")] = ("timeout", [])
    feature.records[(f"_zoikostream.{h}", "TXT")] = ("timeout", [])
    body = verify(world, world.admin_a).json()
    assert body["status"] == "pending_dns" and body["error"]["code"] == "dns_timeout"


def test_certificate_refusal_marks_failed(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.publish(h, fresh(world, world.a).domain_verification_token)
    feature.provider.failed = True
    body = verify(world, world.admin_a).json()
    assert body["status"] == "failed" and body["error"]["code"] == "certificate_failed"
    assert "custom_domain.activation_failed" in [a.action for a in audit_actions(world, world.a)]
    # Fixing it and verifying again resumes the lifecycle.
    feature.provider.failed, feature.provider.ready = False, True
    assert verify(world, world.admin_a).json()["status"] == "active"


def test_provider_outage_is_retried_not_failed(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.publish(h, fresh(world, world.a).domain_verification_token)
    feature.provider.error = "provider_unreachable"
    body = verify(world, world.admin_a).json()
    assert body["status"] == "verified" and body["error"]["code"] == "provider_unreachable"


# ── change / remove ─────────────────────────────────────────────────────────────────────

def test_changing_an_active_domain_restarts_verification(world, feature):
    old = host()
    old_token = activate(world, feature, world.a, old)
    old_hostname_id = fresh(world, world.a).custom_hostname_id
    new = host()
    body = save(world, world.admin_a, new).json()
    assert body["status"] == "pending_dns" and body["domain"] == new and body["public_url"] is None
    o = fresh(world, world.a)
    assert o.domain_verification_token != old_token
    assert not o.domain_verified and not o.custom_domain_enabled and o.custom_hostname_id is None
    assert old_hostname_id in feature.provider.deleted
    # The new hostname does not activate on its own, even with the old DNS still published.
    assert "custom_domain.changed" in [a.action for a in audit_actions(world, world.a)]
    ev = world.db.get(type(world.event_a), world.event_a.id)
    assert ev.public_watch_url.startswith(custom_domains.settings.APP_URL.rstrip("/"))


def test_removing_disables_and_links_fall_back(world, feature):
    h = host()
    activate(world, feature, world.a, h)
    world.db.expire_all()
    assert world.db.get(type(world.event_a), world.event_a.id).public_watch_url == \
        f"https://{h}/events/{world.event_a.id}/watch"
    hostname_id = fresh(world, world.a).custom_hostname_id
    r = client_for(world.admin_a).delete("/api/organization/domain")
    assert r.status_code == 200 and r.json()["status"] == "not_configured" and r.json()["domain"] is None
    o = fresh(world, world.a)
    assert o.domain is None and o.domain_verification_token is None and not o.custom_domain_enabled
    assert hostname_id in feature.provider.deleted
    world.db.expire_all()
    assert world.db.get(type(world.event_a), world.event_a.id).public_watch_url == \
        f"{custom_domains.settings.APP_URL.rstrip('/')}/events/{world.event_a.id}/watch"
    assert routing.lookup(h) is None
    assert audit_actions(world, world.a)[-1].action == "custom_domain.removed"
    # The hostname is free again for whoever can prove they own it.
    assert save(world, world.admin_b, h).status_code == 200


def test_patch_null_also_removes(world, feature):
    save(world, world.admin_a, host())
    assert save(world, world.admin_a, None).json()["status"] == "not_configured"


def test_only_org_admins_manage_and_see_the_token(world, feature):
    save(world, world.admin_a, host())
    c = client_for(world.host_a)
    assert c.patch("/api/organization/domain", json={"domain": host()}).status_code == 403
    assert c.post("/api/organization/domain/verify").status_code == 403
    assert c.delete("/api/organization/domain").status_code == 403
    seen = c.get("/api/organization/domain").json()
    assert seen["status"] == "pending_dns" and seen["dns_records"] == []


def test_each_admin_acts_only_on_their_own_organization(world, feature):
    h = host()
    save(world, world.admin_a, h)
    client_for(world.admin_b).delete("/api/organization/domain")
    assert fresh(world, world.a).domain == h
    assert client_for(world.admin_b).get("/api/organization/domain").json()["domain"] is None


# ── super-admin editor and support console ──────────────────────────────────────────────

def test_super_admin_editor_uses_the_same_rules(world, feature):
    h = host()
    save(world, world.admin_a, h)
    staff = client_for(world.staff)
    assert staff.patch(f"/api/admin/organizations/{world.b.id}", json={"domain": h}).status_code == 409
    assert staff.patch(f"/api/admin/organizations/{world.b.id}",
                       json={"domain": "https://evil.example/x"}).status_code == 422
    other = host()
    r = staff.patch(f"/api/admin/organizations/{world.b.id}", json={"domain": other.upper()})
    assert r.status_code == 200 and r.json()["domain"] == other and r.json()["domain_status"] == "pending_dns"
    o = fresh(world, world.b)
    assert len(o.domain_verification_token) == 64 and not o.domain_verified
    row = audit_actions(world, world.b)[-1]
    assert row.action == "custom_domain.requested" and row.meta["source"] == "super_admin"
    assert row.actor_id == world.staff.id


def test_super_admin_cannot_mark_a_domain_verified(world, feature):
    h = host()
    save(world, world.admin_a, h)
    r = client_for(world.staff).patch(f"/api/admin/organizations/{world.a.id}", json={
        "domain_verified": True, "domain_status": "active", "custom_domain_enabled": True})
    assert r.status_code == 200
    o = fresh(world, world.a)
    assert o.domain_status == "pending_dns" and not o.domain_verified and not o.custom_domain_enabled


def test_support_console_lists_without_tokens_and_can_recheck(world, feature):
    h = host()
    save(world, world.admin_a, h)
    token = fresh(world, world.a).domain_verification_token
    staff = client_for(world.staff)
    body = staff.get("/api/admin/custom-domains").json()
    assert body["availability"]["available"] is True and body["availability"]["cname_target"] == TARGET
    (row,) = [i for i in body["items"] if i["org_id"] == str(world.a.id)]
    assert row["domain"] == h and row["status"] == "pending_dns" and row["requested_at"]
    assert token not in str(body)
    feature.publish(h, token)
    feature.provider.ready = True
    r = staff.post(f"/api/admin/custom-domains/{world.a.id}/verify")
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert r.json()["cname_ok"] is True and r.json()["txt_ok"] is True
    assert client_for(world.admin_a).get("/api/admin/custom-domains").status_code == 403


def test_disable_needs_elevation_then_stops_serving_and_reenable_restarts(world, feature):
    h = host()
    activate(world, feature, world.a, h)
    staff = client_for(world.staff)
    assert staff.post(f"/api/admin/custom-domains/{world.a.id}/disable", json={"reason": "abuse report"}).status_code == 403
    elevate(world, world.staff)
    r = staff.post(f"/api/admin/custom-domains/{world.a.id}/disable", json={"reason": "abuse report"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert not fresh(world, world.a).custom_domain_enabled
    assert routing.lookup(h).status == "disabled"
    # The organization cannot verify its way back.
    assert verify(world, world.admin_a).status_code == 409
    row = audit_actions(world, world.a)[-1]
    assert row.action == "custom_domain.disabled" and row.meta["reason"] == "abuse report"
    r = staff.post(f"/api/admin/custom-domains/{world.a.id}/enable")
    assert r.status_code == 200 and r.json()["status"] == "pending_dns"


def test_support_can_release_an_unverified_claim(world, feature):
    h = host()
    save(world, world.admin_a, h)
    staff = client_for(world.staff)
    elevate(world, world.staff)
    assert staff.post(f"/api/admin/custom-domains/{world.a.id}/release", json={"reason": "owner asked"}).status_code == 204
    assert fresh(world, world.a).domain is None
    assert save(world, world.admin_b, h).status_code == 200


# ── automatic re-checks ─────────────────────────────────────────────────────────────────

def test_sweeper_verifies_pending_domains_without_a_click(world, feature):
    h = host()
    save(world, world.admin_a, h)
    feature.publish(h, fresh(world, world.a).domain_verification_token)
    feature.provider.ready = True
    db = world.db
    custom_domains.sweep(db)
    assert fresh(world, world.a).domain_status == "active"
    row = [a for a in audit_actions(world, world.a) if a.action == "custom_domain.activated"][0]
    assert row.meta.get("automatic") is True and row.actor_id is None


def test_sweeper_respects_the_interval(world, feature):
    h = host()
    save(world, world.admin_a, h)
    verify(world, world.admin_a)                              # checked just now
    o = fresh(world, world.a)
    assert custom_domains._due(o, o.domain_last_checked_at + timedelta(minutes=1)) is None
    assert custom_domains._due(o, o.domain_last_checked_at + timedelta(minutes=11)) == "check"


def test_pending_too_long_expires_to_failed(world, feature):
    save(world, world.admin_a, host())
    o = fresh(world, world.a)
    o.domain_status_changed_at = o.domain_status_changed_at - timedelta(days=8)
    world.db.commit()
    custom_domains.sweep(world.db)
    o = fresh(world, world.a)
    assert o.domain_status == "failed" and o.domain_error == "verification_expired"
    # Verify now restarts the window.
    assert verify(world, world.admin_a).json()["status"] == "pending_dns"


def test_active_domain_survives_a_blip_and_is_deactivated_after_grace(world, feature):
    h = host()
    activate(world, feature, world.a, h)
    hostname_id = fresh(world, world.a).custom_hostname_id
    feature.records.clear()                                   # customer removed the records

    body = verify(world, world.admin_a).json()
    assert body["status"] == "active" and body["error"]["code"] == "dns_changed"
    assert body["deactivates_at"] is not None
    o = fresh(world, world.a)
    assert o.custom_domain_enabled and o.domain_failing_since is not None

    o.domain_failing_since = o.domain_failing_since - timedelta(hours=73)
    world.db.commit()
    body = verify(world, world.admin_a).json()
    assert body["status"] == "failed" and body["public_url"] is None
    o = fresh(world, world.a)
    assert not o.custom_domain_enabled and not o.domain_verified and hostname_id in feature.provider.deleted
    row = audit_actions(world, world.a)[-1]
    assert row.action == "custom_domain.disabled" and row.meta["reason"] == "dns_changed"
    assert routing.lookup(h).status == "failed"


def test_active_domain_recovers_when_records_return(world, feature):
    h = host()
    token = activate(world, feature, world.a, h)
    feature.records.clear()
    verify(world, world.admin_a)
    feature.publish(h, token)
    body = verify(world, world.admin_a).json()
    assert body["status"] == "active" and body["error"] is None and body["failing_since"] is None


def test_a_hostname_changed_mid_check_is_not_overwritten(world, feature, monkeypatch):
    first, second = host(), host()
    save(world, world.admin_a, first)
    real_check = custom_domains.domain_dns.check

    def check_then_change(*a, **kw):
        save(world, world.admin_a, second)                    # the admin changes it meanwhile
        return real_check(*a, **kw)

    monkeypatch.setattr(custom_domains.domain_dns, "check", check_then_change)
    custom_domains.verify(world.db, world.a.id, actor=world.admin_a)
    o = fresh(world, world.a)
    assert o.domain == second and o.domain_status == "pending_dns" and o.domain_last_checked_at is None


def test_profile_posture_reflects_real_verification(world, feature):
    h = host()
    activate(world, feature, world.a, h)
    assert client_for(world.admin_a).get("/api/organization/domain").json()["domain_verified"] is True


def test_unknown_org_cannot_be_targeted(world, feature):
    assert client_for(world.staff).post(f"/api/admin/custom-domains/{uuid.uuid4()}/verify").status_code == 404
