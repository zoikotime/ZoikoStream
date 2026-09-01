"""Organization operational-state authorization (ZST-EC-001 ORG-010).

The previous audit found ORG-010 INCORRECT for one reason: the email told customers
"New sign-in to this Organization is blocked" while `get_current_user` checked only
`user.is_active`. Suspending an organization changed a label and sent a message; it removed
nothing. This module is the enforcement that makes the message true.

    ACTIVE      -> everything as normal
    RESTRICTED  -> operational routes refused; preserved surfaces stay open
    SUSPENDED   -> operational routes refused; preserved surfaces stay open
    DELETED     -> organization access has ended

Two design decisions worth stating plainly:

  * The restriction is ORGANIZATION-scoped, not identity-scoped. A suspended tenant does not
    disable the person's Zoiko identity — IDN-008 owns that, separately. Sign-in still
    succeeds and the identity still works; what is refused is operational access to the
    restricted organization.

  * Preserved surfaces are an explicit allowlist, not "everything we forgot to block".
    Billing, export, privacy, account/security and support/appeal routes stay reachable so a
    customer can pay an invoice, retrieve their data, exercise privacy rights or appeal the
    restriction. Nothing security-sensitive was loosened to achieve that.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    ORG_STATE_ACTIVE,
    ORG_STATE_DELETED,
    ORG_STATE_RESTRICTED,
    ORG_STATE_SUSPENDED,
    Organization,
    User,
)
from ..security import get_current_user_optional

log = logging.getLogger(__name__)

# States in which normal operational access is withdrawn.
BLOCKING_STATES = frozenset({ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED, ORG_STATE_DELETED})

# Path prefixes that remain reachable while an organization is restricted or suspended.
# Deliberately narrow and explicit: each is here because withholding it would either trap
# the customer's money, their data, or their ability to contest the restriction.
PRESERVED_PREFIXES = (
    "/api/organization/billing",          # settle the outstanding requirement
    "/api/organization/invoices",
    "/api/organization/export",           # retrieve their own data
    "/api/organization/privacy",
    "/api/organization/support",          # appeal / review / contact support
    "/api/organization/support-access",   # ORG-009 decisions must never be trapped
    "/api/organization/settings",         # account + security settings
    "/api/organization/security",
    "/api/organization/notifications",
    "/api/organization/profile",          # read-only identity of the org itself
    "/api/organization/status",           # what state am I in, and why
)

# Identity and auth routes are never organization-gated at all: the restriction is on the
# organization, and a person must still be able to sign in, recover their account and manage
# their own identity.
IDENTITY_PREFIXES = ("/api/auth", "/api/contact")

STATE_LABELS = {
    ORG_STATE_ACTIVE: "active",
    ORG_STATE_RESTRICTED: "restricted",
    ORG_STATE_SUSPENDED: "suspended",
    ORG_STATE_DELETED: "deleted",
}


def org_state(org: Organization | None) -> str:
    """Normalized operational state.

    `trial` is a commercial state, not an operational restriction, so it maps to active —
    treating a trialling customer as restricted would withdraw access nobody withdrew.
    """
    if org is None:
        return ORG_STATE_ACTIVE
    raw = (org.status or ORG_STATE_ACTIVE).strip().lower()
    if raw in (ORG_STATE_RESTRICTED, ORG_STATE_SUSPENDED, ORG_STATE_DELETED):
        return raw
    return ORG_STATE_ACTIVE


def is_preserved_path(path: str) -> bool:
    """True when a path stays reachable under restriction."""
    return (path.startswith(IDENTITY_PREFIXES)
            or path.startswith(PRESERVED_PREFIXES))


def blocked_reason(org: Organization | None, path: str) -> str | None:
    """The refusal reason, or None when access is permitted."""
    state = org_state(org)
    if state not in BLOCKING_STATES:
        return None
    if is_preserved_path(path):
        return None
    return state


def require_operational_org_access(request: Request,
                                   user: User | None = Depends(get_current_user_optional),
                                   db: Session = Depends(get_db)) -> User | None:
    """Refuse operational access while the caller's Organization is restricted.

    Applied as a router-level dependency on the organization and events routers. The
    response is deliberately explicit rather than a bare 403: a customer who cannot use
    their tenant needs to know it is a state, which state, and where to go — otherwise the
    restriction reads as an outage and generates a support ticket instead of a payment.
    """
    if user is None:
        # Public routes in these routers (invitation preview/accept, event registration,
        # watch) carry no session. An organization restriction has nothing to say about a
        # request that has not claimed to belong to one, so they pass through untouched.
        return None
    org = db.get(Organization, user.org_id) if user.org_id else None
    state = blocked_reason(org, request.url.path)
    if state is None:
        return user
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        {
            "code": "ORGANIZATION_RESTRICTED",
            "state": STATE_LABELS.get(state, state),
            "message": (
                f"This Organization is {STATE_LABELS.get(state, state)}. Billing, export, "
                f"privacy, security settings and support remain available."
            ),
        },
    )


def capability_summary(state: str) -> str:
    """What ORG-010 may truthfully say is affected, derived from what this module enforces.

    Kept next to the enforcement on purpose: if the allowlist above changes, the sentence
    the customer receives changes with it, instead of drifting into a claim nobody rechecks.
    """
    if state == ORG_STATE_RESTRICTED:
        return ("Operational access to this Organization is refused; billing, export, "
                "privacy, security settings and support remain available")
    if state == ORG_STATE_SUSPENDED:
        return ("Operational access to this Organization is refused; billing, export, "
                "privacy, security settings and support remain available")
    if state == ORG_STATE_DELETED:
        return "Organization access has ended"
    return "No restrictions apply"


def preserved_summary(state: str) -> str:
    if state == ORG_STATE_DELETED:
        return "Contact Zoiko Steam Support for billing, export and privacy records"
    return ("Billing, export, privacy, account security and support routes remain "
            "available")
