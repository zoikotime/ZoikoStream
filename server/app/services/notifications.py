"""Message classification and notification-preference enforcement (ZST-EC-001 ORG-012).

This module is the single server-side authority on two questions:

    1. What CLASS is this message?          -> FAMILY_CLASS
    2. May a preference suppress it?        -> should_send_operational_notification()

Both answers are derived from the canonical email register, never from a UI setting name.
That direction matters: before this, `security_alerts` was a switch in a settings page, and
whether a Class A security email was mandatory would have depended on what that switch was
called. Now the class is a property of the message family, and the preference layer is only
ever consulted for families the register marks configurable.

The preference booleans were entirely inert before this change - `organizations.notifications`
was written by PATCH /organization/notifications and read by nothing else in the codebase.
Four of the eight also name emails that do not exist anywhere (see CATALOG `available`),
which is why the catalog reports availability rather than pretending every switch works.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── message classes ─────────────────────────────────────────────────────────────────────

CLASS_A = "A"   # Security / legal / access — mandatory, never suppressible
CLASS_B = "B"   # Operational — configurable where policy allows
CLASS_C = "C"   # Transactional — mandatory when it carries the result of an action
# Marketing / product education. Its own class, deliberately not a variant of B: a Class B
# operational message is suppressible by PREFERENCE, whereas a Class D message requires an
# affirmative consent record before it may be sent at all. Nothing in this file can promote
# a D family to mandatory, and `is_marketing()` below is what the send paths check.
CLASS_D = "D"   # Marketing / product education — opt-in, consent-gated, always suppressible

# Canonical family -> class. Authoritative, server-side, and the only thing that decides
# whether a preference is even consulted.
FAMILY_CLASS = {
    # Identity — every one of these is Class A except IDN-002.
    "IDN-001": CLASS_A,   # email verification
    "IDN-002": CLASS_C,   # account ready
    "IDN-003": CLASS_A,   # new sign-in detected
    "IDN-004": CLASS_A,   # suspicious sign-in blocked
    "IDN-005": CLASS_A,   # credential lifecycle
    "IDN-006": CLASS_A,   # security settings changed
    "IDN-007": CLASS_A,   # account recovery lifecycle
    "IDN-008": CLASS_A,   # account restriction / deletion
    # Organization administration.
    "ORG-001": CLASS_C,   # invitation lifecycle
    "ORG-002": CLASS_C,   # member joined
    "ORG-003": CLASS_A,   # role / workspace / mode access changed
    "ORG-004": CLASS_A,   # membership removed
    "ORG-007": CLASS_A,   # access review lifecycle
    "ORG-008": CLASS_A,   # ownership transfer
    "ORG-009": CLASS_A,   # authorized support access
    "ORG-010": CLASS_A,   # organization operational restriction
    "ORG-012": CLASS_C,   # notification preferences changed
    # Operational / commercial.
    "EVT-001": CLASS_B,   # event scheduled
    "COM-001": CLASS_C,   # order / receipt / refund — contract communications
    # Media operations. MED-011 is Class A: retention, legal hold and deletion are legal
    # and data-governance messages, and a preference must never be able to suppress the
    # notice that an asset was destroyed or placed under hold.
    "MED-007": CLASS_B,   # recording health lifecycle
    "MED-008": CLASS_B,   # recording finalization — the only media family a preference gates
    "MED-009": CLASS_B,   # replay lifecycle
    "MED-011": CLASS_A,   # retention / legal hold / deletion
    # Live event operations. LVE-001 and LVE-003 carry contract and confirmed-delivery
    # meaning: a proposal, its expiry and a confirmed event are records the customer acts on,
    # so they are mandatory Class C rather than suppressible operational mail. LVE-002/004/005
    # are operational planning prompts and stay Class B.
    "LVE-001": CLASS_C,   # proposal / booking lifecycle
    "LVE-002": CLASS_B,   # event intake lifecycle
    "LVE-003": CLASS_C,   # approved event details and assigned team
    "LVE-004": CLASS_B,   # planning actions required
    "LVE-005": CLASS_B,   # rehearsal lifecycle
    # LVE-006/008/010 are mandatory: a blocked event, a moved schedule and a cancellation
    # all change what the customer must DO, and a preference must never be able to silence
    # them. LVE-011 completion carries the contractual outcome of the event. LVE-007/009/012
    # are operational and genuinely suppressible.
    "LVE-006": CLASS_A,   # readiness gate lifecycle
    "LVE-007": CLASS_B,   # final event-day brief
    "LVE-008": CLASS_A,   # event schedule change
    "LVE-009": CLASS_B,   # event-day activation
    "LVE-010": CLASS_A,   # interruption and cancellation
    "LVE-011": CLASS_C,   # completion and replay
    "LVE-012": CLASS_B,   # post-event evidence and retention
    # Contributors. CON-001 and CON-004 are the only way a contributor learns they have
    # access, and that access is time-bound - a preference must never be able to silence an
    # invitation, a revocation or the link somebody needs to appear on air. CON-005 is the
    # record that their session closed. CON-002/003 are operational prompts.
    "CON-001": CLASS_A,   # contributor invitation lifecycle
    "CON-002": CLASS_B,   # contributor technical check
    "CON-003": CLASS_B,   # contributor rehearsal reminder
    "CON-004": CLASS_A,   # contributor event-day access
    "CON-005": CLASS_C,   # contributor session ended
    # Commerce. All three are financially required records the customer is entitled to: an
    # invoice, a payment outcome, a refund or credit, a dispute, and the entitlement limit
    # that is actively blocking them. A preference must not be able to hide money owed, money
    # returned, or the reason an action is refused.
    "COM-006": CLASS_C,   # invoice and payment confirmation
    "COM-007": CLASS_A,   # payment problem lifecycle
    "COM-008": CLASS_B,   # usage and entitlement lifecycle
    # Support. SUP-001/002/004 are transactional records of a case the customer themselves
    # raised, and the action request is the only way they learn the case is stalled on them.
    # SUP-003 escalation is operational. Feedback rides on SUP-004 but is separately gated
    # by is_feedback_eligible(), so classification - not a preference - excludes sensitive
    # cases.
    "SUP-001": CLASS_C,   # support case opened
    "SUP-002": CLASS_C,   # case update and customer action
    "SUP-003": CLASS_B,   # support escalation
    "SUP-004": CLASS_C,   # resolution and feedback
    # Security. Every one of these is Class A: a confirmed high-risk event, emergency access
    # to a tenant, a security incident, a confirmed policy violation and a content
    # restriction all change what the customer must do, and none may be silenced by a
    # notification preference. SEC-004 is the reporter's own transactional receipt.
    "SEC-001": CLASS_A,   # urgent security alert
    "SEC-002": CLASS_A,   # break-glass lifecycle
    "SEC-003": CLASS_A,   # organization security incident
    "SEC-004": CLASS_C,   # abuse report lifecycle
    "SEC-005": CLASS_A,   # content restriction and appeal
    "SEC-006": CLASS_A,   # policy violation / security-contact verification
    # Privacy. A data-subject request, its verification, its outcome and a notice requiring
    # consent are all legal-basis communications: a notification preference must never be
    # able to suppress the answer to a right the person exercised, or the choice a lawful
    # basis depends on.
    "PRV-001": CLASS_A,   # request intake and verification
    "PRV-002": CLASS_A,   # request status and deadlines
    "PRV-003": CLASS_A,   # export and deletion
    "PRV-004": CLASS_A,   # notice, consent and retention exception
    # Public status. Class B and genuinely suppressible - a status subscription is opt-in
    # and its own communication domain. Unsubscribing here stops STATUS mail only, which is
    # exactly why these are NOT classified alongside security or billing.
    "STS-001": CLASS_C,   # subscription lifecycle (the subscriber's own transaction)
    "STS-002": CLASS_B,   # public incident investigation
    "STS-003": CLASS_B,   # public incident recovery and resolution
    "STS-004": CLASS_B,   # correction and post-incident review
    "STS-005": CLASS_B,   # scheduled maintenance notice
    "STS-006": CLASS_B,   # maintenance execution
    # Trust Center. Security and trust communications, NOT marketing: a security advisory
    # reaches a verified security contact because they are the security contact, and no
    # marketing preference exists that could suppress it.
    "TRU-001": CLASS_A,   # security advisory lifecycle
    "TRU-002": CLASS_C,   # trust evidence request and document access
    "TRU-003": CLASS_C,   # vulnerability disclosure lifecycle
    # Marketing and product education. Class D across the board - every one of these
    # requires an active MarketingSubscription carrying the matching topic, and every one
    # carries one-click unsubscribe.
    "MKT-000": CLASS_C,   # marketing consent lifecycle (the subscriber's own transaction)
    "MKT-001": CLASS_D,   # release notes digest
    "MKT-002": CLASS_D,   # feature availability announcement
    "MKT-003": CLASS_D,   # developer onboarding series
    "MKT-004": CLASS_D,   # live events education and webinar follow-up
}

# Families whose delivery is gated on a marketing consent record rather than on a
# preference. Kept as an explicit set so the separation is checkable in one place: a family
# here may never be mandatory, and a mandatory family may never be here.
MARKETING_FAMILIES = frozenset({"MKT-001", "MKT-002", "MKT-003", "MKT-004"})

# Families that carry contract, legal or access meaning and therefore stay mandatory even
# though they are not Class A. A payment receipt is a record the customer is entitled to;
# an invitation is the only way its recipient learns access was offered.
MANDATORY_CLASS_C = frozenset({
    "IDN-001", "IDN-002", "ORG-001", "ORG-012", "COM-001",
    # A proposal, its expiry and a confirmed event are the customer's only record of what
    # was offered and what will be delivered. Suppressing one would mean a booking could
    # expire, or an event be confirmed, with nobody told.
    "LVE-001", "LVE-003",
    # The event ended and its replay position is the record of what was
    # actually delivered.
    "LVE-011",
    # A contributor is often not an organization member and has no other channel;
    # the record that their session closed is theirs to keep.
    "CON-005",
    # An invoice, a receipt, a refund and a credit note are financial
    # records the customer is entitled to keep - the same reasoning that
    # already makes COM-001 mandatory.
    "COM-006",
    # A case the customer opened, and the request that is blocking it. Neither
    # may be suppressed: a stalled case that nobody is told about is the exact
    # silent-stall failure SUP-002 exists to prevent.
    "SUP-001", "SUP-002", "SUP-004",
    # A reporter's acknowledgement and closure are their only record that
    # the report was handled at all.
    "SEC-004",
    # The result of something the recipient asked for. An approved evidence link, a
    # denial, and the notice that access expired are the only record a requester has of
    # a decision they are waiting on - the same reasoning as SEC-004.
    "TRU-002",
    # A researcher's acknowledgement and lifecycle updates. Same as SEC-004, and a
    # researcher usually has no account here at all.
    "TRU-003",
    # The receipt for a consent decision, including an unsubscribe confirmation. A
    # marketing preference must never be able to suppress the proof that marketing was
    # switched off.
    "MKT-000",
})


def message_class(family: str) -> str:
    """Class for one family. Unknown families are treated as Class A.

    Failing closed is deliberate: a family nobody classified is far more likely to be a new
    security message than a new newsletter, and the cost of wrongly sending is far lower
    than the cost of wrongly suppressing.
    """
    cls = FAMILY_CLASS.get(family)
    if cls is None:
        log.warning("Unclassified email family %r treated as Class A (mandatory)", family)
        return CLASS_A
    return cls


def is_mandatory(family: str) -> bool:
    """True when no preference may suppress this family.

    A marketing family is never mandatory, whatever else is recorded about it: the check is
    explicit rather than implied by class, so a future mis-classification cannot make a
    newsletter unsuppressible.
    """
    if family in MARKETING_FAMILIES:
        return False
    return message_class(family) == CLASS_A or family in MANDATORY_CLASS_C


def is_marketing(family: str) -> bool:
    """True when this family requires an affirmative marketing consent record.

    The send paths in services/marketing.py check consent directly (marketing.eligible), so
    this exists for the inverse question: proving that no mandatory family is marketing, and
    that no marketing family can be reached without consent.
    """
    return family in MARKETING_FAMILIES or message_class(family) == CLASS_D


# ── preference catalog ──────────────────────────────────────────────────────────────────
#
# One entry per stored boolean. `available` records whether a send path for it actually
# EXISTS in this codebase — four of the original eight name emails that are never sent
# anywhere, and the settings page must not present those as working switches.

CATALOG = [
    {
        "key": "event_scheduled",
        "label": "Event scheduled",
        "description": "When a new event is created in your Organization.",
        "family": "EVT-001",
        "message_class": CLASS_B,
        "mandatory": False,
        "configurable": True,
        "available": True,
        "scope": "organization",
        "channel": "email",
    },
    {
        "key": "member_joined",
        "label": "Member joined",
        "description": "When an invited member accepts and joins your Organization.",
        "family": "ORG-002",
        "message_class": CLASS_C,
        "mandatory": False,
        "configurable": True,
        "available": True,
        "scope": "organization",
        "channel": "email",
    },
    {
        "key": "billing",
        "label": "Billing and receipts",
        "description": "Order confirmations, receipts, refunds and payment failures.",
        "family": "COM-001",
        "message_class": CLASS_C,
        # Contract communications. A receipt is a record the customer is entitled to keep.
        "mandatory": True,
        "configurable": False,
        "available": True,
        "scope": "organization",
        "channel": "email",
    },
    {
        "key": "security_alerts",
        "label": "Mandatory security notifications",
        "description": ("Critical security, access, legal and account-protection emails "
                        "cannot be disabled."),
        "family": "IDN-003",
        "message_class": CLASS_A,
        "mandatory": True,
        "configurable": False,
        "available": True,
        "scope": "organization",
        "channel": "email",
    },
    # The four below are stored but have NO send path anywhere in this codebase. They are
    # kept so existing saved values are not silently dropped, and reported as unavailable so
    # the settings page stops offering a switch that controls nothing.
    {
        "key": "event_starting",
        "label": "Event starting soon",
        "description": "Not available yet — Zoiko Steam does not send this notification.",
        "family": None, "message_class": CLASS_B, "mandatory": False,
        "configurable": False, "available": False,
        "scope": "organization", "channel": "email",
    },
    {
        # MED-008's READY variant only. The switch is labelled "Recording ready", so it
        # governs exactly the message that says a recording is ready — not the partial,
        # failed or recovered variants of the same family, which are action-required
        # operational alerts, and not MED-007 capture-health alerts, which report a
        # problem happening now. See services/recording_comms.notify_finalized.
        "key": "recording_ready",
        "label": "Recording ready",
        "description": ("When a recording finishes validation and is ready to use. "
                        "Problems with a recording are always sent."),
        "family": "MED-008",
        "message_class": CLASS_B,
        "mandatory": False,
        "configurable": True,
        "available": True,
        "scope": "organization",
        "channel": "email",
    },
    {
        "key": "weekly_summary",
        "label": "Weekly summary",
        "description": "Not available yet — Zoiko Steam does not send this notification.",
        "family": None, "message_class": CLASS_B, "mandatory": False,
        "configurable": False, "available": False,
        "scope": "organization", "channel": "email",
    },
    {
        "key": "mentions",
        "label": "Mentions",
        "description": "Not available yet — Zoiko Steam does not send this notification.",
        "family": None, "message_class": CLASS_B, "mandatory": False,
        "configurable": False, "available": False,
        "scope": "organization", "channel": "email",
    },
]

CATALOG_BY_KEY = {entry["key"]: entry for entry in CATALOG}

# Keys a caller may actually change. Everything else is either mandatory or unavailable, and
# is normalized rather than trusted.
CONFIGURABLE_KEYS = frozenset(e["key"] for e in CATALOG if e["configurable"])
MANDATORY_KEYS = frozenset(e["key"] for e in CATALOG if e["mandatory"])
UNAVAILABLE_KEYS = frozenset(e["key"] for e in CATALOG if not e["available"])

# Family -> preference key, for the families a preference may govern.
FAMILY_PREFERENCE = {
    entry["family"]: entry["key"]
    for entry in CATALOG
    if entry["configurable"] and entry["family"]
}

# Marketing lives in its own domain and is never mixed with operational preferences. There
# is no marketing subsystem in this codebase; the constant exists so a future one has an
# obvious separate home rather than borrowing an operational switch.
MARKETING_PREFERENCE_KEYS = frozenset()


def normalize(prefs: dict | None) -> dict:
    """Force stored preferences into a truthful shape.

    Mandatory keys are pinned True regardless of what was submitted or previously stored, so
    a legacy `security_alerts: false` sitting in the database cannot suppress anything.
    Unavailable keys are pinned False so the UI cannot show an "on" switch for an email the
    platform never sends.
    """
    out = dict(prefs or {})
    for key in MANDATORY_KEYS:
        out[key] = True
    for key in UNAVAILABLE_KEYS:
        out[key] = False
    return out


def defaults() -> dict:
    """Preference set for an organization that has never saved any."""
    return normalize({
        "event_scheduled": True,
        # Opt-OUT, not opt-in. ORG-002 fired unconditionally while the toggles were inert,
        # so defaulting it off at the moment preferences started working would have silently
        # stopped a canonical family for every tenant that never opened the settings page.
        # A preference becoming effective must not change behaviour by itself.
        "member_joined": True,
        "billing": True,
        "security_alerts": True,
        "event_starting": False,
        # Opt-OUT, matching member_joined: a preference becoming effective must not
        # silently stop a family for tenants that never opened the settings page.
        "recording_ready": True,
        "weekly_summary": False,
        "mentions": False,
    })


def effective(org) -> dict:
    """The organization's preferences, normalized. The only correct way to read them."""
    stored = getattr(org, "notifications", None) or {}
    merged = defaults()
    merged.update({k: v for k, v in stored.items() if k in CATALOG_BY_KEY})
    return normalize(merged)


