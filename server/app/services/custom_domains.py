"""The custom domain lifecycle. Every write to an organization's domain state goes through here.

    not_configured --save--> pending_dns --DNS proven--> verified --certificate + HTTPS--> active
                                 ^   |                     |                                |
                                 |   +--7 days--> failed <-+-- certificate refused          |
                                 |                  |                                       |
                                 +---- Verify now --+     DNS removed, grace expires -------+
    any --staff disable--> disabled --staff re-enable--> pending_dns
    any --remove--> not_configured            any --hostname change--> pending_dns (new token)

`verifying` is the transient state while a check runs; an active domain keeps serving during
its re-checks instead.

What makes each step real:
  * saving needs the feature to be available (provider, CNAME target that resolves, declared
    platform hosts), a valid hostname, and a hostname no other organization holds — the
    database refuses a duplicate even if two saves race (uq_organizations_domain);
  * verified needs BOTH the CNAME to the platform target AND the TXT record carrying this
    organization's own random token (services/domain_dns.py) — a CNAME alone proves nothing
    about who owns the zone;
  * active needs the certificate provider to report the hostname live AND an HTTPS request
    to the hostname to come back with a value only this platform can compute for this
    organization (services/domain_probe.py). Nobody, staff included, can skip to active.

Re-checks: pending domains every 10 minutes for a day then hourly, until
CUSTOM_DOMAIN_PENDING_DAYS; certificate-pending domains every 2 minutes; active domains every
6 hours. An active domain whose records disappear keeps serving for CUSTOM_DOMAIN_GRACE_HOURS,
then is deactivated and its certificate hostname removed, so an abandoned hostname stops
serving instead of lingering.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..domain_names import cname_target, configured_platform_hosts, normalize_custom_hostname
from ..models import AuditLog, Organization
from . import custom_domain_routing as routing
from . import domain_dns, domain_probe, domain_provider
from .domain_provider import ProviderError

log = logging.getLogger(__name__)

NOT_CONFIGURED = "not_configured"
PENDING_DNS = "pending_dns"
VERIFYING = "verifying"
VERIFIED = "verified"
ACTIVE = "active"
FAILED = "failed"
DISABLED = "disabled"
STATUSES = (NOT_CONFIGURED, PENDING_DNS, VERIFYING, VERIFIED, ACTIVE, FAILED, DISABLED)

STALE_CHECK = timedelta(minutes=5)
PENDING_FAST = timedelta(minutes=10)
PENDING_SLOW = timedelta(hours=1)
VERIFIED_INTERVAL = timedelta(minutes=2)
ACTIVE_INTERVAL = timedelta(hours=6)
SWEEP_INTERVAL_SECONDS = 60.0
SWEEP_BATCH = 20
TARGET_CACHE_SECONDS = 600.0

UNAVAILABLE_MESSAGE = "Custom domains are temporarily unavailable."
TAKEN_MESSAGE = "This custom domain is already registered to another organization."
DISABLED_MESSAGE = "This domain was disabled by ZoikoStream support. Contact support to re-enable it."

# Every code that can be stored in organizations.domain_error, in words a customer can act on.
# Raw resolver and provider exceptions never reach a person.
ERROR_MESSAGES = {
    domain_dns.NXDOMAIN_CODE: ("{host} doesn't exist in DNS yet. Add the CNAME record below. New "
                               "records can take up to 48 hours to appear everywhere."),
    domain_dns.CNAME_MISSING: ("CNAME not detected: {host} doesn't point to {target} yet. If your DNS "
                               "is hosted on Cloudflare, set the record to \"DNS only\". DNS changes can "
                               "take up to 48 hours to propagate."),
    domain_dns.CNAME_MISMATCH: "The CNAME for {host} points to {found}, not {target}. Update it to {target}.",
    domain_dns.TXT_MISSING: ("Ownership TXT record not found at {txt_name}. Add it exactly as shown. DNS "
                             "changes can take up to 48 hours to propagate."),
    domain_dns.TXT_MISMATCH: ("A TXT record exists at {txt_name}, but its value doesn't match. Copy the "
                              "value exactly as shown."),
    domain_dns.DNS_TIMEOUT: "DNS lookups timed out. This is usually temporary; we'll check again automatically.",
    domain_dns.DNS_ERROR: "DNS servers for {host} returned an error. We'll check again automatically.",
    "certificate_failed": ("Secure certificate provisioning failed. Make sure the CNAME is still in place "
                           "and that no CAA record blocks certificate issuance, then select Verify now."),
    domain_probe.CERTIFICATE_INVALID: ("{host} answers over HTTPS, but its certificate isn't valid yet. "
                                       "We'll keep checking while it is issued."),
    domain_probe.UNRESOLVED: "{host} doesn't resolve to an address yet. DNS changes can take up to 48 hours.",
    domain_probe.FORBIDDEN: "{host} resolves to a private network address, so it can't be served publicly.",
    domain_probe.UNREACHABLE: ("HTTPS requests to {host} aren't reaching ZoikoStream yet. We'll keep "
                               "checking while the certificate is deployed."),
    domain_probe.NOT_ROUTED: ("HTTPS requests to {host} aren't reaching your ZoikoStream organization yet. "
                              "We'll keep checking."),
    "provider_unreachable": "We couldn't reach the certificate service. We'll retry automatically.",
    "provider_auth": "We couldn't complete certificate setup. We'll retry automatically.",
    "provider_rate_limited": "The certificate service is busy. We'll retry automatically.",
    "provider_rejected": "The certificate service refused this hostname. Contact ZoikoStream support.",
    "provider_error": "We couldn't complete certificate setup. We'll retry automatically.",
    "dns_changed": ("The DNS records for {host} changed or were removed. Restore both records, or event "
                    "pages will stop being served from this domain."),
    "verification_expired": ("We couldn't verify {host} within {days} days, so automatic checks stopped. "
                             "Check both records, then select Verify now."),
    "invalid_hostname": "The saved domain isn't a valid hostname. Change it to continue.",
}


class DomainError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ── availability ─────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str | None = None
    cname_target: str | None = None
    provider: str | None = None


_target_lock = threading.Lock()
_target_cache: dict[str, tuple[float, bool]] = {}


def _target_resolves(target: str) -> bool:
    now = time.monotonic()
    with _target_lock:
        hit = _target_cache.get(target)
    if hit and now - hit[0] < TARGET_CACHE_SECONDS:
        return hit[1]
    ok = domain_dns.resolves(target)
    with _target_lock:
        _target_cache[target] = (now, ok)
    return ok


def availability() -> Availability:
    """Whether customers may be offered custom domains right now. Fails closed."""
    provider = domain_provider.get_provider()
    if provider is None:
        return Availability(False, "provider_unconfigured")
    target = cname_target()
    if not target:
        return Availability(False, "cname_target_unconfigured", provider=provider.name)
    if not configured_platform_hosts():
        return Availability(False, "platform_hosts_unconfigured", target, provider.name)
    if not _target_resolves(target):
        return Availability(False, "cname_target_unresolved", target, provider.name)
    return Availability(True, None, target, provider.name)


# ── state helpers ────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _transition(org: Organization, prev: str, new: str, now: datetime) -> None:
    """The ONE place status and its two mirrors change together."""
    org.domain_status = new
    if new != prev:
        org.domain_status_changed_at = now
    org.domain_verified = new in (VERIFIED, ACTIVE)
    org.custom_domain_enabled = new == ACTIVE


def _clear_progress(org: Organization) -> None:
    org.domain_verified_at = None
    org.domain_activated_at = None
    org.domain_last_checked_at = None
    org.domain_check_started_at = None
    org.domain_failing_since = None
    org.domain_error = None
    org.domain_check = None
    org.custom_hostname_id = None
    org.custom_hostname_status = None
    org.certificate_status = None


def _locked(db: Session, org_id) -> Organization:
    return db.execute(
        select(Organization).where(Organization.id == org_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one()


def _audit(db: Session, org_id, action: str, *, actor=None, ip=None, **meta) -> None:
    """custom_domain.* rows. Hostnames and reasons only: never a verification token or any
    provider credential."""
    data = {k: v for k, v in meta.items() if v is not None}
    if actor is None:
        data["automatic"] = True
    db.add(AuditLog(
        actor_id=actor.id if actor else None, actor_email=actor.email if actor else None,
        action=action, target_type="custom_domain", target_id=str(org_id), org_id=org_id,
        meta=data, ip=ip,
    ))
    db.commit()


def _release_provider(hostname_id: str | None) -> None:
    """Remove a hostname from the certificate provider. If this fails the hostname can still
    reach the platform at the edge, but routing refuses it (it is no longer active)."""
    if not hostname_id:
        return
    provider = domain_provider.get_provider()
    if provider is None:
        log.warning("custom hostname %s left at provider: no provider configured", hostname_id)
        return
    try:
        provider.delete(hostname_id)
    except ProviderError as exc:
        log.warning("custom hostname %s not removed from provider: %s", hostname_id, exc.code)


def _normalize(hostname) -> str | None:
    try:
        return normalize_custom_hostname(hostname)
    except ValueError as exc:
        raise DomainError(422, str(exc)) from None


# ── save / change / remove ───────────────────────────────────────────────────────────────

def check_claimable(db: Session, hostname: str, *, exclude_org_id=None) -> None:
    """Raise unless `hostname` may be requested right now (feature available, nobody else
    holds it). The unique index is the final word; this gives the clear message first."""
    if not availability().available:
        raise DomainError(503, UNAVAILABLE_MESSAGE)
    stmt = select(Organization.id).where(func.lower(Organization.domain) == hostname)
    if exclude_org_id is not None:
        stmt = stmt.where(Organization.id != exclude_org_id)
    if db.scalar(stmt) is not None:
        raise DomainError(409, TAKEN_MESSAGE)


def set_domain(db: Session, org: Organization, hostname, *, actor, ip=None,
               source: str = "organization") -> Organization:
    """Request a hostname, or change to another. A change ALWAYS restarts verification with a
    new token: the old hostname's proof and certificate never carry over."""
    hostname = _normalize(hostname)
    if hostname is None:
        return remove_domain(db, org, actor=actor, ip=ip, source=source)
    if hostname == org.domain:
        return org
    check_claimable(db, hostname, exclude_org_id=org.id)

    previous, previous_hostname_id = org.domain, org.custom_hostname_id
    now = _now()
    org.domain = hostname
    org.domain_verification_token = secrets.token_hex(32)
    _clear_progress(org)
    org.domain_requested_at = now
    _transition(org, org.domain_status, PENDING_DNS, now)
    org.domain_status_changed_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()       # another organization saved it a moment ago
        raise DomainError(409, TAKEN_MESSAGE) from None
    _release_provider(previous_hostname_id)
    routing.invalidate(previous, hostname)
    _audit(db, org.id, "custom_domain.changed" if previous else "custom_domain.requested",
           actor=actor, ip=ip, hostname=hostname, previous_hostname=previous, source=source)
    db.refresh(org)
    return org


