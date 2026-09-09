"""Proposal, booking and event-approval communications (ZST-EC-001 LVE-001, LVE-003).

**Recipient resolution is the substance of LVE-001, not the templates.** The pre-existing
`routers/commercial._order_contact` returned ONE address: the purchaser if the order named
one, otherwise the commercial account's billing contact. That conflates four distinct people:

    event owner        - who runs the event (Event.created_by, plus the assigned host)
    purchaser          - who placed the order (EventOrder.purchaser_id)
    billing contact    - who receives invoices (CommercialAccount.billing_contact_email)
    commercial contact - who may accept commercial terms (security.commercial_can(.., "accept"))

They are frequently the same person on a small self-serve order and frequently NOT on a
managed one, where a procurement address bought an event somebody else is running. Sending a
proposal only to a billing address means the person who has to act on it never sees it.
`resolve()` returns all four separately and `Recipients.addresses()` deduplicates - so the
same person on two roles is mailed once, and two different people are both reached.

**Timezone.** LVE requires the EVENT's own zone (`Event.timezone`), not the server's, the
browser's or the Organization's. `event_timestamp()` uses it and falls back to the
Organization's only when the event has none, always labelling whichever zone it rendered.

Recipient-local time is NOT rendered: `User` carries no timezone column, so there is no
authoritative recipient zone to convert into. Stated in the LVE report as an unsupported
requirement rather than guessed at from a browser or a locale.

**Scope, responsibilities and assumptions** are DERIVED from the ServiceProfile's own
`requires_*` flags and the order's lines, never authored as prose. Those flags are what
actually decide who must do what: `requires_backup_contribution` genuinely is a customer
obligation, `managed_only` genuinely is a Zoiko one. A proposal that described obligations
the readiness gates do not enforce would be a sales document, not a system message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import email as email_mod
from ..email import UnsafeLinkError
from ..models import (
    CommercialAccount,
    Event,
    EventAssignment,
    EventLifecycleEvent,
    EventOrder,
    EventOrderLine,
    Organization,
    Quote,
    ServiceProfile,
    User,
)
from ..security import commercial_can
from . import notifications

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── timezone ────────────────────────────────────────────────────────────────────────────

def event_zone(event: Event | None, org: Organization | None = None) -> tuple[ZoneInfo, str]:
    """The event's authoritative zone, and the name to label it with.

    Event.timezone wins outright. The Organization's zone is only a fallback for an event
    that never had one set, and UTC is the last resort - labelling a wrong zone would be
    worse than showing UTC, which is the same posture org_comms.org_timestamp takes.
    """
    for name in ((getattr(event, "timezone", None) or "").strip(),
                 (getattr(org, "timezone", None) or "").strip()):
        if not name:
            continue
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("Unusable timezone %r; falling back", name)
    return timezone.utc, "UTC"


def event_timestamp(event: Event | None, moment: datetime | None,
                    org: Organization | None = None) -> str:
    """Exact timestamp in the EVENT's timezone, with the zone always named."""
    if moment is None:
        return "Not set"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone, name = event_zone(event, org)
    return f"{moment.astimezone(zone).strftime('%d %b %Y, %I:%M %p')} {name}"


# There is no User.timezone column anywhere in this codebase, so no recipient has an
# authoritative zone to convert into. LVE explicitly forbids converting without one.
RECIPIENT_LOCAL_SUPPORTED = False
LOCAL_TIME_NOTE = ("All times are shown in the event's timezone. Zoiko Steam does not store a "
                   "personal timezone, so no local conversion is shown.")


def _duration_between(start: datetime | None, end: datetime | None) -> str:
    """Human duration between two committed moments, or an honest "Unknown"."""
    if not start or not end:
        return "Unknown"
    seconds = max(0, int((end - start).total_seconds()))
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        return f"{seconds // 60} minutes"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ── recipients ──────────────────────────────────────────────────────────────────────────

