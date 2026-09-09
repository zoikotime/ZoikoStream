"""Contributor authorization and communications (ZST-EC-001 CON-001 -> CON-005).

**Membership separation.** `invite()` takes an EMAIL, not a `User`. Nothing in this module
writes `User.org_id`, `User.role`, or creates a user of any kind - a test asserts the user
count is unchanged across an external invitation. An org member and an external guest reach
the same grant by the same path; the only difference is whether `user_id` happens to resolve.

**Backstage boundary.** Every contributor CTA points at `/contributor/events/{event_id}` -
a contributor-scoped surface. The pre-existing `_console_url` in `routers/events.py` sent
speakers to `/speaker/backstage` (acceptable) but hosts and moderators to
`/host/dashboard` / `/moderator/dashboard`, which are organizer consoles. Contributor mail
from this module never links there.

**Forwardability, stated honestly.** `exchange()` binds to the signed-in user when the grant
has a `user_id` - a different account is refused. An EXTERNAL contributor has no account to
bind to, so their link is a bearer credential: short-lived, single-purpose, revocable, but
forwardable. `link_is_identity_bound()` reports which case applies and the email copy is
generated from it, so the message never claims a protection the backend is not applying.
CON-004 is reported PARTIAL for exactly this reason.

**No LiveKit credential ever leaves in an email.** The emailed token is exchanged
server-side; `issue_media_credential()` is the only thing that mints a LiveKit token, and it
runs after authorization.

**CON-005 is not a disconnect.** `ContributorSession.last_disconnected_at` is written on
every websocket drop and is deliberately NOT what this module reads. `end_session()` sets
`ended_at`/`end_reason` for one of four authoritative reasons only.

**Marketing.** Nothing here touches a marketing surface, because none exists
(`notifications.MARKETING_PREFERENCE_KEYS` is empty). Contribution is operational processing;
tests assert no subscription of any kind appears at any point in the lifecycle.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..models import (
    CONTRIBUTOR_ROLES,
    NETWORK_TEST_SUPPORTED,
    SESSION_END_LABELS,
    SESSION_END_REASONS,
    TECH_CHECK_CAPABILITIES,
    TECH_CHECK_REQUIRED,
    TECH_CHECK_VALIDITY_POLICY,
    TECH_FAILURE_LABELS,
    TOKEN_PURPOSE_EVENT_ACCESS,
    ContributorAccessToken,
    ContributorSession,
    ContributorTechnicalCheck,
    Event,
    EventAssignment,
    EventContributorGrant,
    EventRehearsal,
    Organization,
    User,
)
from . import event_comms
from .event_comms import _Bg, _claim, _ledger, event_timestamp, is_test_org

log = logging.getLogger(__name__)

# How wide the backstage window opens around the event. Derived from the schedule rather than
# stored per grant, so LVE-008 moving the event moves the window with it.
ACCESS_OPENS_BEFORE = timedelta(minutes=90)
ACCESS_CLOSES_AFTER = timedelta(hours=4)
# A backstage token lives only as long as the window it belongs to, capped so a link mailed
# well in advance cannot be a long-lived credential.
TOKEN_MAX_LIFETIME = timedelta(hours=8)
# One governed reminder threshold, shared with LVE-005's own rehearsal reminder.
REHEARSAL_REMINDER_HOURS = 24
# Invitation validity when the caller names none.
DEFAULT_INVITE_DAYS = 14

# No technical-check validity policy exists in this product, so none is invented. When a
# policy is configured this becomes a timedelta and the EXPIRED transition starts firing.
TECH_CHECK_VALIDITY = TECH_CHECK_VALIDITY_POLICY


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    """sha256, matching crud.event._hash_link_token and the DEV-012 export tokens."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _may_send(family: str, org) -> bool:
    from . import notifications

    return notifications.should_send_operational_notification(family=family, org=org)


def _org(db: Session, event: Event) -> Organization | None:
    return db.get(Organization, event.org_id) if event else None


def backstage_url(event_id) -> str:
    """The contributor-scoped surface.

    Never an organizer console, never Super Admin, never organization administration. A
    contributor landing here sees one event.
    """
    return f"{email_mod.public_base_url()}/contributor/events/{event_id}"


def accept_url(event_id, token: str) -> str:
    return f"{backstage_url(event_id)}/accept?t={token}"


def join_url(event_id, token: str) -> str:
    return f"{backstage_url(event_id)}/join?t={token}"


# ══ recipients ══════════════════════════════════════════════════════════════════════════