# ── the enforcement point ───────────────────────────────────────────────────────────────

def should_send_operational_notification(*, family: str, org=None, user=None,
                                         channel: str = "email") -> bool:
    """Whether one message may be delivered, given the committed preferences.

    Every configurable sender calls this instead of reading a boolean directly. Class A and
    the mandatory Class C families short-circuit before any preference is consulted, so
    there is no code path in which a stored setting can suppress them — that is what makes
    the guarantee structural rather than a convention every future sender has to remember.
    """
    if channel != "email":
        return False
    if is_mandatory(family):
        return True

    key = FAMILY_PREFERENCE.get(family)
    if key is None:
        # Not mandatory and not governed by any preference: nothing to consult, so send.
        return True

    entry = CATALOG_BY_KEY.get(key)
    if entry is not None and not entry["available"]:
        return False

    if org is None:
        # No organization context to read a preference from. Send: an operational message
        # with no tenant is not something a tenant setting can speak to.
        return True
    return bool(effective(org).get(key, True))


def summarize_change(previous: dict, current: dict) -> list[str]:
    """Human-readable change lines for the ORG-012 body.

    Renders labels and On/Off, never raw JSON, and only for keys that actually moved.
    """
    lines = []
    for entry in CATALOG:
        key = entry["key"]
        was, now = bool(previous.get(key)), bool(current.get(key))
        if was != now:
            lines.append(f"{entry['label']}: {'On' if was else 'Off'} -> "
                         f"{'On' if now else 'Off'}")
    return lines