class Recipients:
    """The four distinct commercial/operational roles, resolved separately."""

    def __init__(self, event_owner=None, host=None, purchaser=None,
                 billing=None, commercial=None):
        self.event_owner = event_owner            # User | None
        self.host = host                          # User | None
        self.purchaser = purchaser                # User | None
        self.billing = billing                    # (email, name) | None
        self.commercial = list(commercial or [])  # list[User]

    def _pairs(self) -> list[tuple[str, str]]:
        out = []
        for user in (self.event_owner, self.host, self.purchaser, *self.commercial):
            if user is not None and getattr(user, "email", None):
                out.append((user.email, user.full_name or "there"))
        if self.billing and self.billing[0]:
            out.append((self.billing[0], self.billing[1] or "there"))
        return out

    def addresses(self) -> list[str]:
        """Deduplicated, case-insensitively, preserving first-seen order."""
        seen, out = set(), []
        for address, _name in self._pairs():
            key = address.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(address)
        return out

    def name_for(self, address: str) -> str:
        target = (address or "").strip().lower()
        for candidate, name in self._pairs():
            if candidate.strip().lower() == target:
                return name
        return "there"

    def owner_is_purchaser(self) -> bool:
        """Whether the same person owns the event and placed the order.

        LVE-001 forbids substituting a purchaser for an event owner. This reports whether
        they genuinely coincide, so the message can say so truthfully instead of the code
        silently assuming it.
        """
        if self.event_owner is None or self.purchaser is None:
            return False
        return str(self.event_owner.id) == str(self.purchaser.id)


def commercial_contacts(db: Session, org_id) -> list[User]:
    """Members who may actually accept commercial terms.

    Derived from the authorization rule (`commercial_can(user, "accept")` - org_admin and
    billing_admin) rather than a second hand-maintained list of "people who probably care".
    """
    if not org_id:
        return []
    members = db.scalars(
        select(User).where(User.org_id == org_id, User.is_active.is_(True),
                           User.role != "super_admin")
    ).all()
    out = []
    for user in members:
        try:
            if commercial_can(user, "accept"):
                out.append(user)
        except ValueError:  # pragma: no cover - constant action name
            continue
    return out


def event_host(db: Session, event: Event | None) -> User | None:
    """The assigned host, if one exists. EventAssignment(role="host") is the real record of
    who runs the event, which is not necessarily whoever created the row."""
    if event is None:
        return None
    assignment = db.scalar(
        select(EventAssignment).where(EventAssignment.event_id == event.id,
                                      EventAssignment.role == "host")
    )
    return db.get(User, assignment.user_id) if assignment else None


def resolve(db: Session, event: Event | None, order: EventOrder | None = None) -> Recipients:
    """All four roles for one event/order, each from its own authoritative field."""
    owner = db.get(User, event.created_by) if event and event.created_by else None
    host = event_host(db, event)
    purchaser = db.get(User, order.purchaser_id) if order and order.purchaser_id else None

    billing = None
    account_id = (order.commercial_account_id if order
                  else getattr(event, "commercial_account_id", None))
    if account_id:
        account = db.get(CommercialAccount, account_id)
        if account and account.billing_contact_email:
            billing = (account.billing_contact_email, account.billing_contact_name)

    return Recipients(event_owner=owner, host=host, purchaser=purchaser, billing=billing,
                      commercial=commercial_contacts(db, event.org_id if event else None))


# ── shared plumbing ─────────────────────────────────────────────────────────────────────

def is_test_org(org: Organization | None) -> bool:
    """Authoritative test mode: Organization.is_test, the same stored flag MED-001 uses.
    Never inferred from a hostname, an event name or an address."""
    return bool(org is not None and getattr(org, "is_test", False))


def _claim(db: Session, row, column: str) -> bool:
    """Durable, single-shot claim of one communication event."""
    if getattr(row, column) is not None:
        return False
    setattr(row, column, _now())
    db.commit()
    return True


def _ledger(db: Session, *, org_id, family: str, transition: str, event_id=None,
            quote_id=None, order_id=None, detail: str | None = None, actor_id=None) -> None:
    db.add(EventLifecycleEvent(org_id=org_id, event_id=event_id, quote_id=quote_id,
                               order_id=order_id, family=family, transition=transition,
                               detail=detail, actor_id=actor_id))
    db.commit()