def remove_domain(db: Session, org: Organization, *, actor, ip=None, source: str = "organization",
                  reason: str | None = None) -> Organization:
    """Stop serving and forget the hostname. Links fall back to the platform URL at once."""
    if not org.domain:
        return org
    previous, previous_hostname_id = org.domain, org.custom_hostname_id
    org.domain = None
    org.domain_verification_token = None
    org.domain_requested_at = None
    _clear_progress(org)
    _transition(org, org.domain_status, NOT_CONFIGURED, _now())
    db.commit()
    _release_provider(previous_hostname_id)
    routing.invalidate(previous)
    _audit(db, org.id, "custom_domain.removed", actor=actor, ip=ip, hostname=previous,
           source=source, reason=reason)
    db.refresh(org)
    return org


# ── verification ─────────────────────────────────────────────────────────────────────────

def verify(db: Session, org_id, *, actor=None, ip=None, trigger: str = "manual") -> Organization:
    """Check DNS now and move the domain as far along the lifecycle as the evidence allows."""
    avail = availability()
    if not avail.available:
        raise DomainError(503, UNAVAILABLE_MESSAGE)

    org = _locked(db, org_id)
    if not org.domain:
        db.rollback()
        raise DomainError(409, "Add a domain first.")
    if org.domain_status == DISABLED:
        db.rollback()
        raise DomainError(409, DISABLED_MESSAGE)
    if org.domain_error == "invalid_hostname":
        db.rollback()
        raise DomainError(409, ERROR_MESSAGES["invalid_hostname"])
    now = _now()
    if (org.domain_status == VERIFYING and org.domain_check_started_at
            and now - org.domain_check_started_at < STALE_CHECK):
        db.rollback()
        return org          # a check is already running; its result will land on its own
    prev = PENDING_DNS if org.domain_status == VERIFYING else org.domain_status
    host, token = org.domain, org.domain_verification_token
    if prev != ACTIVE:
        org.domain_status = VERIFYING        # transient: the status clock is not restarted
    org.domain_check_started_at = now
    db.commit()
    if trigger != "auto":
        _audit(db, org.id, "custom_domain.verification_started", actor=actor, ip=ip,
               hostname=host, trigger=trigger)

    # Network work happens with no row lock held.
    try:
        result = domain_dns.check(host, token, avail.cname_target)
    except Exception:  # noqa: BLE001 - a resolver library fault is a temporary DNS error
        log.exception("custom-domain dns check crashed")
        result = domain_dns.DnsResult(False, False, error=domain_dns.DNS_ERROR, temporary=True)
    provider_state, provider_error = None, None
    if prev == ACTIVE and result.ok:
        # ensure, not status: a hostname that vanished at the provider is recreated here, and
        # the grace period covers the minutes its certificate takes.
        try:
            provider_state = domain_provider.get_provider().ensure(host, org.custom_hostname_id)
        except ProviderError as exc:
            provider_error = exc.code

    org = _locked(db, org_id)
    if org.domain != host or org.domain_verification_token != token:
        db.commit()
        return org          # the hostname changed while we checked; that change owns the state
    now = _now()
    org.domain_check_started_at = None
    org.domain_last_checked_at = now
    org.domain_check = result.as_json()

    if prev == ACTIVE:
        return _recheck_active(db, org, result, provider_state, provider_error, now)

    if result.ok:
        if prev != VERIFIED:
            org.domain_verified_at = now
        org.domain_error = None
        _transition(org, prev, VERIFIED, now)
        db.commit()
        routing.invalidate(host)
        if prev != VERIFIED:
            _audit(db, org.id, "custom_domain.verified", actor=actor, ip=ip, hostname=host, trigger=trigger)
        return _provision(db, org, actor=actor, ip=ip)

    org.domain_error = result.error
    if result.temporary:
        _transition(org, prev, prev, now)            # resolver trouble: no change of state
    else:
        _transition(org, prev, PENDING_DNS, now)     # records missing or wrong
    db.commit()
    routing.invalidate(host)
    return org


