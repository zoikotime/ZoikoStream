"""Policy Adapter (ZST-UX-GUIDE-001 §9, §11.1): who may use Guide, which tenant data a request
may touch, which capabilities are on. Server-side and deterministic — the model is never asked
whether access should be granted.

Platform rules are REUSED, not re-implemented: the role ladder comes from app.security and
event edit rights from the events router's own `_can_edit`, so Guide can never disagree with
the platform about who may do what.

Tenant isolation is stricter than the platform's: `org_scoped` lets a super_admin read every
organization, but Guide pins every read to the caller's OWN organization. Cross-tenant support
work keeps going through the audited Support Access workflow, never through chat (§16).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.admin import get_feature_flag_by_key
from app.models.event import Event
from app.models.organization import Organization
from app.models.user import User
from app.security import _ROLE_RANK

from . import errors
from .config import get_settings

POLICY_VERSION = "opguide-policy-1.0"
FEATURE_FLAG_KEY = "operator_guide"

# Guide is for people who run events (§9). Speakers use the contributor surfaces; viewers
# use the public Guide; billing_admin is outside the platform ladder entirely.
GUIDE_ROLES = ("host", "org_admin", "super_admin")

# §6.17 capability switches an organization admin controls.
CAPABILITIES = {
    "docs_qa": "Documentation Q&A",
    "event_analysis": "Event-state analysis",
    "analytics": "Analytics explanation",
    "drafts": "Draft configuration",
    "actions": "Approved low-risk actions",
}
# Fixed governance rows shown to admins; not switchable (§6.17).
FIXED_GOVERNANCE = {
    "audit": "Enabled",
    "write_confirmation": "Required",
    "high_risk_via_guide": "Blocked",
}


def rank(user: User) -> int:
    return _ROLE_RANK.get(user.role, -1)


def is_org_admin(user: User) -> bool:
    return rank(user) >= _ROLE_RANK["org_admin"]


@dataclass(frozen=True)
class Actor:
    """The authenticated principal Guide acts for — exactly the platform request's identity."""
    user: User
    org: Organization
    token: str               # the caller's own bearer token; used only to call platform APIs

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def role(self) -> str:
        return self.user.role


def guide_available(db: Session) -> bool:
    """Global switches: deployment env + Super Admin feature flag (absent row = on)."""
    if not get_settings().OPGUIDE_ENABLED:
        return False
    flag = get_feature_flag_by_key(db, FEATURE_FLAG_KEY)
    return flag.enabled if flag else True


def require_actor(db: Session, user: User, token: str, *, allow_org_disabled: bool = False) -> Actor:
    """The Guide principal for this request. ``allow_org_disabled`` is used only by the
    governance endpoints, so an organization admin can turn Guide back on."""
    if user.role not in GUIDE_ROLES:
        raise errors.Forbidden("Guide is available to event hosts and organization admins.")
    if not guide_available(db):
        raise errors.Unavailable(errors.GUIDE_UNAVAILABLE)
    org = db.get(Organization, user.org_id) if user.org_id else None
    if org is None:
        raise errors.Forbidden("Guide needs an organization context.")
    from . import governance  # local: governance imports policy
    if not allow_org_disabled and not governance.settings_for(db, org.id)["enabled"]:
        raise errors.Unavailable("Guide is turned off for this organization. An organization "
                                 "admin can turn it on in Guide settings.")
    return Actor(user=user, org=org, token=token)


def require_capability(db: Session, actor: Actor, capability: str) -> None:
    from . import governance
    if not governance.settings_for(db, actor.org_id)["capabilities"].get(capability, True):
        raise errors.EntitlementBlocked(
            f"{CAPABILITIES[capability]} is turned off for this organization in Guide "
            "settings. An organization admin can change this.")


def resolve_event(db: Session, actor: Actor, event_id) -> Event:
    """The event, if and only if it belongs to the caller's organization and is not deleted.
    Unknown, deleted and other-tenant events are indistinguishable (no existence oracle)."""
    try:
        eid = uuid.UUID(str(event_id))
    except (TypeError, ValueError):
        raise errors.NotFound("I can't find that event in your organization.") from None
    ev = db.scalar(select(Event).where(Event.id == eid, Event.deleted_at.is_(None),
                                       Event.org_id == actor.org_id))
    if ev is None:
        raise errors.NotFound("I can't find that event in your organization.")
    return ev


def can_edit_event(db: Session, actor: Actor, event: Event) -> bool:
    """The platform's own edit rule (events router), so Guide never disagrees with it."""
    from app.routers.events import _can_edit
    return bool(_can_edit(db, event, actor.user))


def permission_label(actor: Actor, allowed: bool, needs: str) -> dict:
    return {"allowed": allowed, "needs": needs, "role": actor.role}
