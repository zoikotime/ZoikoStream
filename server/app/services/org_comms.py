"""Organization communications and membership lifecycle (ZST-EC-001 ORG-001 to ORG-004).

Every Organization notice funnels through here so no router decides who to tell or what to
claim. The order is fixed and is the whole point of the module:

    authoritative Organization state -> commit -> claim the notification -> render -> Resend

The claim step is a conditional UPDATE against the row that already records the transition
(the Invitation for ORG-001/002, an OrgMembershipEvent for ORG-003/004). That is what makes
these safe against a page refresh, a retried request, a Resend retry or a second worker:
the database, not the caller, decides whether this transition has already been announced.

What this module will NOT do:
  * invent workspace or mode-access scope - neither exists as a domain concept here, so the
    workspace line comes from the real implicit workspace and mode access is omitted
  * claim step-up authentication or dual approval happened - no such subsystem exists
  * conflate ORG-004 with IDN-008 - one Organization membership ending is not the identity
    being restricted, and the two are announced separately with separate wording
  * consult a notification preference for a Class A message
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    ORG_ACCESS_CHANGED,
    ORG_MEMBERSHIP_REMOVED,
    Invitation,
    Organization,
    OrgMembershipEvent,
    User,
)
from . import notifications as notif_svc
from . import org as org_svc

log = logging.getLogger(__name__)

# Roles that count as "Organization administrator" for recipient resolution. super_admin is
# deliberately excluded: it is a platform role that bypasses org isolation, so including it
# would mail platform staff about every tenant's membership churn.
ORG_ADMIN_ROLES = ("org_admin",)

# User-facing role names. The email must never expose the raw stored identifier when a
# human-readable name exists. "moderator" is retained as a DISPLAY label only: the role is
# retired and nothing issues it, but a database written before the migration can still hold
# the value (models/user.LEGACY_USER_ROLES), and emailing such a person the raw slug — or
# nothing — would be worse than naming the role they were given. Grants nothing.
ROLE_LABELS = {
    "super_admin": "Platform Administrator",
    "org_admin": "Administrator",
    "billing_admin": "Billing Administrator",
    "host": "Host",
    "moderator": "Moderator",
    "speaker": "Speaker",
    "viewer": "Viewer",
}

# How close to expiry the Reminder fires, and how often the ticker looks.
REMINDER_BEFORE_EXPIRY = timedelta(days=2)
REMINDER_INTERVAL_SECONDS = 900.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── display helpers ─────────────────────────────────────────────────────────────────────

def role_label(role: str | None) -> str:
    return ROLE_LABELS.get((role or "").lower(), (role or "Unknown").replace("_", " ").title())


def workspace_scope(org: Organization | None) -> str:
    """The invitation's workspace scope, from the one workspace that actually exists.

    services/org.py is explicit that this platform has no Workspace entity - an organization
    IS its production workspace. Rendering that real scope is honest; rendering "All
    workspaces" or a chosen subset would describe a permission model that cannot be enforced.
    """
    if org is None:
        return "the Organization workspace"
    return org_svc.workspace(org)["label"]


def org_timestamp(org: Organization | None, moment: datetime | None) -> str:
    """Exact timestamp with its timezone, in the Organization's configured zone.

    Organizations.timezone is a real stored IANA name, so "29 Aug 2026, 05:30 PM IST" is a
    fact rather than a guess. When it is unset or unparseable the value falls back to UTC and
    is labelled UTC - the canonical requirement is that the zone is stated, not that it is
    local, and mislabelling a zone would be worse than showing UTC.
    """
    if moment is None:
        return "Not set"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = timezone.utc
    name = (getattr(org, "timezone", None) or "").strip()
    if name:
        try:
            zone = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("Organization %s has unusable timezone %r; rendering UTC",
                        getattr(org, "id", "?"), name)
    local = moment.astimezone(zone)
    label = local.strftime("%Z") or "UTC"
    return f"{local.strftime('%d %b %Y, %I:%M %p')} {label}"


def describe_access(*, role: str | None, org: Organization | None, active: bool = True) -> str:
    """Normalized, user-facing access snapshot.

    Deliberately built from role + the real workspace only. There is no mode-access concept
    to report, so the string does not carry a Mode segment rather than printing a made-up one.
    """
    if not active:
        return "No access"
    return f"Role: {role_label(role)} - Workspaces: {workspace_scope(org)}"


# ── high-risk access transitions (ZST-EC-001 ORG-003) ───────────────────────────────────
#
# "High risk" is defined against the REAL role model (models/user.ROLES + the _ROLE_RANK
# ladder in security.py), not invented names. A grant is high-risk when it moves someone
# into a role that can administer the Organization — that is the transition that lets the
# grantee change everyone else's access, so it is the one that needs a fresh credential
# check rather than a session that authenticated hours ago.

ADMINISTRATIVE_ROLES = frozenset({"org_admin", "super_admin"})


def is_high_risk_grant(previous_role: str | None, new_role: str | None) -> bool:
    """True when a change hands over administrative control of the Organization."""
    prev = (previous_role or "").lower()
    new = (new_role or "").lower()
    if prev == new:
        return False
    return new in ADMINISTRATIVE_ROLES


# ── recipient resolution ────────────────────────────────────────────────────────────────

def _eligible(user: User | None) -> bool:
    return bool(user and user.is_active and user.deleted_at is None and user.email)


def org_admins(db: Session, org_id) -> list[User]:
    """Active administrators of one organization."""
    if org_id is None:
        return []
    return list(db.scalars(
        select(User).where(
            User.org_id == org_id,
            User.role.in_(ORG_ADMIN_ROLES),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    ).all())


def recipients(*users: User | None, extra: list[str] | None = None,
               exclude: set[str] | None = None) -> list[str]:
    """Deduplicated, lower-cased address list in a stable order.

    Dedup is by address, not by user id, because the inviter is very often also an
    administrator and would otherwise be mailed twice for one event.
    """
    seen: set[str] = set(a.lower() for a in (exclude or set()))
    out: list[str] = []
    for candidate in list(users) + [None] * 0:
        if _eligible(candidate):
            addr = candidate.email.lower()
            if addr not in seen:
                seen.add(addr)
                out.append(addr)
    for addr in extra or []:
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr.lower())
    return out


# ── notification claims ─────────────────────────────────────────────────────────────────

def _claim(db: Session, model, row_id, column: str) -> bool:
    """Claim one transition's notification. Returns True exactly once per (row, column)."""
    col = getattr(model, column)
    updated = db.execute(
        update(model).where(model.id == row_id, col.is_(None)).values(**{column: _now()})
    ).rowcount
    db.commit()
    return bool(updated)


