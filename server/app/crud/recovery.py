"""Account recovery and recovery-contact lifecycle (ZST-EC-001 IDN-006, IDN-007).

Replaces the previous reset mechanism outright. That mechanism stored a 4-digit code in
cleartext on `users.reset_token` with no attempt counter, which meant one database read
handed over every pending reset code on the platform. Nothing here writes a code in the
clear: the code lives only in the email, and only its sha256 is persisted, via the same
`IdentityChallenge` primitive IDN-001 already uses.

Purpose binding is what makes that reuse safe. A challenge carries the job it was minted
for, every lookup asserts it, and an email-verification token can therefore never be
redeemed as a recovery code or vice versa.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import (
    ACCOUNT_RECOVERY,
    RECOVERY_ADDITIONAL_VERIFICATION,
    RECOVERY_CANCELED,
    RECOVERY_COMPLETED,
    RECOVERY_CONTACT,
    RECOVERY_MAX_ATTEMPTS,
    RECOVERY_STARTED,
    RECOVERY_VERIFIED,
    AccountRecovery,
    IdentityChallenge,
    User,
)

# Recovery codes are short-lived by design: the holder is reading one out of their inbox
# right now, and a long window is only ever useful to someone who is not.
RECOVERY_TTL_MINUTES = 15
RECOVERY_LOCKOUT_MINUTES = 30

# Six digits, not four. 10^6 with a 5-attempt lockout and a 15-minute window is a very
# different proposition from 10^4 with unlimited tries, which is what this replaced.
_CODE_DIGITS = 6

# Outcomes of redeeming a code. Returned rather than raised so the router owns the HTTP
# mapping and the copy.
VALID = "valid"
INVALID = "invalid"
EXPIRED = "expired"
LOCKED = "locked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _new_code() -> str:
    """Cryptographically random, zero-padded. `secrets`, never `random`."""
    return f"{secrets.randbelow(10 ** _CODE_DIGITS):0{_CODE_DIGITS}d}"


def mask_destination(address: str | None) -> str:
    """`jane.doe@example.com` -> `j******e@example.com`.

    IDN-006 requires the recovery destination to be shown masked. The holder can recognize
    an address they already know; anyone reading over their shoulder learns nothing new.
    """
    if not address or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        masked = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked = f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"


# ── Recovery lifecycle ──────────────────────────────────────────────────────────────────

def active_recovery(db: Session, user: User) -> AccountRecovery | None:
    """The one recovery still in flight for this user, if any."""
    return db.scalar(
        select(AccountRecovery)
        .where(
            AccountRecovery.user_id == user.id,
            AccountRecovery.status.in_((RECOVERY_STARTED, RECOVERY_ADDITIONAL_VERIFICATION,
                                        RECOVERY_VERIFIED)),
        )
        .order_by(AccountRecovery.started_at.desc())
    )


def start_recovery(db: Session, user: User) -> tuple[AccountRecovery, str, bool]:
    """Begin (or reuse) a recovery and mint a fresh code.

    Returns (recovery, raw_code, is_new). A second request while one is already in flight
    reuses the same recovery row and rotates the code — so "resend" cannot be used to farm
    a fresh attempt budget, because `failed_attempts` lives on the recovery, not the code.
    """
    recovery = active_recovery(db, user)
    is_new = recovery is None
    if recovery is None:
        recovery = AccountRecovery(
            user_id=user.id, status=RECOVERY_STARTED, started_at=_now(),
        )
        db.add(recovery)

    # Any earlier code stops working the moment a new one is minted.
    for stale in db.scalars(
        select(IdentityChallenge).where(
            IdentityChallenge.user_id == user.id,
            IdentityChallenge.purpose == ACCOUNT_RECOVERY,
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    ).all():
        stale.superseded_at = _now()

    raw = _new_code()
    db.add(IdentityChallenge(
        user_id=user.id,
        purpose=ACCOUNT_RECOVERY,
        token_hash=_hash(raw),
        expires_at=_now() + timedelta(minutes=RECOVERY_TTL_MINUTES),
    ))
    recovery.status = RECOVERY_ADDITIONAL_VERIFICATION
    db.commit()
    db.refresh(recovery)
    return recovery, raw, is_new


def is_locked(recovery: AccountRecovery) -> bool:
    return bool(recovery.locked_until and recovery.locked_until > _now())


def verify_code(db: Session, user: User, code: str) -> tuple[str, AccountRecovery | None]:
    """Check a submitted recovery code.

    Every wrong answer costs one of a small, durable budget held on the recovery record.
    Exhausting it locks the recovery for a fixed window — the control the previous
    implementation lacked entirely, which is what made a 4-digit code walkable.
    """
    recovery = active_recovery(db, user)
    if recovery is None:
        return INVALID, None
    if is_locked(recovery):
        return LOCKED, recovery

    challenge = db.scalar(
        select(IdentityChallenge).where(
            IdentityChallenge.user_id == user.id,
            IdentityChallenge.purpose == ACCOUNT_RECOVERY,   # purpose-bound
            IdentityChallenge.token_hash == _hash(code),
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    )
    if challenge is None:
        recovery.failed_attempts += 1
        if recovery.failed_attempts >= RECOVERY_MAX_ATTEMPTS:
            recovery.locked_until = _now() + timedelta(minutes=RECOVERY_LOCKOUT_MINUTES)
        db.commit()
        return (LOCKED if is_locked(recovery) else INVALID), recovery

    challenge.attempts += 1
    challenge.last_attempt_at = _now()
    if challenge.expires_at <= _now():
        db.commit()
        return EXPIRED, recovery

    recovery.status = RECOVERY_VERIFIED
    recovery.verified_at = _now()
    db.commit()
    db.refresh(recovery)
    return VALID, recovery


def consume_code(db: Session, user: User, code: str) -> bool:
    """Spend the code. Single-use: the same code cannot complete a second recovery."""
    challenge = db.scalar(
        select(IdentityChallenge).where(
            IdentityChallenge.user_id == user.id,
            IdentityChallenge.purpose == ACCOUNT_RECOVERY,
            IdentityChallenge.token_hash == _hash(code),
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    )
    if challenge is None or challenge.expires_at <= _now():
        return False
    challenge.consumed_at = _now()
    db.commit()
    return True


def complete_recovery(db: Session, recovery: AccountRecovery) -> AccountRecovery:
    """Only ever called AFTER the new credential is committed."""
    recovery.status = RECOVERY_COMPLETED
    recovery.completed_at = _now()
    db.commit()
    db.refresh(recovery)
    return recovery


def cancel_recovery(db: Session, recovery: AccountRecovery, reason: str | None = None) -> AccountRecovery:
    recovery.status = RECOVERY_CANCELED
    recovery.canceled_at = _now()
    recovery.reason = reason
    db.commit()
    db.refresh(recovery)
    return recovery


def claim_notice(db: Session, recovery: AccountRecovery, column: str) -> bool:
    """Claim one transition's notification exactly once.

    Conditional UPDATE, same pattern as IDN-002/003: the database arbitrates, so a retried
    request or a restarted worker cannot emit a second message for the same transition.
    """
    col = getattr(AccountRecovery, column)
    updated = db.execute(
        update(AccountRecovery)
        .where(AccountRecovery.id == recovery.id, col.is_(None))
        .values(**{column: _now()})
    ).rowcount
    db.commit()
    if updated:
        db.refresh(recovery)
        return True
    return False


def recovery_recipients(user: User) -> list[str]:
    """Account holder, plus the approved recovery contact when one is verified.

    An unverified nomination is never included: it is an address someone merely typed, and
    copying recovery mail to it would hand an attacker the recovery flow.
    """
    recipients = [user.email]
    if user.recovery_email and user.recovery_email_verified_at:
        if user.recovery_email.lower() != user.email.lower():
            recipients.append(user.recovery_email)
    return recipients


# ── Recovery contact (IDN-006) ──────────────────────────────────────────────────────────

def start_recovery_contact_change(db: Session, user: User, new_address: str) -> str:
    """Nominate a recovery address and mint a challenge sent TO that address.

    The nomination is parked in `recovery_email_pending` and is not honoured anywhere until
    the challenge is redeemed. Proving control of the destination before trusting it is the
    whole security property here.
    """
    for stale in db.scalars(
        select(IdentityChallenge).where(
            IdentityChallenge.user_id == user.id,
            IdentityChallenge.purpose == RECOVERY_CONTACT,
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    ).all():
        stale.superseded_at = _now()

    raw = secrets.token_urlsafe(32)
    user.recovery_email_pending = new_address.lower()
    db.add(IdentityChallenge(
        user_id=user.id,
        purpose=RECOVERY_CONTACT,
        token_hash=_hash(raw),
        expires_at=_now() + timedelta(minutes=60),
    ))
    db.commit()
    return raw


def confirm_recovery_contact(db: Session, raw_token: str) -> User | None:
    """Redeem a recovery-contact challenge and promote the pending address.

    Returns the user on success. The promotion and the consumption land in one commit, so
    the address can never become active while the token stays live.
    """
    if not raw_token:
        return None
    challenge = db.scalar(
        select(IdentityChallenge).where(
            IdentityChallenge.token_hash == _hash(raw_token),
            IdentityChallenge.purpose == RECOVERY_CONTACT,
            IdentityChallenge.consumed_at.is_(None),
            IdentityChallenge.superseded_at.is_(None),
        )
    )
    if challenge is None or challenge.expires_at <= _now():
        return None
    user = db.get(User, challenge.user_id)
    if user is None or not user.recovery_email_pending:
        return None

    now = _now()
    challenge.consumed_at = now
    user.recovery_email = user.recovery_email_pending
    user.recovery_email_verified_at = now
    user.recovery_email_pending = None
    db.commit()
    db.refresh(user)
    return user