def _provision(db: Session, org: Organization, *, actor=None, ip=None) -> Organization:
    """Verified -> certificate -> HTTPS probe -> active. Re-entrant: every step is idempotent."""
    host, org_id = org.domain, org.id
    provider = domain_provider.get_provider()
    if provider is None:
        org.domain_error = "provider_unreachable"
        db.commit()
        return org
    try:
        state = provider.ensure(host, org.custom_hostname_id)
    except ProviderError as exc:
        org.domain_error = exc.code
        db.commit()
        return org
    probe_error = domain_probe.probe(host, org_id) if state.ready and not state.failed else None

    org = _locked(db, org_id)
    if org.domain != host or org.domain_status != VERIFIED:
        db.commit()
        return org
    now = _now()
    org.custom_hostname_id = state.hostname_id
    org.custom_hostname_status = state.hostname_status
    org.certificate_status = state.certificate_status
    if state.failed:
        org.domain_error = "certificate_failed"
        _transition(org, VERIFIED, FAILED, now)
        db.commit()
        routing.invalidate(host)
        _audit(db, org_id, "custom_domain.activation_failed", actor=actor, ip=ip, hostname=host,
               reason="certificate_failed", certificate_status=state.certificate_status)
        return org
    if not state.ready:
        org.domain_error = None                     # certificate still being issued: not an error
        db.commit()
        return org
    if probe_error:
        org.domain_error = probe_error
        db.commit()
        return org
    org.domain_error = None
    org.domain_failing_since = None
    org.domain_activated_at = now
    _transition(org, VERIFIED, ACTIVE, now)
    db.commit()
    routing.invalidate(host)
    _audit(db, org_id, "custom_domain.activated", actor=actor, ip=ip, hostname=host,
           public_url=f"https://{host}")
    return org


