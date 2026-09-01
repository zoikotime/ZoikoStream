"""Developer-platform communications (ZST-EC-001 DEV-002).

One credential creation produces one DEV-002 notification set, describing only safe
metadata. The claim that makes it exactly-once lives on the credential record itself
(`dev_002_notified_at`), so the identity of the credential is the idempotency key — a
retried request cannot mint a second security email for the same key.

Two things this module reports rather than dresses up, because both are true of the
platform today:

  * **Scopes do not exist.** API key records carry a label, a prefix, a hash and an expiry —
    there is no scope field anywhere in the schema. The email says so instead of printing
    "Full access", which would be a claim about authorization nobody implemented.

  * **The credential authenticates nothing yet.** `key_hash` is written at creation and
    never read: no route, dependency or middleware verifies an API key. Telling a security
    administrator that a new credential can reach their data would be false, so the access
    line states the real position.

Both are surfaced as gaps in the DEV-002 report rather than papered over here.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .. import email as email_mod
from ..crud import admin as admin_crud
from ..email import UnsafeLinkError
from ..models import Organization, User
from . import org_comms

log = logging.getLogger(__name__)

# Truthful stand-ins for capabilities this platform does not model. Kept as named constants
# so a future scope subsystem replaces one value rather than hunting through templates.
NO_SCOPES = "Not scoped — Zoiko Steam does not currently support scoped credentials"
NO_API_AUTH = (
    "No Zoiko Steam API endpoint currently accepts API-key authentication, so this "
    "credential grants no access yet"
)

# There is no separate developer/test environment in the product. The raw key carries a
# literal `zk_live_` banner, which is a string constant rather than authoritative state, so
# it is NOT treated as a mode signal — see the reported DEV-001 gap.
ENVIRONMENT_LABEL = "Production (Zoiko Steam has no separate test environment)"


def recipients(db: Session, org: Organization, creator: User | None) -> list[str]:
    """The creator plus the Organization's administrators, deduplicated.

    The canonical contract asks for "workspace security administrators". This platform has
    no workspace entity and no security-administrator role, so the closest authoritative
    equivalent is used — the Organization's recorded owner and its `org_admin` members —
    and the substitution is reported rather than implied.
    """
    owner = db.get(User, org.owner_user_id) if org and org.owner_user_id else None
    admins = org_comms.org_admins(db, org.id if org else None)
    return org_comms.recipients(creator, owner, *admins)


def notify_credential_created(db: Session, background, *, org: Organization,
                              creator: User | None, key_id: str) -> bool:
    """Queue DEV-002 for one committed credential. Returns True if this call sent it.

    Called only AFTER the credential row is committed — the email reports a credential that
    already exists, and a delivery failure can never unmake it.
    """
    record = admin_crud.get_api_key_record(org, key_id)
    if record is None:
        # Nothing committed, nothing to announce.
        log.warning("DEV-002 skipped: credential %s not found on org %s", key_id, org.id)
        return False

    if not admin_crud.claim_key_notification(db, org, key_id):
        return False                      # already announced for this credential

    addresses = recipients(db, org, creator)
    if not addresses:
        return False

    fingerprint = admin_crud.key_fingerprint(record)
    created_display = org_comms.org_timestamp(org, _as_datetime(record.get("created_at")))
    expires_raw = record.get("expires_at")
    expires_display = (org_comms.org_timestamp(org, _as_datetime(expires_raw))
                       if expires_raw else "Does not expire")

    try:
        for address in addresses:
            background.add_task(
                email_mod.send_api_credential_created_email, address,
                org_name=org.name,
                credential_name=record.get("label") or "Unnamed credential",
                fingerprint=fingerprint,
                created_at=created_display,
                creator=(creator.email if creator else "An authorized administrator"),
                scope_summary=NO_SCOPES,
                environment=ENVIRONMENT_LABEL,
                expires_display=expires_display,
                access_summary=NO_API_AUTH,
            )
    except UnsafeLinkError:
        log.exception("DEV-002 not queued: APP_URL unsafe for this environment")
        return False
    return True


def _as_datetime(value):
    """Records store ISO strings in a JSON column; the timestamp helper wants a datetime."""
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        return value
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