def contributor_recipient(grant: EventContributorGrant) -> event_comms.Recipients:
    """The contributor, and nobody else.

    A contributor message must not reach unrelated organization users, so this builds a
    single-address bundle from the grant's own email rather than resolving org roles.
    """
    holder = type("_C", (), {"email": grant.email,
                             "full_name": grant.display_name or "there",
                             "id": grant.id})()
    return event_comms.Recipients(event_owner=holder)


def event_technician(db: Session, event: Event) -> User | None:
    """The operator a technical-check failure should reach.

    There is no `technician` role in ASSIGNMENT_ROLES, so this resolves the assigned HOST -
    the closest authoritative operational owner - and returns None rather than fanning the
    failure out to every event team member, which CON-002 explicitly forbids. The absence of
    a real technician role is reported as a gap.
    """
    assignment = db.scalar(
        select(EventAssignment).where(EventAssignment.event_id == event.id,
                                      EventAssignment.role == "host"))
    return db.get(User, assignment.user_id) if assignment else None


TECHNICIAN_ROLE_SUPPORTED = False


# ══ CON-001 — invitation lifecycle ══════════════════════════════════════════════════════

def _resolve_user(db: Session, email: str) -> User | None:
    """Whether this address already belongs to a platform account.

    Purely informational: a match enables identity-bound access later, and a miss is a
    perfectly valid external contributor. Nothing is created either way.
    """
    return db.scalar(select(User).where(User.email == (email or "").strip().lower(),
                                        User.deleted_at.is_(None)))


def invite(db: Session, event: Event, *, email: str, role: str, invited_by=None,
           display_name: str | None = None, expires_at: datetime | None = None,
           technical_check_required: bool = True,
           rehearsal_required: bool = False) -> tuple[EventContributorGrant | None, str | None]:
    """Create or refresh a contributor grant. Returns (grant, raw_invitation_token).

    NO organization membership is created, checked or implied. The address does not need to
    belong to a user at all - which is the entire point of the grant existing.
    """
    address = (email or "").strip().lower()
    if not address or role not in CONTRIBUTOR_ROLES:
        return None, None

    grant = db.scalar(
        select(EventContributorGrant).where(EventContributorGrant.event_id == event.id,
                                            EventContributorGrant.email == address))
    if grant is None:
        grant = EventContributorGrant(event_id=event.id, org_id=event.org_id, email=address)
        db.add(grant)

    existing = _resolve_user(db, address)
    grant.user_id = existing.id if existing else None
    grant.display_name = display_name or (existing.full_name if existing else None)
    grant.role = role
    grant.status = "invited"
    grant.invited_by = invited_by
    grant.invited_at = _now()
    grant.accepted_at = None
    grant.revoked_at = grant.revoked_by = grant.revoke_reason = None
    grant.expires_at = expires_at or (_now() + timedelta(days=DEFAULT_INVITE_DAYS))
    grant.technical_check_required = bool(technical_check_required)
    grant.rehearsal_required = bool(rehearsal_required)
    # A fresh invitation is a fresh lifecycle: re-arm every marker so the new cycle can be
    # communicated without a stale one suppressing it.
    grant.invited_notified_at = None
    grant.accepted_notified_at = None
    grant.reminder_notified_at = None
    grant.revoked_notified_at = None
    grant.expired_notified_at = None
    grant.access_notified_at = None
    refresh_access_window(db, event, grant)
    db.commit()
    db.refresh(grant)

    raw = issue_token(db, grant, purpose="contributor_invitation",
                      expires_at=grant.expires_at)
    return grant, raw


def is_expired(grant: EventContributorGrant, now: datetime | None = None) -> bool:
    return bool(grant.expires_at and grant.expires_at < (now or _now()))


def accept(db: Session, grant: EventContributorGrant, *, user_id=None) -> tuple[bool, str | None]:
    """Accept an invitation. Refuses an expired or revoked one."""
    if grant.status == "revoked":
        return False, "This contributor invitation was revoked."
    if grant.status == "expired" or is_expired(grant):
        # Committed on the way past, so an expired invitation stops being usable at the
        # moment somebody tries rather than lingering as `invited`.
        grant.status = "expired"
        db.commit()
        return False, "This contributor invitation has expired."
    if grant.status == "accepted":
        return True, None
    grant.status = "accepted"
    grant.accepted_at = _now()
    if user_id is not None:
        grant.user_id = user_id
    db.commit()
    if grant.technical_check_required:
        require_technical_check(db, grant)
    return True, None