def _recheck_active(db: Session, org: Organization, result, provider_state, provider_error,
                    now: datetime) -> Organization:
    host = org.domain
    if result.temporary or provider_error:
        db.commit()                                 # nothing learned about the domain itself
        return org
    problem = None if result.ok else "dns_changed"
    if provider_state is not None:
        if provider_state.hostname_id:
            org.custom_hostname_id = provider_state.hostname_id
        org.custom_hostname_status = provider_state.hostname_status
        org.certificate_status = provider_state.certificate_status
        if not provider_state.ready:
            problem = "certificate_failed"
    if problem is None:
        org.domain_failing_since = None
        org.domain_error = None
        db.commit()
        return org
    org.domain_error = problem
    grace = timedelta(hours=settings.CUSTOM_DOMAIN_GRACE_HOURS)
    if org.domain_failing_since is None:
        org.domain_failing_since = now
    if now - org.domain_failing_since < grace:
        db.commit()
        return org
    # Grace exhausted: stop serving, and take the hostname off the certificate provider.
    previous_hostname_id = org.custom_hostname_id
    org.custom_hostname_id = None
    org.custom_hostname_status = None
    org.certificate_status = None
    org.domain_failing_since = None
    org.domain_activated_at = None
    org.domain_verified_at = None
    _transition(org, ACTIVE, FAILED, now)
    db.commit()
    routing.invalidate(host)
    _release_provider(previous_hostname_id)
    _audit(db, org.id, "custom_domain.disabled", hostname=host, reason=problem)
    return org