def _queue(background, send, recipients: Recipients, **kwargs) -> None:
    """Queue one message per deduplicated address.

    Wrapped so a provider failure can never propagate into the caller's transaction: LVE
    requires that Resend being down cannot undo a proposal approval, a booking acceptance or
    any other committed state.
    """
    try:
        for address in recipients.addresses():
            background.add_task(send, address, name=recipients.name_for(address), **kwargs)
    except UnsafeLinkError:
        log.exception("LVE notice not queued: APP_URL unsafe for this environment")


class _Bg:
    """Inline background shim for callers already off the request path (tickers, workers)."""

    def add_task(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - a notice must never break committed state
            log.exception("LVE notice failed")


def _may_send(family: str, org: Organization | None) -> bool:
    """Preference gate. LVE-001/003 are contract and access communications, so the registry
    classifies them mandatory - this call is what makes that a checked property rather than
    an assumption."""
    return notifications.should_send_operational_notification(family=family, org=org)


# ── scope / responsibilities / assumptions (LVE-001) ────────────────────────────────────
#
# Every line below is generated FROM a ServiceProfile flag that a readiness gate actually
# enforces (crud/commercial.required_readiness_checks). Nothing here is sales prose.

CUSTOMER_OBLIGATIONS = {
    "requires_backup_contribution": "Provide a second, independent contribution path (backup encoder or feed)",
    "requires_preview_return": "Accept and monitor the preview return feed during the event",
    "requires_full_rehearsal": "Attend a full rehearsal before the event",
}
ZOIKO_OBLIGATIONS = {
    "requires_dual_recording": "Capture two independent recordings of the event",
    "requires_command_owner": "Assign a named Zoiko Steam command owner to the event",
    "managed_only": "Run the event as a managed delivery rather than self-service",
    "requires_reserved_capacity": "Reserve dedicated delivery capacity for the event window",
    "requires_change_freeze": "Apply a change freeze ahead of the event",
}
# Stated as an assumption because it is a precondition the platform does not itself verify at
# proposal time - it becomes a readiness gate later.
STANDING_ASSUMPTIONS = (
    "Scope, dates and audience size are as recorded on the event at the time of this proposal",
    "Any change to those may change price, capacity or readiness requirements",
)


def _profile_of(db: Session, event: Event | None,
                order: EventOrder | None) -> ServiceProfile | None:
    profile_id = (getattr(order, "service_profile_id", None)
                  or getattr(event, "service_profile_id", None))
    return db.get(ServiceProfile, profile_id) if profile_id else None


def scope_summary(profile: ServiceProfile | None) -> str:
    if profile is None:
        return "Standard self-service delivery (no managed service profile on this event)"
    tier = (profile.risk_tier or "").upper()
    return f"{profile.name} ({tier})" if tier else profile.name


def included_services(db: Session, order: EventOrder | None) -> list[str]:
    """The order's own frozen lines. A quote carries a total but no lines, so a proposal
    issued before an order exists honestly reports that pricing detail follows."""
    if order is None:
        return []
    lines = db.scalars(
        select(EventOrderLine).where(EventOrderLine.event_order_id == order.id)
    ).all()
    return [f"{ln.description or ln.service_code} (x{ln.quantity:g})" for ln in lines]


def _obligations(profile: ServiceProfile | None, mapping: dict) -> list[str]:
    if profile is None:
        return []
    return [text for flag, text in mapping.items() if getattr(profile, flag, False)]


def responsibilities(profile: ServiceProfile | None) -> tuple[list[str], list[str]]:
    """(customer, zoiko) obligations, each derived from an enforced profile flag."""
    customer = _obligations(profile, CUSTOMER_OBLIGATIONS)
    zoiko = _obligations(profile, ZOIKO_OBLIGATIONS)
    if not customer:
        customer = ["Provide event content, speakers and scheduling details"]
    if not zoiko:
        zoiko = ["Provide the streaming platform and standard delivery support"]
    return customer, zoiko


# Zoiko Steam has no venue/location column and no in-person delivery concept anywhere in the
# model - every event is delivered through the streaming platform. Stated as a constant so
# the proposal and approval messages describe the delivery model without inventing a venue.
DELIVERY_MODEL = "Virtual - delivered through the Zoiko Steam streaming platform"


# == LVE-001 - proposal and booking lifecycle ===========================================

def _quote_context(db: Session, quote: Quote):
    event = db.get(Event, quote.event_id)
    org = db.get(Organization, event.org_id) if event else None
    order = db.scalar(
        select(EventOrder).where(EventOrder.event_id == quote.event_id)
        .order_by(EventOrder.created_at.desc())
    ) if event else None
    return event, org, order


def notify_proposal_ready(db: Session, background, quote: Quote) -> bool:
    """Announce an ISSUED proposal.

    Gated on the committed status, not on the caller's intention: a draft quote announces
    nothing, which is the difference between "we prepared a proposal" and "a proposal is
    available for you to review".
    """
    if quote.status != "issued":
        return False
    if not _claim(db, quote, "proposal_ready_notified_at"):
        return False
    event, org, order = _quote_context(db, quote)
    if event is None or not _may_send("LVE-001", org):
        return False
    people = resolve(db, event, order)
    if not people.addresses():
        return False

    profile = _profile_of(db, event, order)
    customer, zoiko = responsibilities(profile)
    _ledger(db, org_id=event.org_id, family="LVE-001", transition="proposal_ready",
            event_id=event.id, quote_id=quote.id, order_id=order.id if order else None)
    _queue(background, email_mod.send_proposal_ready_email, people,
           event_title=event.title or "your event",
           reference="Q-" + str(quote.id)[:8],
           scope=scope_summary(profile),
           services=included_services(db, order) or [
               "Detailed line items are confirmed at booking"],
           customer_responsibilities=customer,
           zoiko_responsibilities=zoiko,
           assumptions=list(STANDING_ASSUMPTIONS),
           amount=f"{quote.amount} {quote.currency}",
           valid_until=event_timestamp(event, quote.valid_until, org),
           validity_note=("This proposal has no stated expiry."
                          if quote.valid_until is None else
                          "The proposal cannot be accepted after this time."),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_booking_accepted(db: Session, background, quote: Quote) -> bool:
    """Announce a committed acceptance. Only ever called after accept_quote() commits."""
    if quote.status != "accepted":
        return False
    if not _claim(db, quote, "accepted_notified_at"):
        return False
    event, org, order = _quote_context(db, quote)
    if event is None or not _may_send("LVE-001", org):
        return False
    people = resolve(db, event, order)
    if not people.addresses():
        return False

    profile = _profile_of(db, event, order)
    _ledger(db, org_id=event.org_id, family="LVE-001", transition="accepted",
            event_id=event.id, quote_id=quote.id, order_id=order.id if order else None)
    _queue(background, email_mod.send_booking_accepted_email, people,
           event_title=event.title or "your event",
           reference="Q-" + str(quote.id)[:8],
           accepted_at=event_timestamp(event, quote.accepted_at, org),
           scope=scope_summary(profile),
           services=included_services(db, order) or ["As quoted"],
           event_owner=(people.event_owner.email if people.event_owner
                        else "Not recorded on this event"),
           commercial_contact=(people.purchaser.email if people.purchaser
                               else (people.billing[0] if people.billing else "Not recorded")),
           same_person=people.owner_is_purchaser(),
           next_step="Complete the event intake so planning can begin.",
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_booking_changed(db: Session, background, event: Event, *, previous: str,
                           current: str, change_reference: str, effective_at) -> bool:
    """Announce a COMMITTED, approved booking change.

    Takes the two summaries rather than deriving them, because only the caller holds the
    pre-change state - by the time this runs the row already carries the new values.
    """
    if event is None:
        return False
    org = db.get(Organization, event.org_id)
    if not _may_send("LVE-001", org):
        return False
    order = db.scalar(
        select(EventOrder).where(EventOrder.event_id == event.id)
        .order_by(EventOrder.created_at.desc()))
    people = resolve(db, event, order)
    if not people.addresses():
        return False
    _ledger(db, org_id=event.org_id, family="LVE-001", transition="booking_changed",
            event_id=event.id, order_id=order.id if order else None,
            detail=previous + " -> " + current)
    _queue(background, email_mod.send_booking_changed_email, people,
           event_title=event.title or "your event",
           reference=change_reference,
           previous_summary=previous,
           current_summary=current,
           effective_at=event_timestamp(event, effective_at, org),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def expire(db: Session, quote: Quote) -> bool:
    """Move an issued proposal past its validity to EXPIRED.

    crud.commercial.accept_quote already refuses an expired quote and flips the status on
    the way past, so acceptance was never actually possible after `valid_until`. What was
    missing is that the row sat in `issued` until somebody TRIED - so nothing could announce
    the expiry, and the console showed a live proposal that could not be accepted. This is
    the deadline-driven half of the same rule, not a second opinion about it.
    """
    if quote.status != "issued" or quote.valid_until is None:
        return False
    if quote.valid_until >= _now():
        return False
    quote.status = "expired"
    db.commit()
    return True


def notify_proposal_expired(db: Session, background, quote: Quote) -> bool:
    if quote.status != "expired":
        return False
    if not _claim(db, quote, "expired_notified_at"):
        return False
    event, org, order = _quote_context(db, quote)
    if event is None or not _may_send("LVE-001", org):
        return False
    people = resolve(db, event, order)
    if not people.addresses():
        return False
    _ledger(db, org_id=event.org_id, family="LVE-001", transition="expired",
            event_id=event.id, quote_id=quote.id)
    _queue(background, email_mod.send_proposal_expired_email, people,
           event_title=event.title or "your event",
           reference="Q-" + str(quote.id)[:8],
           expired_at=event_timestamp(event, quote.valid_until, org),
           reissue_note=("A proposal cannot be accepted once it has expired. Ask your Zoiko "
                         "Steam contact to issue a new one if you still want to proceed."),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def expire_due(db: Session, background=None) -> int:
    """One sweep over issued proposals whose validity has passed."""
    background = background or _Bg()
    sent = 0
    for quote in db.scalars(
        select(Quote).where(Quote.status == "issued", Quote.valid_until.isnot(None),
                            Quote.valid_until < _now())
    ).all():
        if expire(db, quote) and notify_proposal_expired(db, background, quote):
            sent += 1
    return sent


# == LVE-003 - event details and assigned team ==========================================

# Event statuses that mean the record is genuinely confirmed, not merely created or made
# visible. "published" is deliberately NOT here: publishing makes an event visible, it does
# not confirm that it will run.
APPROVED_STATUSES = ("scheduled", "ready_to_arm", "armed")


def is_approved(event: Event | None, order: EventOrder | None = None) -> bool:
    """Whether this event has reached a real approved/confirmed state.

    Two authorities, either sufficient: a commercial event is confirmed by its order's
    lifecycle reaching CONFIRMED, and a non-commercial one by being SCHEDULED with a real
    start time. Creating a draft satisfies neither, which is what stops LVE-003 from
    degenerating into the existing "event created" notice.
    """
    if event is None:
        return False
    if getattr(order, "lifecycle_state", None) == "confirmed":
        return True
    return event.status in APPROVED_STATUSES and event.start_time is not None


def team(db: Session, event: Event) -> list:
    """(role, name, user_id) for every assignment, in a stable order."""
    rows = db.scalars(
        select(EventAssignment).where(EventAssignment.event_id == event.id)).all()
    out = []
    for row in rows:
        user = db.get(User, row.user_id)
        out.append((row.role, (user.full_name or user.email) if user else "Unknown",
                    str(row.user_id)))
    return sorted(out, key=lambda r: (r[0], r[2]))


def team_signature(members: list) -> str:
    """Identity of the team, independent of display names.

    Keyed on (role, user_id) ONLY. Renaming a staff member produces the same signature and
    therefore sends nothing - which is exactly what LVE-003 requires.
    """
    return ";".join(role + ":" + uid for role, _name, uid in members)


def _access_model(event: Event) -> str:
    """The audience-access model, from the event's own committed columns."""
    visibility = {"public": "Public - anyone with the link",
                  "private": "Private - invited or linked attendees only",
                  "unlisted": "Unlisted - reachable by direct link only"}.get(
        event.visibility, event.visibility or "Not set")
    if event.registration_required:
        cap = f", capped at {event.registration_limit}" if event.registration_limit else ""
        return f"{visibility}; registration required{cap}"
    return f"{visibility}; no registration required"


def _contributor_plan(db: Session, event: Event) -> str:
    speakers = [m for m in team(db, event) if m[0] == "speaker"]
    if not speakers:
        return "No contributors assigned yet"
    return f"{len(speakers)} contributor(s) assigned"


def _replay_policy(db: Session, event: Event) -> str:
    """Truthful replay position. Publication is never automatic in this platform, so an
    entitlement that exists but is unpublished must not read as "replay available"."""
    from ..crud import commercial as commercial_crud

    entitlement = commercial_crud.get_replay_entitlement(db, event.id, scope="audience")
    if entitlement is None:
        return "No replay entitlement created yet"
    if entitlement.publish_state == "published":
        return "Replay published"
    return "Replay not published (currently " + entitlement.publish_state.replace("_", " ") + ")"


def notify_event_approved(db: Session, background, event: Event,
                          order: EventOrder | None = None) -> bool:
    """Announce an approved event and its confirmed details."""
    if not is_approved(event, order):
        return False
    if not _claim(db, event, "approved_notified_at"):
        return False
    org = db.get(Organization, event.org_id)
    if not _may_send("LVE-003", org):
        return False
    people = resolve(db, event, order)
    if not people.addresses():
        return False

    members = team(db, event)
    _ledger(db, org_id=event.org_id, family="LVE-003", transition="event_approved",
            event_id=event.id)
    _queue(background, email_mod.send_event_approved_email, people,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           start_at=event_timestamp(event, event.start_time, org),
           end_at=event_timestamp(event, event.end_time, org),
           zone=event_zone(event, org)[1],
           local_note=LOCAL_TIME_NOTE,
           delivery_model=DELIVERY_MODEL,
           access_model=_access_model(event),
           contributor_plan=_contributor_plan(db, event),
           recording_requirement=("Recording enabled" if event.recording_enabled
                                  else "Recording not enabled"),
           replay_policy=_replay_policy(db, event),
           team=[f"{role.title()}: {name}" for role, name, _ in members]
                or ["Not yet assigned"],
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return True


def notify_team(db: Session, background, event: Event) -> str | None:
    """Announce a real team assignment or change. Returns the variant, or None.

    The signature comparison is the whole point: this is safe to call after ANY assignment
    write, because a call that does not change WHO is on the team sends nothing.
    """
    members = team(db, event)
    signature = team_signature(members)
    if event.team_signature == signature:
        return None
    if not members:
        # An emptied team is a change, but there is no team to announce. Record it so the
        # next real assignment is correctly seen as an assignment rather than a change.
        event.team_signature = signature
        db.commit()
        return None

    org = db.get(Organization, event.org_id)
    variant = "assigned" if not event.team_signature else "changed"
    previous_names = event.team_display or "No team assigned"
    current_names = ", ".join(f"{role.title()}: {name}" for role, name, _ in members)

    event.team_signature = signature
    event.team_display = current_names[:500]
    event.team_notified_at = _now()
    db.commit()

    if not _may_send("LVE-003", org):
        return None
    people = resolve(db, event, None)
    if not people.addresses():
        return None

    host = event_host(db, event)
    _ledger(db, org_id=event.org_id, family="LVE-003", transition="team_" + variant,
            event_id=event.id, detail=previous_names + " -> " + current_names)
    _queue(background, email_mod.send_event_team_email, people,
           variant=variant,
           event_title=event.title or "your event",
           reference=str(event.id)[:8],
           previous_team=previous_names,
           current_team=current_names,
           effective_at=event_timestamp(event, _now(), org),
           primary_contact=(host.email if host else "Not yet assigned"),
           event_id=str(event.id),
           org_name=org.name if org else "your Organization",
           test_mode=is_test_org(org))
    return variant
