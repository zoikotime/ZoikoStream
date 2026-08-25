"""Identity-security layer for ZST-EC-001 IDN-003, IDN-004 and IDN-005.

One place that owns the whole path:

    authentication event -> sign-in context -> risk decision -> committed state -> template

The router calls two functions (`evaluate_sign_in` and `record_*`) and never decides on its
own whether to send anything. That keeps `send_*` calls out of the auth router and means the
rules can be read, tested and changed in one file.

WHAT THE RISK LAYER IS
    A deterministic, explainable rule set over durable rows in `sign_in_events`. Two counters
    and a set-membership test. There is no scoring model, no ML, and nothing adaptive — the
    baseline requires that a block be defensible, and "the model said so" is not defensible.

WHAT IT DELIBERATELY IS NOT
    It does not fingerprint browsers, does not store raw User-Agents or full IP addresses,
    and does not geolocate. Where a fact is unavailable it is rendered as unavailable rather
    than guessed.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ALLOW,
    ALLOW_NEW_CONTEXT,
    AUTH_METHOD_PASSWORD,
    BLOCK_SUSPICIOUS,
    SignInEvent,
    User,
)

log = logging.getLogger(__name__)

# ── Risk thresholds ─────────────────────────────────────────────────────────────────────
# Internal only. Never rendered into an email, never returned in an API response: the
# baseline forbids disclosing detection logic or thresholds to a recipient.
#
# Tuned so that a person mistyping their password several times is NEVER blocked — the
# window is short and the count is well above human fumbling — while a sustained guessing
# run against one account is. A single wrong password is an ordinary 401 and nothing else.
_FAILURE_WINDOW = timedelta(minutes=15)
_FAILURES_BEFORE_BLOCK = 10

# How long a block persists once triggered. The attacker keeps getting blocked; the account
# holder is told once (see _BLOCK_NOTICE_COOLDOWN) rather than once per attempt.
_BLOCK_WINDOW = timedelta(minutes=15)

# A burst of identical blocked attempts is one security event to a human. Only the first
# inside this window produces IDN-004.
_BLOCK_NOTICE_COOLDOWN = timedelta(hours=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_reference() -> str:
    """Non-secret correlation id for one authentication event.

    Random rather than derived from the JWT so that knowing it reveals nothing about the
    session and it can be safely printed in an email and quoted to Support. It authorizes
    nothing: no endpoint accepts it.
    """
    return secrets.token_hex(8)


# ── Context normalization ───────────────────────────────────────────────────────────────
# Ordered longest-token-first so Edge is not reported as Chrome and Chrome is not reported
# as Safari. Both lie about themselves in the User-Agent string, which is why the order
# matters more than the list.
_BROWSERS = (
    ("edg", "Edge"),
    ("opr", "Opera"),
    ("chrome", "Chrome"),
    ("firefox", "Firefox"),
    ("safari", "Safari"),
)
_PLATFORMS = (
    ("android", "Android"),
    ("iphone", "iPhone"),
    ("ipad", "iPad"),
    ("windows", "Windows"),
    ("mac os", "macOS"),
    ("macintosh", "macOS"),
    ("linux", "Linux"),
)


def parse_user_agent(user_agent: str | None) -> tuple[str | None, str | None]:
    """Reduce a User-Agent to (browser family, platform family).

    A coarse bucket is the point, not accuracy: this feeds a "have I seen this kind of
    machine before" test. Over-precision would make every browser update look like a new
    device and email the user for nothing. Unrecognized input stays None and is rendered as
    unavailable — never as a raw User-Agent, which the baseline treats as unnecessary data.
    """
    if not user_agent:
        return None, None
    ua = user_agent.lower()
    browser = next((label for token, label in _BROWSERS if token in ua), None)
    platform = next((label for token, label in _PLATFORMS if token in ua), None)
    return browser, platform


def network_context(ip: str | None) -> str | None:
    """Coarse network bucket: IPv4 /24, IPv6 /48.

    Enough to notice "this is somewhere else"; not enough to locate a person, and not the
    full address, which the baseline calls unnecessarily precise IP information.
    """
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version == 4:
        return str(ipaddress.ip_network(f"{addr}/24", strict=False))
    return str(ipaddress.ip_network(f"{addr}/48", strict=False))


def approximate_location(_ip: str | None) -> str | None:
    """Always None today.

    No geolocation provider is configured anywhere in this repository. Returning None makes
    the templates render "Approximate location unavailable", which is true. Fabricating a
    city from an IP range would be worse than saying nothing, and the baseline explicitly
    forbids inventing precise geolocation. Wire a provider here and every template picks it
    up with no further change.
    """
    return None


def describe_device(browser: str | None, platform: str | None) -> str:
    """Human-readable device string — "Chrome on Windows", never a raw User-Agent."""
    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Unrecognized device"


def describe_location(location: str | None) -> str:
    return location or "Approximate location unavailable"


# ── Risk evaluation ─────────────────────────────────────────────────────────────────────

def _recent_failures(db: Session, user_id) -> int:
    """Failed or blocked attempts against this account inside the protection window."""
    return db.query(SignInEvent).filter(
        SignInEvent.user_id == user_id,
        SignInEvent.outcome.in_(("failed", "blocked")),
        SignInEvent.occurred_at >= _now() - _FAILURE_WINDOW,
    ).count()


def is_locked_out(db: Session, user: User) -> bool:
    """True when this account is inside an active block window.

    Checked BEFORE the password is verified, so a correct password offered during a guessing
    run does not grant access — which is the whole point of blocking.
    """
    if _recent_failures(db, user.id) < _FAILURES_BEFORE_BLOCK:
        return False
    latest = db.query(SignInEvent).filter(
        SignInEvent.user_id == user.id,
        SignInEvent.outcome.in_(("failed", "blocked")),
    ).order_by(SignInEvent.occurred_at.desc()).first()
    return bool(latest and latest.occurred_at >= _now() - _BLOCK_WINDOW)


def is_new_context(db: Session, user: User, browser: str | None, platform: str | None) -> bool:
    """True when no earlier SUCCESSFUL sign-in matched this browser/platform pair.

    Signals used, and only these:
      * no prior successful sign-in at all  -> new (first sign-in; see policy note below)
      * browser family not seen before      -> new
      * platform family not seen before     -> new

    Network context is deliberately NOT a trigger. Mobile networks re-bucket constantly and
    a laptop moving between office and home would email the user daily for nothing. It is
    recorded on the row for investigation, but it does not by itself make a context new.

    POLICY — first sign-in: the first successful sign-in after activation has no prior
    context to match, so it is treated as new and produces exactly one IDN-003. That is the
    conservative reading: the account holder learns the account is in use, and an attacker
    who verified a stolen address cannot sign in silently even once.
    """
    prior = db.query(SignInEvent).filter(
        SignInEvent.user_id == user.id,
        SignInEvent.outcome == "success",
    )
    if prior.count() == 0:
        return True
    return prior.filter(
        SignInEvent.browser_family == browser,
        SignInEvent.platform_family == platform,
    ).count() == 0


def evaluate_sign_in(db: Session, user: User | None) -> str:
    """Risk decision taken BEFORE credentials are checked.

    Returns BLOCK_SUSPICIOUS or ALLOW. The new-context distinction is made after the
    password verifies, because an unverified claim about who is signing in is worthless.
    Unknown account -> ALLOW: the caller still fails on credentials, and treating an unknown
    address differently would leak whether it exists.
    """
    if user is None:
        return ALLOW
    return BLOCK_SUSPICIOUS if is_locked_out(db, user) else ALLOW


# ── Recording ───────────────────────────────────────────────────────────────────────────

def record_event(
    db: Session,
    *,
    user: User | None,
    outcome: str,
    ip: str | None,
    user_agent: str | None,
    risk_decision: str = ALLOW,
    is_new: bool = False,
    method: str = AUTH_METHOD_PASSWORD,
) -> SignInEvent:
    """Persist one authentication decision and return the row.

    Committed before any email is queued: the security record is the authoritative fact, and
    a mail outage must never be able to erase it.
    """
    browser, platform = parse_user_agent(user_agent)
    event = SignInEvent(
        user_id=user.id if user else None,
        session_reference=new_session_reference(),
        occurred_at=_now(),
        outcome=outcome,
        authentication_method=method,
        browser_family=browser,
        platform_family=platform,
        network_context=network_context(ip),
        approximate_location=approximate_location(ip),
        is_new_context=is_new,
        risk_decision=risk_decision,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def claim_notification(db: Session, event: SignInEvent) -> bool:
    """Claim the right to send this event's notification exactly once.

    Mirrors crud.identity.claim_account_ready: a conditional UPDATE, so a retried request or
    a restarted worker cannot produce a second email for the same event.
    """
    from sqlalchemy import update

    updated = db.execute(
        update(SignInEvent)
        .where(SignInEvent.id == event.id, SignInEvent.notified_at.is_(None))
        .values(notified_at=_now())
    ).rowcount
    db.commit()
    if updated:
        db.refresh(event)
        return True
    return False


def block_notice_already_sent(db: Session, user: User) -> bool:
    """True when this account was already told about a block recently.

    A guessing run produces one blocked row per attempt. To a person that is one event, so
    only the first inside the cooldown is notified — the rest are recorded and silent.
    """
    return db.query(SignInEvent).filter(
        SignInEvent.user_id == user.id,
        SignInEvent.outcome == "blocked",
        SignInEvent.notified_at.isnot(None),
        SignInEvent.notified_at >= _now() - _BLOCK_NOTICE_COOLDOWN,
    ).count() > 0
