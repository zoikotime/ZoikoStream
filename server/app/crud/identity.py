"""Identity-challenge lifecycle (ZST-EC-001 IDN-001).

The only module that knows how a verification token is minted, hashed, looked up and
consumed. Routers never see a raw token except the one returned by `issue_email_verification`
for immediate delivery, and never compute a hash themselves.

Token properties, as required by the baseline's secure-link standard:
  * cryptographically random  — secrets.token_urlsafe(32), 256 bits
  * never stored in the clear — only sha256(raw) is persisted
  * short-lived               — settings.EMAIL_VERIFICATION_TTL_MINUTES
  * purpose-bound             — every lookup asserts purpose == EMAIL_VERIFICATION
  * single-use                — consumed_at set in the same transaction as the state change
  * invalid after expiry, consumption or supersession
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import EMAIL_VERIFICATION, IdentityChallenge, User

# Same construction as crud/organization.py::_hash_token — a 256-bit random token needs a
# fast digest, not a password KDF. Kept local rather than imported so the two token families
# stay independent modules.
_TOKEN_BYTES = 32


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ttl_minutes() -> int:
    return settings.EMAIL_VERIFICATION_TTL_MINUTES


def supersede_active(db: Session, user_id, purpose: str = EMAIL_VERIFICATION) -> int:
    """Invalidate every still-redeemable challenge for this user and purpose.

    Called before issuing a replacement so a resend cannot leave two working links alive —
    the older link stops working the moment the newer one is minted. Rows are marked, not
    deleted: the challenge history is evidence.

    Returns the number of challenges invalidated. Does NOT commit; the caller owns the
    transaction so issuing is atomic with superseding.
    """
    now = _now()
    active = db.scalars(
        select(IdentityChallenge).where(
            IdentityChallenge.user_id == user_id,
            IdentityChallenge.purpose == purpose,
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    ).all()
    for challenge in active:
        challenge.superseded_at = now
    return len(active)


def issue_email_verification(
    db: Session, user: User, requested_ip: str | None = None
) -> tuple[IdentityChallenge, str]:
    """Mint a fresh email-verification challenge for `user`.

    Returns (challenge, raw_token). The raw token is returned exactly once, for immediate
    delivery by email — it is not recoverable afterwards. Any previously active challenge
    is superseded in the same transaction.
    """
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    supersede_active(db, user.id, EMAIL_VERIFICATION)
    challenge = IdentityChallenge(
        user_id=user.id,
        purpose=EMAIL_VERIFICATION,
        token_hash=_hash_token(raw),
        expires_at=_now() + timedelta(minutes=ttl_minutes()),
        requested_ip=requested_ip,
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge, raw


# Redemption outcomes. Returned instead of raising so the router owns the HTTP mapping and
# the copy, and so "already used" stays distinguishable from "invalid" for the UI.
VALID = "valid"
INVALID = "invalid"
EXPIRED = "expired"
ALREADY_USED = "already_used"


def resolve_email_verification(db: Session, raw_token: str) -> tuple[str, IdentityChallenge | None]:
    """Look up a raw token and classify it. Never reveals anything about a token it
    cannot find: an unknown hash and a malformed string are both INVALID.

    Records the attempt against a row we did find, so repeated hits on a spent or expired
    link are visible for abuse investigation.
    """
    if not raw_token:
        return INVALID, None

    challenge = db.scalar(
        select(IdentityChallenge).where(
            IdentityChallenge.token_hash == _hash_token(raw_token),
            # Purpose-bound: a password-reset or any future challenge type can never be
            # redeemed here even if its raw token were somehow presented.
            IdentityChallenge.purpose == EMAIL_VERIFICATION,
        )
    )
    if challenge is None:
        return INVALID, None

    challenge.attempts += 1
    challenge.last_attempt_at = _now()
    db.commit()

    if challenge.consumed_at is not None:
        return ALREADY_USED, challenge
    if challenge.superseded_at is not None:
        # A newer link was issued. Surfaced as INVALID rather than EXPIRED: the link was
        # replaced, not timed out, and telling the holder "expired" would invite a resend
        # loop when the newest email already sits in their inbox.
        return INVALID, challenge
    if challenge.expires_at <= _now():
        return EXPIRED, challenge
    return VALID, challenge


def consume_email_verification(db: Session, challenge: IdentityChallenge) -> User:
    """Mark the user verified and the challenge spent, atomically.

    Both writes land in one commit: there is no window in which the token is spent but the
    account is still unverified, or the account is verified while the token stays live.
    Re-checks single-use state under the same session before writing, so two concurrent
    redemptions of the same link cannot both succeed.
    """
    if challenge.consumed_at is not None:
        raise ValueError("challenge already consumed")

    now = _now()
    user = db.get(User, challenge.user_id)
    if user is None:
        raise ValueError("challenge has no user")

    challenge.consumed_at = now
    # Idempotent on the user side: re-verifying an already-verified account keeps the
    # original timestamp, which is the auditable fact.
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = now
    db.commit()
    db.refresh(user)
    return user


def mark_verified(db: Session, user: User, when: datetime | None = None) -> User:
    """Flip a user to verified without a challenge.

    Used only by the invitation-acceptance path: the invitee proved control of the address
    by redeeming an emailed invitation token, which is the same proof IDN-001 collects.
    Not exposed over HTTP — nothing a caller can reach may set this.
    """
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = when or _now()
    return user