def revoke(db: Session, grant: EventContributorGrant, *, actor_id=None,
           reason: str | None = None) -> bool:
    """Withdraw contributor authorization and invalidate everything it carried.

    Revocation is not a label: every unconsumed token is revoked here, `authorize()` refuses
    a non-accepted grant, and any open backstage session is closed. A revoked contributor
    cannot exchange an old link.
    """
    if grant.status == "revoked":
        return False
    grant.status = "revoked"
    grant.revoked_at = _now()
    grant.revoked_by = actor_id
    grant.revoke_reason = (reason or "")[:200] or None
    for token in db.scalars(
        select(ContributorAccessToken).where(
            ContributorAccessToken.grant_id == grant.id,
            ContributorAccessToken.revoked_at.is_(None))).all():
        token.revoked_at = _now()
    db.commit()
    session = _session_for(db, grant)
    if session is not None and session.ended_at is None:
        end_session(db, session, reason="access_revoked")
    return True


def expire_due(db: Session, background=None) -> int:
    """Deadline-driven: move invitations past `expires_at` to EXPIRED and announce once."""
    background = background or _Bg()
    sent = 0
    for grant in db.scalars(
        select(EventContributorGrant).where(
            EventContributorGrant.status == "invited",
            EventContributorGrant.expires_at.isnot(None),
            EventContributorGrant.expires_at < _now())).all():
        grant.status = "expired"
        db.commit()
        if notify_expired(db, background, grant):
            sent += 1
    return sent


# -- invitation messages ------------------------------------------------------------------

CONSENT_NOTICE = (
    "Taking part means your camera, microphone and name are visible to the event audience, "
    "and the session may be recorded and made available as a replay. Zoiko Steam processes "
    "your details only to run this event - contributing does not subscribe you to marketing "
    "or to any mailing list."
)


def _grant_context(db: Session, grant: EventContributorGrant):
    event = db.get(Event, grant.event_id)
    org = _org(db, event) if event else None
    return event, org


def _shared(db: Session, grant: EventContributorGrant, event: Event, org) -> dict:
    check = technical_check(db, grant)
    return {
        "event_title": event.title or "your event",
        "role": grant.role.title(),
        "starts": event_timestamp(event, event.start_time, org),
        "zone": event_comms.event_zone(event, org)[1],
        "local_note": event_comms.LOCAL_TIME_NOTE,
        "technical_check": ("Required" if grant.technical_check_required else "Not required"),
        "technical_status": (check.status.replace("_", " ").title() if check
                             else "Not started"),
        "rehearsal": "Required" if grant.rehearsal_required else "Not required",
        "org_name": org.name if org else "the organizer",
        "test_mode": is_test_org(org),
    }


def _queue_one(background, send, grant: EventContributorGrant, **kwargs) -> None:
    """Queue to the contributor's own address. Wrapped so a provider failure cannot
    propagate into the caller's transaction and undo committed authorization state."""
    try:
        background.add_task(send, grant.email,
                            name=grant.display_name or "there", **kwargs)
    except email_mod.UnsafeLinkError:
        log.exception("CON notice not queued: APP_URL unsafe for this environment")


def notify_invited(db: Session, background, grant: EventContributorGrant,
                   raw_token: str | None) -> bool:
    if grant.status != "invited":
        return False
    if not _claim(db, grant, "invited_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-001", org):
        return False
    inviter = db.get(User, grant.invited_by) if grant.invited_by else None
    _ledger(db, org_id=grant.org_id, family="CON-001", transition="invited",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_invite_email, grant,
               inviter=(inviter.full_name or inviter.email) if inviter else "The event team",
               grants=ROLE_ACCESS[grant.role],
               consent=CONSENT_NOTICE,
               expires_at=event_timestamp(event, grant.expires_at, org),
               accept_url=(accept_url(event.id, raw_token) if raw_token
                           else backstage_url(event.id)),
               **_shared(db, grant, event, org))
    return True


# What each contributor role actually grants, so the invitation states the access truthfully.
ROLE_ACCESS = {
    "host": "Run the event: start and end the broadcast, manage contributors on air",
    "moderator": "Moderate chat, Q&A and polls during the event",
    "speaker": "Join backstage and appear on air with camera and microphone",
    "panelist": "Join backstage and appear on air as part of a panel",
    "presenter": "Join backstage, appear on air and share your screen",
}


