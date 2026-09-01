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
}

# Families that carry contract, legal or access meaning and therefore stay mandatory even
# though they are not Class A. A payment receipt is a record the customer is entitled to;
# an invitation is the only way its recipient learns access was offered.
MANDATORY_CLASS_C = frozenset({
    "IDN-001", "IDN-002", "ORG-001", "ORG-012", "COM-001",
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
    """True when no preference may suppress this family."""
    return message_class(family) == CLASS_A or family in MANDATORY_CLASS_C


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
