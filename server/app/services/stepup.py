"""Step-up authentication (ZST-EC-001 ORG-003 / ORG-008).

    re-verify password  ->  purpose-bound grant (5 min, single use)  ->  high-risk operation

`issue()` is the only way a grant comes into existence, and it takes the plaintext password
and checks it. There is no path that mints a grant from an existing session, because "the
user logged in three hours ago" is precisely the assurance step-up exists to replace.

`consume()` is single-use and re-checks purpose, owner and expiry inside one call, so a
caller cannot accidentally validate a grant and then forget to spend it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import STEP_UP_PURPOSES, STEP_UP_TTL_MINUTES, StepUpGrant, User
from ..security import verify_password

log = logging.getLogger(__name__)

# Outcomes, so routers map policy to status codes without re-deriving it.
OK = "ok"
BAD_CREDENTIALS = "bad_credentials"
UNKNOWN_PURPOSE = "unknown_purpose"
INVALID = "invalid"
EXPIRED = "expired"
WRONG_PURPOSE = "wrong_purpose"
ALREADY_USED = "already_used"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue(db: Session, user: User, *, password: str, purpose: str,
          ip: str | None = None) -> tuple[str, str | None]:
    """Re-verify the holder's password and mint a purpose-bound grant.

    Returns (outcome, raw_reference). The raw reference is returned exactly once and is
    never recoverable afterwards.
    """
    if purpose not in STEP_UP_PURPOSES:
        return UNKNOWN_PURPOSE, None
    if not user.password_hash or not verify_password(password, user.password_hash):
        # Deliberately the same outcome whatever the reason — this is a credential check.
        return BAD_CREDENTIALS, None

    # Any earlier unspent grant for this purpose is retired, so a user cannot accumulate
    # several live step-ups by re-authenticating repeatedly.
    db.execute(
        update(StepUpGrant)
        .where(StepUpGrant.user_id == user.id, StepUpGrant.purpose == purpose,
               StepUpGrant.consumed_at.is_(None))
        .values(consumed_at=_now(), consumed_for="superseded")
    )
    raw = secrets.token_urlsafe(32)
    db.add(StepUpGrant(
        user_id=user.id, purpose=purpose, token_hash=_hash(raw),
        expires_at=_now() + timedelta(minutes=STEP_UP_TTL_MINUTES),
        requested_ip=ip,
    ))
    db.commit()
    return OK, raw


def consume(db: Session, user: User, *, reference: str | None, purpose: str,
            spent_on: str | None = None) -> str:
    """Spend one grant. Returns OK only for a live, unspent grant of the right purpose
    belonging to this user.

    Spending is a conditional UPDATE, so two concurrent high-risk operations cannot both
    succeed on one re-authentication.
    """
    if not reference:
        return INVALID
    grant = db.scalar(select(StepUpGrant).where(StepUpGrant.token_hash == _hash(reference)))
    if grant is None or grant.user_id != user.id:
        return INVALID
    if grant.purpose != purpose:
        # A grant minted to change a role must not authorize an ownership transfer.
        return WRONG_PURPOSE
    if grant.consumed_at is not None:
        return ALREADY_USED
    if grant.expires_at <= _now():
        return EXPIRED

    claimed = db.execute(
        update(StepUpGrant)
        .where(StepUpGrant.id == grant.id, StepUpGrant.consumed_at.is_(None))
        .values(consumed_at=_now(), consumed_for=spent_on)
    ).rowcount
    db.commit()
    return OK if claimed else ALREADY_USED


def describe(outcome: str) -> str:
    """Message for a refused step-up. Never reveals which part failed beyond the category."""
    return {
        BAD_CREDENTIALS: "Password re-verification failed.",
        UNKNOWN_PURPOSE: "Unsupported step-up purpose.",
        WRONG_PURPOSE: "This verification was issued for a different action.",
        EXPIRED: "The verification expired. Re-verify and try again.",
        ALREADY_USED: "That verification has already been used.",
    }.get(outcome, "Re-verify your password to continue.")