def notify_accepted(db: Session, background, grant: EventContributorGrant) -> bool:
    if grant.status != "accepted":
        return False
    if not _claim(db, grant, "accepted_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-001", org):
        return False
    _ledger(db, org_id=grant.org_id, family="CON-001", transition="accepted",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_accepted_email, grant,
               next_action=("Complete your technical check"
                            if grant.technical_check_required else
                            "Watch for your rehearsal details"
                            if grant.rehearsal_required else
                            "Watch for your event-day access link"),
               # Access is NOT granted at acceptance time. The link arrives when the window
               # opens, because a credential issued now would have to be long-lived.
               access_note=("Your personal backstage link is sent when the join window "
                            "opens, shortly before the event."),
               backstage=backstage_url(event.id),
               **_shared(db, grant, event, org))
    return True


def outstanding_actions(db: Session, grant: EventContributorGrant) -> list[str]:
    """What this contributor still owes, from committed state."""
    out = []
    if grant.technical_check_required:
        check = technical_check(db, grant)
        if check is None or check.status in ("required", "in_progress", "needs_attention",
                                             "expired"):
            out.append("Complete your technical check")
    if grant.rehearsal_required:
        session = _session_for(db, grant)
        if session is None or not session.rehearsal_complete:
            out.append("Attend the rehearsal")
    return out


def notify_reminder(db: Session, background, grant: EventContributorGrant) -> bool:
    """One reminder, only while the grant is active and something is genuinely outstanding."""
    if grant.status not in ("invited", "accepted") or is_expired(grant):
        return False
    outstanding = outstanding_actions(db, grant)
    if not outstanding:
        return False
    if not _claim(db, grant, "reminder_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-001", org):
        return False
    _ledger(db, org_id=grant.org_id, family="CON-001", transition="reminder",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_reminder_email, grant,
               outstanding=outstanding,
               backstage=backstage_url(event.id),
               **_shared(db, grant, event, org))
    return True


def notify_revoked(db: Session, background, grant: EventContributorGrant) -> bool:
    if grant.status != "revoked":
        return False
    if not _claim(db, grant, "revoked_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-001", org):
        return False
    _ledger(db, org_id=grant.org_id, family="CON-001", transition="revoked",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_revoked_email, grant,
               revoked_at=event_timestamp(event, grant.revoked_at, org),
               # Only a reason an operator explicitly recorded as customer-safe. Absent means
               # the message says nothing about why rather than guessing.
               reason=grant.revoke_reason,
               **_shared(db, grant, event, org))
    return True


def notify_expired(db: Session, background, grant: EventContributorGrant) -> bool:
    if grant.status != "expired":
        return False
    if not _claim(db, grant, "expired_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-001", org):
        return False
    _ledger(db, org_id=grant.org_id, family="CON-001", transition="expired",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_expired_email, grant,
               expired_at=event_timestamp(event, grant.expires_at, org),
               **_shared(db, grant, event, org))
    return True


# ══ CON-002 — technical check ═══════════════════════════════════════════════════════════

def technical_check(db: Session, grant: EventContributorGrant) -> ContributorTechnicalCheck | None:
    return db.scalar(
        select(ContributorTechnicalCheck).where(
            ContributorTechnicalCheck.grant_id == grant.id))


def require_technical_check(db: Session, grant: EventContributorGrant,
                            deadline_at: datetime | None = None) -> ContributorTechnicalCheck:
    check = technical_check(db, grant)
    if check is None:
        check = ContributorTechnicalCheck(grant_id=grant.id, event_id=grant.event_id,
                                          status="required")
        db.add(check)
    check.deadline_at = deadline_at or grant.access_window_start
    db.commit()
    db.refresh(check)
    return check


def apply_preflight(db: Session, grant: EventContributorGrant,
                    preflight: dict) -> ContributorTechnicalCheck | None:
    """Fold a REAL browser preflight result into the governed lifecycle.

    `preflight` is `ContributorSession.preflight_result`, produced by the contributor's own
    browser via `services/contributor._preflight_result`. Nothing here decides a capability -
    it copies what was actually reported and applies the same PASSED rule the admit gate uses.
    """
    if not preflight:
        return None
    check = technical_check(db, grant) or require_technical_check(db, grant)
    check.browser_result = bool(preflight.get("browser_supported"))
    check.camera_result = bool(preflight.get("camera_ok"))
    check.microphone_result = bool(preflight.get("mic_ok"))
    check.speaker_result = bool(preflight.get("speaker_ok"))
    check.framing_result = bool(preflight.get("framing_ok"))
    # Stored as an unverified self-report. There is no measured network test in this product,
    # so it is never allowed to influence the verdict.
    quality = preflight.get("network_quality")
    check.network_self_report = str(quality)[:40] if quality else None

    failures = [TECH_FAILURE_LABELS[cap] for cap in TECH_CHECK_CAPABILITIES
                if not bool(preflight.get(_PREFLIGHT_KEY[cap]))]
    required_ok = all(bool(preflight.get(_PREFLIGHT_KEY[cap])) for cap in TECH_CHECK_REQUIRED)
    check.failure_categories = failures
    check.completed_at = _now()
    if required_ok:
        check.status = "passed"
        check.attention_notified_at = None
        if TECH_CHECK_VALIDITY is not None:
            check.expires_at = _now() + TECH_CHECK_VALIDITY
    else:
        check.status = "needs_attention"
        check.passed_notified_at = None
    db.commit()
    db.refresh(check)
    return check