def _queue(background, send, addresses: list[str], **kwargs) -> None:
    """Queue one send per recipient. Never raises: the state is already committed, and no
    delivery problem may undo it."""
    try:
        for address in addresses:
            background.add_task(send, address, **kwargs)
    except UnsafeLinkError:
        log.exception("Organization notice not queued: APP_URL unsafe for this environment")


# ── ORG-001 invitation lifecycle ────────────────────────────────────────────────────────

def _invitation_facts(db: Session, inv: Invitation) -> dict:
    org = inv.organization or db.get(Organization, inv.org_id)
    inviter = inv.inviter
    return {
        "org": org,
        "org_name": org.name if org else "your Organization",
        "inviter_name": (inviter.full_name or inviter.email) if inviter else "An administrator",
        "role_name": role_label(inv.role),
        "workspace_scope": workspace_scope(org),
        "expires_display": org_timestamp(org, inv.expires_at),
    }


def announce_invited(db: Session, background, inv: Invitation, raw_token: str) -> None:
    """ORG-001 base. Called after the invitation row is committed.

    The raw token is passed in rather than read back because only the hash is stored - the
    caller holds the single copy that will ever exist.
    """
    if not _claim(db, Invitation, inv.id, "invited_notified_at"):
        return
    facts = _invitation_facts(db, inv)
    try:
        url = email_mod.invitation_url(raw_token)
    except UnsafeLinkError:
        log.exception("ORG-001 not sent for invitation %s: APP_URL unsafe", inv.id)
        return
    background.add_task(
        email_mod.send_invitation_email, inv.email,
        org_name=facts["org_name"], inviter_name=facts["inviter_name"],
        role_name=facts["role_name"], workspace_scope=facts["workspace_scope"],
        expires_display=facts["expires_display"], invite_url=url,
    )


def announce_reminder(db: Session, background, inv: Invitation, raw_token: str) -> None:
    """ORG-001 Reminder. Only ever sent for an invitation that is still pending."""
    if inv.status != "pending":
        return
    if not _claim(db, Invitation, inv.id, "reminder_sent_at"):
        return
    facts = _invitation_facts(db, inv)
    try:
        url = email_mod.invitation_url(raw_token)
    except UnsafeLinkError:
        log.exception("ORG-001 reminder not sent for %s: APP_URL unsafe", inv.id)
        return
    background.add_task(
        email_mod.send_invitation_reminder_email, inv.email,
        org_name=facts["org_name"], inviter_name=facts["inviter_name"],
        role_name=facts["role_name"], workspace_scope=facts["workspace_scope"],
        expires_display=facts["expires_display"], invite_url=url,
    )