# ── staff actions (support console) ──────────────────────────────────────────────────────

def disable(db: Session, org: Organization, *, actor, ip=None, reason: str) -> Organization:
    """Stop a hostname serving, immediately. There is deliberately no staff action that
    marks a domain verified or active: those states come only from the evidence."""
    if not org.domain:
        raise DomainError(409, "This organization has no custom domain.")
    if org.domain_status == DISABLED:
        return org
    host, previous_hostname_id = org.domain, org.custom_hostname_id
    org.custom_hostname_id = None
    org.custom_hostname_status = None
    org.certificate_status = None
    org.domain_failing_since = None
    org.domain_activated_at = None
    org.domain_error = None
    _transition(org, org.domain_status, DISABLED, _now())
    db.commit()
    routing.invalidate(host)
    _release_provider(previous_hostname_id)
    _audit(db, org.id, "custom_domain.disabled", actor=actor, ip=ip, hostname=host,
           reason=reason, source="support")
    db.refresh(org)
    return org


def enable(db: Session, org: Organization, *, actor, ip=None) -> Organization:
    """Lift a staff disable. The domain starts over at pending_dns and must prove itself."""
    if org.domain_status != DISABLED:
        raise DomainError(409, "This domain isn't disabled.")
    _clear_progress(org)
    _transition(org, DISABLED, PENDING_DNS, _now())
    db.commit()
    routing.invalidate(org.domain)
    _audit(db, org.id, "custom_domain.reenabled", actor=actor, ip=ip, hostname=org.domain, source="support")
    db.refresh(org)
    return org


# ── automatic re-checks ──────────────────────────────────────────────────────────────────

def _due(org: Organization, now: datetime) -> str | None:
    status = org.domain_status
    if status == VERIFYING:
        started = org.domain_check_started_at
        return "check" if started is None or now - started >= STALE_CHECK else None
    since = org.domain_status_changed_at or org.domain_requested_at or now
    window = timedelta(days=settings.CUSTOM_DOMAIN_PENDING_DAYS)
    if status == PENDING_DNS:
        if now - since >= window:
            return "expire"
        interval = PENDING_FAST if now - since < timedelta(days=1) else PENDING_SLOW
    elif status == VERIFIED:
        if now - since >= window:
            return "expire"
        interval = VERIFIED_INTERVAL
    elif status == ACTIVE:
        interval = ACTIVE_INTERVAL
    else:
        return None
    last = org.domain_last_checked_at
    return "check" if last is None or now - last >= interval else None


def _expire(db: Session, org_id, now: datetime) -> None:
    org = _locked(db, org_id)
    if org.domain_status not in (PENDING_DNS, VERIFIED):
        db.commit()
        return
    prev = org.domain_status
    if prev == PENDING_DNS or not org.domain_error:
        org.domain_error = "verification_expired"
    _transition(org, prev, FAILED, now)
    db.commit()
    routing.invalidate(org.domain)
    _audit(db, org_id, "custom_domain.activation_failed", hostname=org.domain,
           reason="verification_expired" if prev == PENDING_DNS else org.domain_error)


def sweep(db: Session, *, limit: int = SWEEP_BATCH) -> int:
    """One pass of automatic checks. Returns how many domains it acted on."""
    if not availability().available:
        return 0
    now = _now()
    rows = db.scalars(
        select(Organization)
        .where(Organization.domain.is_not(None),
               Organization.domain_status.in_((PENDING_DNS, VERIFYING, VERIFIED, ACTIVE)))
        .order_by(Organization.domain_last_checked_at.asc().nulls_first())
        .limit(500)
    ).all()
    due = [(org.id, action) for org in rows if (action := _due(org, now))][:limit]
    for org_id, action in due:
        try:
            if action == "expire":
                _expire(db, org_id, now)
            else:
                verify(db, org_id, trigger="auto")
        except DomainError:
            db.rollback()
        except Exception:  # noqa: BLE001 - one bad domain must not stop the others
            db.rollback()
            log.exception("custom-domain check failed for org %s", org_id)
    return len(due)