# Capability name -> the key the existing preflight payload actually uses.
_PREFLIGHT_KEY = {
    "browser_supported": "browser_supported",
    "camera_ok": "camera_ok",
    "mic_ok": "mic_ok",
    "speaker_ok": "speaker_ok",
    "framing_ok": "framing_ok",
}

REQUIRED_CHECK_LABELS = [
    "A supported browser",
    "Camera permission and a working camera",
    "Microphone permission and a working microphone",
]
NETWORK_NOTE = ("Zoiko Steam does not run a network speed test. Use a wired connection or "
                "strong Wi-Fi where you can - we cannot verify it for you.")


def notify_technical_check(db: Session, background, grant: EventContributorGrant,
                           check: ContributorTechnicalCheck) -> str | None:
    """Announce one technical-check transition."""
    marker = {"required": "required_notified_at", "passed": "passed_notified_at",
              "needs_attention": "attention_notified_at",
              "expired": "expired_notified_at"}.get(check.status)
    if marker is None:
        return None
    if not _claim(db, check, marker):
        return None
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-002", org):
        return None

    _ledger(db, org_id=grant.org_id, family="CON-002",
            transition=f"tech_{check.status}", event_id=event.id)
    _queue_one(background, email_mod.send_contributor_tech_check_email, grant,
               variant=check.status,
               deadline=event_timestamp(event, check.deadline_at, org),
               completed_at=event_timestamp(event, check.completed_at, org),
               required_checks=list(REQUIRED_CHECK_LABELS),
               tested=[TECH_FAILURE_LABELS[c].split(" unavailable")[0]
                       for c in TECH_CHECK_CAPABILITIES
                       if getattr(check, _CHECK_FIELD[c]) is True],
               failures=list(check.failure_categories or []),
               network_note=NETWORK_NOTE,
               validity=("No expiry - this check stays valid for the event"
                         if TECH_CHECK_VALIDITY is None else
                         event_timestamp(event, check.expires_at, org)),
               check_url=backstage_url(event.id),
               **_shared(db, grant, event, org))

    # A failure also reaches the assigned operational owner - and ONLY them.
    if check.status == "needs_attention":
        technician = event_technician(db, event)
        if technician is not None and technician.email:
            background.add_task(
                email_mod.send_contributor_tech_alert_email, technician.email,
                name=technician.full_name or "there",
                contributor=grant.email, role=grant.role.title(),
                event_title=event.title or "your event",
                failures=list(check.failure_categories or []),
                event_id=str(event.id),
                org_name=org.name if org else "your Organization",
                test_mode=is_test_org(org))
    return check.status


_CHECK_FIELD = {"browser_supported": "browser_result", "camera_ok": "camera_result",
                "mic_ok": "microphone_result", "speaker_ok": "speaker_result",
                "framing_ok": "framing_result"}


# ══ CON-003 — rehearsal reminder ════════════════════════════════════════════════════════

def rehearsal_for(db: Session, event: Event) -> EventRehearsal | None:
    """The scheduled LVE-005 rehearsal. There is no second rehearsal model."""
    return db.scalar(
        select(EventRehearsal).where(EventRehearsal.event_id == event.id,
                                     EventRehearsal.status == "scheduled")
        .order_by(EventRehearsal.scheduled_at))


def invalidate_rehearsal_reminders(db: Session, event: Event) -> int:
    """Called when LVE-008 moves the schedule.

    Bumps the reminder VERSION on every active grant instead of clearing a marker, so the
    reminder for the new time can be sent exactly once while the record still shows that an
    earlier one went out for the old time.
    """
    grants = db.scalars(
        select(EventContributorGrant).where(
            EventContributorGrant.event_id == event.id,
            EventContributorGrant.status.in_(("invited", "accepted")))).all()
    for grant in grants:
        grant.rehearsal_reminder_version += 1
    if grants:
        db.commit()
    return len(grants)