def announce_expired(db: Session, background, inv: Invitation) -> None:
    """ORG-001 Expired. Fires where expiry is actually observed and recorded."""
    if not _claim(db, Invitation, inv.id, "expired_notified_at"):
        return
    facts = _invitation_facts(db, inv)
    background.add_task(
        email_mod.send_invitation_expired_email, inv.email,
        org_name=facts["org_name"], role_name=facts["role_name"],
        expires_display=facts["expires_display"],
    )


def announce_revoked(db: Session, background, inv: Invitation) -> None:
    """ORG-001 Revoked. Effective time only; the reason is never disclosed."""
    if not _claim(db, Invitation, inv.id, "revoked_notified_at"):
        return
    facts = _invitation_facts(db, inv)
    background.add_task(
        email_mod.send_invitation_revoked_email, inv.email,
        org_name=facts["org_name"], role_name=facts["role_name"],
        effective_at=org_timestamp(facts["org"], _now()),
    )


# ── ORG-002 member joined ───────────────────────────────────────────────────────────────

def announce_member_joined(db: Session, background, inv: Invitation, member: User) -> None:
    """ORG-002. Called only after accept_invitation has committed the membership.

    Recipients are the original inviter plus the organization's administrators, deduplicated
    by address. The new member is NOT a recipient: they performed the action and already know.
    """
    if not _claim(db, Invitation, inv.id, "joined_notified_at"):
        return
    org = inv.organization or db.get(Organization, inv.org_id)
    admins = org_admins(db, inv.org_id)
    # The new member is frequently an administrator themselves (an invited org_admin), so
    # they are excluded by address rather than assumed absent.
    addresses = recipients(inv.inviter, *admins, exclude={member.email.lower()})
    if not addresses:
        return
    # ZST-EC-001 ORG-012. ORG-002 is Class C but policy permits preference control, so it
    # is one of the two families a stored preference genuinely governs. The claim above has
    # already been taken, which is deliberate: a suppressed notification is still a
    # notification that happened, and re-enabling the preference later must not retro-send
    # an announcement about a member who joined weeks ago.
    if not notif_svc.should_send_operational_notification(family="ORG-002", org=org):
        return
    _queue(
        background, email_mod.send_member_joined_email, addresses,
        member_name=member.full_name or member.email,
        org_name=org.name if org else "your Organization",
        inviter_name=(inv.inviter.full_name or inv.inviter.email) if inv.inviter
        else "An administrator",
        accepted_at=org_timestamp(org, inv.accepted_at or _now()),
        role_summary=role_label(member.role),
        workspace_scope=workspace_scope(org),
    )


# ── ORG-003 / ORG-004 membership policy transitions ─────────────────────────────────────

