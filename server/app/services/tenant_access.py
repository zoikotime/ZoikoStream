"""Tenant-data authorization boundary for platform staff (ZST-EC-001 ORG-009).

Before this module, `require_super_admin` was the only thing standing between a platform
account and every tenant's data: 31 admin routes trusted the role alone, and the support
approval workflow was a parallel record nobody had to use. This is the missing half — the
place where a customer's approval actually decides whether staff may read or change their
data.

    platform admin  ->  approved, ACTIVE, unexpired SupportAccessRequest
                        for THIS organization
                        held by THIS engineer
                        carrying THIS capability
                    ->  access, and one audit row per action

Two things are deliberately NOT behind this gate:

  * platform-global operations that expose no tenant data (feature flags, releases,
    incidents, platform settings, plan catalogue, aggregate analytics)
  * adverse platform enforcement — suspending, restricting or deleting an organization.
    Requiring the customer's approval to suspend the customer would make the control
    meaningless for non-payment or abuse. Those actions stay super-admin-only, are audited,
    and are announced through ORG-010 rather than approved through ORG-009.

`authorize()` is called explicitly by each route rather than wired as a bare dependency.
A dependency can be omitted silently; an explicit call that RETURNS the thing the handler
needs cannot be skipped without the handler having nothing to work with, and
test_tenant_access asserts every tenant-data route reaches it.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import SupportAccessRequest, User
from ..security import require_super_admin
from . import support_access as support_svc

log = logging.getLogger(__name__)

# ── capability vocabulary ───────────────────────────────────────────────────────────────
#
# `allowed_actions` on a support request was free text, which cannot be enforced: a route
# has no way to decide whether "look at their stuff" covers minting an API key. These are
# the capabilities a customer can actually approve, and the only strings a route checks.

CAP_TENANT_READ = "tenant.read"                     # organization + member records
CAP_MEMBERS_WRITE = "tenant.members.write"          # modify or remove a tenant's members
CAP_EVENTS_READ = "tenant.events.read"
CAP_EVENTS_WRITE = "tenant.events.write"            # destructive on tenant content
CAP_RECORDINGS_READ = "tenant.recordings.read"      # playback of customer media
CAP_CREDENTIALS_READ = "tenant.credentials.read"    # API key metadata
CAP_CREDENTIALS_WRITE = "tenant.credentials.write"  # mint or revoke tenant credentials
CAP_BILLING_WRITE = "tenant.billing.write"

CAPABILITIES = {
    CAP_TENANT_READ: "Read Organization and member records",
    CAP_MEMBERS_WRITE: "Modify or remove Organization members",
    CAP_EVENTS_READ: "Read event records",
    CAP_EVENTS_WRITE: "Modify or delete events",
    CAP_RECORDINGS_READ: "Open recording playback",
    CAP_CREDENTIALS_READ: "List API credentials",
    CAP_CREDENTIALS_WRITE: "Create or revoke API credentials",
    CAP_BILLING_WRITE: "Change subscription state",
}

# Capabilities that are never granted by a blanket read approval - each has to be named
# explicitly by the customer, because each either exposes customer content or mints
# something that outlives the session.
SENSITIVE_CAPABILITIES = frozenset({
    CAP_RECORDINGS_READ, CAP_CREDENTIALS_READ, CAP_CREDENTIALS_WRITE,
    CAP_MEMBERS_WRITE, CAP_EVENTS_WRITE, CAP_BILLING_WRITE,
})


def normalize_capabilities(raw: list[str]) -> list[str]:
    """Keep only recognized capabilities, deduplicated, order preserved."""
    seen, out = set(), []
    for item in raw or []:
        key = (item or "").strip().lower()
        if key in CAPABILITIES and key not in seen:
            seen.add(key)
            out.append(key)
    return out


class TenantAccessDenied(HTTPException):
    """403 with a reason the customer's own audit trail can carry."""

    def __init__(self, detail: str, reason: str):
        super().__init__(status.HTTP_403_FORBIDDEN, detail)
        self.reason = reason


class SupportContext:
    """Per-request handle a staff route uses to reach tenant data."""

    def __init__(self, db: Session, admin: User, request: Request):
        self.db, self.admin, self.request = db, admin, request
        self._session: SupportAccessRequest | None = None

    @property
    def client_ip(self) -> str | None:
        return self.request.client.host if self.request and self.request.client else None

    def session_for(self, org_id) -> SupportAccessRequest | None:
        """The live approved session for this organization held by THIS engineer."""
        req = support_svc.active_for(self.db, org_id)
        if req is None:
            return None
        if not support_svc.session_is_live(self.db, req):
            return None
        # The approval named an engineer. Another staff account riding a colleague's
        # approved session is exactly the attribution failure ORG-009 exists to close.
        if req.engineer_id != self.admin.id:
            return None
        return req

    def authorize(self, org_id, capability: str, *, resource: str,
                  resource_id=None, summary: str | None = None) -> SupportAccessRequest:
        """Gate one tenant-data operation, and record it. Raises 403 unless permitted.

        Returns the session so the caller can reference it; the audit row is written here
        so attribution cannot depend on a route remembering to log.
        """
        if capability not in CAPABILITIES:
            # A typo must fail closed, not open.
            log.error("Unknown tenant capability %r requested by %s", capability,
                      self.admin.id)
            raise TenantAccessDenied("Unsupported support capability",
                                     reason="unknown_capability")
        if org_id is None:
            raise TenantAccessDenied("Target Organization could not be resolved",
                                     reason="no_target_org")

        session = self.session_for(org_id)
        if session is None:
            raise TenantAccessDenied(
                "This Organization has not approved an active support session for you. "
                "Request access through /admin/support-access.",
                reason="no_active_approved_session",
            )

        approved = set(support_svc.actions_list(session.allowed_actions))
        if capability not in approved:
            # In scope for the session but not for this action.
            support_svc.record_action(
                self.db, session, actor=self.admin,
                action="support_access.denied_out_of_scope",
                target_type=resource, target_id=resource_id,
                meta={"capability": capability, "approved": sorted(approved)},
                ip=self.client_ip,
            )
            raise TenantAccessDenied(
                f"The approved support session does not permit '{capability}'.",
                reason="capability_not_approved",
            )

        support_svc.record_action(
            self.db, session, actor=self.admin,
            action=f"support_access.{capability}",
            target_type=resource, target_id=resource_id,
            meta={"capability": capability, "summary": summary,
                  "emergency": bool(session.emergency)},
            ip=self.client_ip,
        )
        self._session = session
        return session


def support_context(request: Request, db: Session = Depends(get_db),
                    admin: User = Depends(require_super_admin)) -> SupportContext:
    """Dependency for staff routes that touch tenant data."""
    return SupportContext(db, admin, request)