def reminder_eligible(db: Session, grant: EventContributorGrant, event: Event,
                      rehearsal: EventRehearsal | None) -> tuple[bool, str]:
    """Every precondition CON-003 requires, each read from committed state."""
    if rehearsal is None:
        return False, "no rehearsal is scheduled"
    if rehearsal.status != "scheduled":
        return False, f"the rehearsal is {rehearsal.status}"
    if event.status == "cancelled":
        return False, "the event is cancelled"
    if grant.status != "accepted":
        return False, f"the contributor grant is {grant.status}"
    if is_expired(grant):
        return False, "the contributor grant has expired"
    if grant.rehearsal_reminded_version == grant.rehearsal_reminder_version:
        return False, "a reminder for this schedule was already sent"
    if rehearsal.scheduled_at is None:
        return False, "the rehearsal has no time"
    now = _now()
    if rehearsal.scheduled_at < now:
        return False, "the rehearsal has passed"
    if rehearsal.scheduled_at - timedelta(hours=REHEARSAL_REMINDER_HOURS) > now:
        return False, "outside the reminder threshold"
    return True, "eligible"


def notify_rehearsal_reminder(db: Session, background, grant: EventContributorGrant) -> bool:
    event, org = _grant_context(db, grant)
    if event is None:
        return False
    rehearsal = rehearsal_for(db, event)
    ok, _why = reminder_eligible(db, grant, event, rehearsal)
    if not ok:
        return False
    # Claim by VERSION, so a rescheduled rehearsal can legitimately remind again.
    grant.rehearsal_reminded_version = grant.rehearsal_reminder_version
    db.commit()
    if not _may_send("CON-003", org):
        return False

    _ledger(db, org_id=grant.org_id, family="CON-003", transition="rehearsal_reminder",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_rehearsal_email, grant,
               # Read fresh from the rehearsal row, so an old time can never be mailed.
               rehearsal_at=event_timestamp(event, rehearsal.scheduled_at, org),
               purpose=rehearsal.purpose or "Confirm your camera, microphone and cues",
               preparation=("Join from a quiet room on the device you will use on the day, "
                            "and complete your technical check first if you have not."),
               backstage=backstage_url(event.id),
               **_shared(db, grant, event, org))
    return True


# ══ CON-004 — event-day access ══════════════════════════════════════════════════════════

def refresh_access_window(db: Session, event: Event,
                          grant: EventContributorGrant) -> None:
    """Derive the access window from the event schedule. Re-derived on every reschedule."""
    if event.start_time is None:
        grant.access_window_start = grant.access_window_end = None
        return
    grant.access_window_start = event.start_time - ACCESS_OPENS_BEFORE
    grant.access_window_end = (event.end_time or event.start_time) + ACCESS_CLOSES_AFTER


def reissue_for_schedule_change(db: Session, event: Event) -> int:
    """LVE-008 hook: move every window and kill every token minted for the old one.

    An access token outliving the schedule it was issued against is exactly the stale
    credential CON-004 warns about, so they are revoked rather than re-dated.
    """
    grants = db.scalars(
        select(EventContributorGrant).where(
            EventContributorGrant.event_id == event.id,
            EventContributorGrant.status.in_(("invited", "accepted")))).all()
    for grant in grants:
        refresh_access_window(db, event, grant)
        grant.access_notified_at = None
        for token in db.scalars(
            select(ContributorAccessToken).where(
                ContributorAccessToken.grant_id == grant.id,
                ContributorAccessToken.purpose == TOKEN_PURPOSE_EVENT_ACCESS,
                ContributorAccessToken.revoked_at.is_(None))).all():
            token.revoked_at = _now()
    if grants:
        db.commit()
    invalidate_rehearsal_reminders(db, event)
    return len(grants)


def issue_token(db: Session, grant: EventContributorGrant, *, purpose: str,
                expires_at: datetime | None = None) -> str:
    """Mint one purpose-bound credential. Returns the raw value; only its hash is stored.

    `secrets.token_urlsafe(32)` is 256 bits from the OS CSPRNG - not `random`, not a uuid.
    """
    raw = secrets.token_urlsafe(32)
    ceiling = _now() + TOKEN_MAX_LIFETIME
    effective = min(expires_at, ceiling) if expires_at else ceiling
    db.add(ContributorAccessToken(grant_id=grant.id, event_id=grant.event_id,
                                  token_hash=_hash(raw), purpose=purpose,
                                  expires_at=effective))
    db.commit()
    return raw