def record_membership_event(db: Session, *, user: User | None, email: str, org_id, kind: str,
                            previous_access: str | None = None,
                            current_access: str | None = None,
                            identity_also_restricted: bool = False) -> OrgMembershipEvent:
    """Persist one committed transition. Called AFTER the change is durable."""
    event = OrgMembershipEvent(
        user_id=user.id if user is not None else None,
        email=email.lower(), org_id=org_id, kind=kind, effective_at=_now(),
        previous_access=previous_access, current_access=current_access,
        identity_also_restricted=identity_also_restricted,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def notify_membership_event(db: Session, background, event: OrgMembershipEvent) -> None:
    """Queue the ORG-003 / ORG-004 set for a recorded transition.

    Class A: mandatory, and nothing here reads a notification preference.
    """
    if not _claim(db, OrgMembershipEvent, event.id, "notified_at"):
        return
    org = db.get(Organization, event.org_id) if event.org_id else None
    org_name = org.name if org else "your Organization"
    effective = org_timestamp(org, event.effective_at)

    member = db.get(User, event.user_id) if event.user_id else None
    admins = org_admins(db, event.org_id)
    member_address = event.email.lower()
    # The affected member gets the second-person copy; administrators get the third-person
    # one. Splitting the lists is what keeps an admin from being told "your access changed"
    # about somebody else's membership.
    admin_addresses = recipients(*admins, exclude={member_address})

    if event.kind == ORG_ACCESS_CHANGED:
        send = email_mod.send_access_changed_email
        shared = {"org_name": org_name, "effective_at": effective,
                  "previous_access": event.previous_access or "Not recorded",
                  "current_access": event.current_access or "Not recorded"}
        # A member whose account was deactivated in the same breath still gets told: the
        # access change is real and they may be able to read it elsewhere.
        _queue(background, send, [member_address], is_affected_member=True, **shared)
        _queue(background, send, admin_addresses, is_affected_member=False, **shared)
    elif event.kind == ORG_MEMBERSHIP_REMOVED:
        send = email_mod.send_membership_removed_email
        shared = {"org_name": org_name, "effective_at": effective}
        _queue(background, send, [member_address], is_affected_member=True, **shared)
        _queue(background, send, admin_addresses, is_affected_member=False, **shared)
    del member  # resolved only to prove the row still exists; copy is address-driven


def announce_access_changed(db: Session, background, *, user: User, org_id,
                            previous_access: str, current_access: str) -> OrgMembershipEvent | None:
    """ORG-003. No-ops when the normalized snapshots are identical, so an update that
    touched only a name or a flag cannot produce an access notice."""
    if previous_access == current_access:
        return None
    event = record_membership_event(
        db, user=user, email=user.email, org_id=org_id, kind=ORG_ACCESS_CHANGED,
        previous_access=previous_access, current_access=current_access,
    )
    notify_membership_event(db, background, event)
    return event


def announce_membership_removed(db: Session, background, *, user: User | None, email: str,
                                org_id, previous_access: str | None = None,
                                identity_also_restricted: bool = False) -> OrgMembershipEvent:
    """ORG-004. `identity_also_restricted` is recorded for the audit trail only - it never
    changes the copy, because IDN-008 owns the identity claim and says it in its own words."""
    event = record_membership_event(
        db, user=user, email=email, org_id=org_id, kind=ORG_MEMBERSHIP_REMOVED,
        previous_access=previous_access, current_access="No access",
        identity_also_restricted=identity_also_restricted,
    )
    notify_membership_event(db, background, event)
    return event


# ── ORG-001 Reminder ticker ─────────────────────────────────────────────────────────────
# Uses the same lifespan-ticker pattern as the five schedulers already in app/main.py.
#
# One genuine limitation, stated rather than hidden: create_invitation stores only the
# token HASH, so a reminder cannot re-send the original link. The ticker therefore rotates
# the invitation token before reminding, which is the behaviour resend_invitation already
# has. It deliberately does NOT extend expires_at - the deadline the reminder announces is
# the same deadline the invitation already had, so the reminder cannot silently widen the
# authorization window.

class _Bg:
    """Minimal BackgroundTasks stand-in so the ticker can reuse the announce_* functions
    unchanged. Sends inline; the ticker is already off the request path."""

    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a ticker must not die on one bad send
            log.exception("Invitation reminder send failed")


def due_reminders(db: Session, *, now: datetime | None = None) -> list[Invitation]:
    now = now or _now()
    return list(db.scalars(
        select(Invitation).where(
            Invitation.status == "pending",
            Invitation.reminder_sent_at.is_(None),
            Invitation.expires_at > now,
            Invitation.expires_at <= now + REMINDER_BEFORE_EXPIRY,
        )
    ).all())


def send_due_reminders(db: Session, background=None) -> int:
    """One pass. Returns how many reminders were claimed and queued."""
    from ..crud import organization as org_crud

    background = background or _Bg()
    sent = 0
    for inv in due_reminders(db):
        raw = org_crud.rotate_invitation_token(db, inv)
        before = inv.reminder_sent_at
        announce_reminder(db, background, inv, raw)
        db.refresh(inv)
        if inv.reminder_sent_at != before:
            sent += 1
    return sent


async def run_invitation_reminders(interval: float = REMINDER_INTERVAL_SECONDS) -> None:
    """Background ticker started from the app lifespan.

    ponytail: single-ticker assumption, same as the five tickers already in main.py. The
    reminder_sent_at claim means a second worker cannot duplicate the mail even if it also
    runs this loop - it would lose the conditional UPDATE race and skip.
    """
    from ..db import SessionLocal

    while True:
        try:
            await asyncio.sleep(interval)
            db = SessionLocal()
            try:
                await asyncio.to_thread(send_due_reminders, db)
            finally:
                db.close()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Invitation reminder pass failed")