async def run_custom_domain_sweeper(interval: float = SWEEP_INTERVAL_SECONDS) -> None:
    """Background ticker (main.py, leader process only)."""
    from ..db import SessionLocal

    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                await asyncio.to_thread(sweep, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("custom-domain sweep failed")


# ── views ────────────────────────────────────────────────────────────────────────────────

def _message(org: Organization, code: str, target: str | None) -> str:
    template = ERROR_MESSAGES.get(code, "We couldn't verify this domain yet. We'll keep checking.")
    check = org.domain_check or {}
    return template.format(
        host=org.domain or "your domain", target=target or "the ZoikoStream CNAME target",
        found=check.get("cname_found") or "another hostname",
        txt_name=domain_dns.txt_name(org.domain or "your-domain"),
        days=settings.CUSTOM_DOMAIN_PENDING_DAYS,
    )


def _deactivates_at(org: Organization) -> datetime | None:
    if org.domain_status == ACTIVE and org.domain_failing_since:
        return org.domain_failing_since + timedelta(hours=settings.CUSTOM_DOMAIN_GRACE_HOURS)
    return None


def public_view(org: Organization) -> dict:
    """GET /organization/domain. The token is included because the owner must publish it;
    it is only ever returned to that organization's own members."""
    avail = availability()
    status = org.domain_status if org.domain else NOT_CONFIGURED
    target = avail.cname_target if avail.available else None
    usable = bool(org.domain) and status != DISABLED and org.domain_error != "invalid_hostname"
    records = []
    if usable and target and org.domain_verification_token:
        records = [
            {"type": "CNAME", "name": org.domain, "value": target,
             "purpose": "Routes your event pages to ZoikoStream"},
            {"type": "TXT", "name": domain_dns.txt_name(org.domain),
             "value": domain_dns.txt_value(org.domain_verification_token),
             "purpose": "Proves your organization owns this domain"},
        ]
    error = None
    if org.domain and org.domain_error:
        error = {"code": org.domain_error, "message": _message(org, org.domain_error, target)}
    return {
        "domain": org.domain,
        "domain_verified": bool(org.domain_verified),
        "status": status,
        "available": avail.available,
        "cname_target": target,
        "dns_records": records,
        "error": error,
        "check": org.domain_check if org.domain else None,
        "certificate_status": org.certificate_status,
        "public_url": f"https://{org.domain}" if status == ACTIVE and org.custom_domain_enabled else None,
        "requested_at": org.domain_requested_at,
        "verified_at": org.domain_verified_at,
        "activated_at": org.domain_activated_at,
        "last_checked_at": org.domain_last_checked_at,
        "failing_since": org.domain_failing_since,
        "deactivates_at": _deactivates_at(org),
        "can_verify": avail.available and usable,
    }


def admin_view(org: Organization, avail: Availability | None = None) -> dict:
    """Support console row. Everything needed to diagnose a domain, and no token."""
    avail = avail or availability()
    check = org.domain_check or {}
    return {
        "org_id": str(org.id),
        "organization": org.name,
        "domain": org.domain,
        "status": org.domain_status,
        "cname_ok": check.get("cname_ok") if check else None,
        "cname_found": check.get("cname_found"),
        "txt_ok": check.get("txt_ok") if check else None,
        "txt_present": check.get("txt_present"),
        "certificate_status": org.certificate_status,
        "custom_hostname_status": org.custom_hostname_status,
        "custom_hostname_id": org.custom_hostname_id,
        "error_code": org.domain_error,
        "error": _message(org, org.domain_error, avail.cname_target) if org.domain_error else None,
        "requested_at": org.domain_requested_at,
        "verified_at": org.domain_verified_at,
        "activated_at": org.domain_activated_at,
        "last_checked_at": org.domain_last_checked_at,
        "failing_since": org.domain_failing_since,
        "deactivates_at": _deactivates_at(org),
    }


def list_for_support(db: Session) -> list[Organization]:
    return list(db.scalars(
        select(Organization).where(Organization.domain.is_not(None))
        .order_by(Organization.domain_requested_at.desc().nulls_last(), Organization.name)
    ).all())