def link_is_identity_bound(grant: EventContributorGrant) -> bool:
    """Whether the backend can actually bind this link to one person.

    True only when the grant resolves to a platform account, because that is the only case in
    which `exchange()` has an identity to compare against. The email copy is generated from
    this, so a bearer link is never described as non-forwardable.
    """
    return grant.user_id is not None


FORWARD_NOTE_BOUND = (
    "This link only works when you are signed in as the account it was issued to, so "
    "forwarding it gives nobody else access."
)
FORWARD_NOTE_BEARER = (
    "Keep this link private. It is personal to you and expires shortly after the event, but "
    "anyone who has it could use it before then."
)


def authorize(db: Session, grant: EventContributorGrant, *,
              now: datetime | None = None) -> tuple[bool, str | None]:
    """Whether this grant may reach backstage right now. Scoped to its own event only."""
    moment = now or _now()
    if grant.status == "revoked":
        return False, "This contributor access was revoked."
    if grant.status != "accepted":
        return False, "This contributor invitation has not been accepted."
    if is_expired(grant, moment):
        return False, "This contributor access has expired."
    if grant.access_window_start and moment < grant.access_window_start:
        return False, "The backstage join window has not opened yet."
    if grant.access_window_end and moment > grant.access_window_end:
        return False, "The backstage join window has closed."
    return True, None


def exchange(db: Session, raw_token: str, *, purpose: str = TOKEN_PURPOSE_EVENT_ACCESS,
             signed_in_user_id=None
             ) -> tuple[EventContributorGrant | None, str | None]:
    """Validate a personal link and return the grant it authorizes.

    Order matters: the token is matched by HASH, its purpose and expiry are checked, the
    grant's own authorization is checked, and only then is identity compared. A token minted
    for an invitation cannot be used for backstage access, and vice versa.
    """
    if not raw_token:
        return None, "This link is not valid."
    token = db.scalar(
        select(ContributorAccessToken).where(
            ContributorAccessToken.token_hash == _hash(raw_token)))
    if token is None or token.purpose != purpose:
        return None, "This link is not valid."
    if token.revoked_at is not None:
        return None, "This link is no longer valid."
    if token.expires_at and token.expires_at < _now():
        return None, "This link has expired."

    grant = db.get(EventContributorGrant, token.grant_id)
    if grant is None:
        return None, "This link is not valid."
    if purpose == TOKEN_PURPOSE_EVENT_ACCESS:
        ok, reason = authorize(db, grant)
        if not ok:
            return None, reason
    elif grant.status == "revoked":
        return None, "This contributor invitation was revoked."

    # Identity binding, where an identity exists to bind to. An external contributor has no
    # account, so this is skipped for them - and the email says so rather than claiming
    # otherwise. See link_is_identity_bound().
    if grant.user_id is not None and signed_in_user_id is not None:
        if str(signed_in_user_id) != str(grant.user_id):
            return None, "This link was issued to a different person."

    token.use_count += 1
    token.used_by_user_id = signed_in_user_id
    db.commit()
    return grant, None


def issue_media_credential(db: Session, grant: EventContributorGrant) -> dict | None:
    """Mint a short-lived LiveKit credential AFTER authorization.

    This is the only place a media token is created, and it is returned to an authorized
    caller over HTTPS - never placed in an email, a URL or a notification.
    """
    ok, _reason = authorize(db, grant)
    if not ok:
        return None
    from . import livekit

    event = db.get(Event, grant.event_id)
    if event is None:
        return None
    identity = str(grant.user_id) if grant.user_id else f"contributor-{grant.id}"
    room = f"event_{event.id}"
    can_publish = True
    token = livekit.create_stream_token(identity, room, can_publish)
    return {"url": livekit.settings.LIVEKIT_URL, "token": token, "room": room,
            "identity": identity, "role": grant.role}


def notify_access_ready(db: Session, background, grant: EventContributorGrant) -> bool:
    """Send the personal backstage link when the window is opening.

    Deliberately gated on the window being CLOSE: the token's lifetime is short, so mailing
    it days ahead would deliver something already expired on arrival.
    """
    if grant.status != "accepted" or grant.access_window_start is None:
        return False
    now = _now()
    if grant.access_window_start - timedelta(hours=2) > now:
        return False
    if grant.access_window_end and grant.access_window_end < now:
        return False
    if not _claim(db, grant, "access_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-004", org):
        return False

    raw = issue_token(db, grant, purpose=TOKEN_PURPOSE_EVENT_ACCESS,
                      expires_at=grant.access_window_end)
    _ledger(db, org_id=grant.org_id, family="CON-004", transition="access_ready",
            event_id=event.id)
    _queue_one(background, email_mod.send_contributor_access_email, grant,
               window_opens=event_timestamp(event, grant.access_window_start, org),
               join_url=join_url(event.id, raw),
               # Generated from what the backend actually enforces for THIS grant.
               forward_note=(FORWARD_NOTE_BOUND if link_is_identity_bound(grant)
                             else FORWARD_NOTE_BEARER),
               support="Reply to this email if you cannot get in.",
               **_shared(db, grant, event, org))
    return True


# ══ CON-005 — session ended ═════════════════════════════════════════════════════════════

def _session_for(db: Session, grant: EventContributorGrant) -> ContributorSession | None:
    session = db.scalar(
        select(ContributorSession).where(ContributorSession.grant_id == grant.id))
    if session is not None:
        return session
    if grant.user_id is None:
        return None
    # Falls back to the pre-existing (event, user) session so a contributor invited the old
    # way is still covered.
    return db.scalar(
        select(ContributorSession).where(ContributorSession.event_id == grant.event_id,
                                         ContributorSession.user_id == grant.user_id))


def end_session(db: Session, session: ContributorSession, *, reason: str) -> bool:
    """Close a backstage session authoritatively.

    A websocket drop must never reach here. `services/contributor.mark_disconnected` writes
    `last_disconnected_at` and nothing else, so a contributor whose connection blips has an
    open session with a recent disconnect timestamp - which is not an ending and produces no
    message.
    """
    if reason not in SESSION_END_REASONS:
        return False
    if session.ended_at is not None:
        return False
    session.ended_at = _now()
    session.end_reason = reason
    db.commit()
    return True


def end_sessions_for_event(db: Session, event: Event, background=None) -> int:
    """Close every open backstage session because the event itself ended."""
    background = background or _Bg()
    if event.status not in ("ended", "cancelled"):
        return 0
    closed = 0
    for grant in db.scalars(
        select(EventContributorGrant).where(
            EventContributorGrant.event_id == event.id)).all():
        session = _session_for(db, grant)
        if session is None or session.ended_at is not None:
            continue
        if end_session(db, session, reason="event_ended"):
            notify_session_ended(db, background, grant, session)
            closed += 1
    return closed


def notify_session_ended(db: Session, background, grant: EventContributorGrant,
                         session: ContributorSession) -> bool:
    """One notice per authoritative session end."""
    if session.ended_at is None or session.end_reason not in SESSION_END_REASONS:
        return False
    if not _claim(db, session, "ended_notified_at"):
        return False
    event, org = _grant_context(db, grant)
    if event is None or not _may_send("CON-005", org):
        return False

    _ledger(db, org_id=grant.org_id, family="CON-005", transition="session_ended",
            event_id=event.id, detail=session.end_reason)
    _queue_one(background, email_mod.send_contributor_session_ended_email, grant,
               ended_at=event_timestamp(event, session.ended_at, org),
               reason=SESSION_END_LABELS[session.end_reason],
               further_action=("Nothing further is needed."
                               if session.end_reason in ("left", "event_ended") else
                               "Contact the event team if you still need access."),
               **_shared(db, grant, event, org))
    return True


# ══ sweeper ═════════════════════════════════════════════════════════════════════════════

def sweep(db: Session, background=None) -> dict:
    """One durable pass over every deadline-driven contributor obligation.

    Runs on the existing leader-elected planning ticker
    (`services/event_planning.run_event_planning_sweeper`) rather than a CON-specific queue,
    so reminders survive a process restart without new infrastructure.
    """
    background = background or _Bg()
    counts = {"expired": 0, "rehearsal_reminders": 0, "access_links": 0}
    try:
        counts["expired"] = expire_due(db, background)
    except Exception:  # noqa: BLE001 - one family must not stop the others
        log.exception("CON-001 invitation expiry sweep failed")

    try:
        for grant in db.scalars(
            select(EventContributorGrant).where(
                EventContributorGrant.status == "accepted")).all():
            if notify_rehearsal_reminder(db, background, grant):
                counts["rehearsal_reminders"] += 1
    except Exception:  # noqa: BLE001
        log.exception("CON-003 rehearsal reminder sweep failed")

    try:
        for grant in db.scalars(
            select(EventContributorGrant).where(
                EventContributorGrant.status == "accepted",
                EventContributorGrant.access_notified_at.is_(None),
                EventContributorGrant.access_window_start.isnot(None))).all():
            if notify_access_ready(db, background, grant):
                counts["access_links"] += 1
    except Exception:  # noqa: BLE001
        log.exception("CON-004 access link sweep failed")
    return counts
