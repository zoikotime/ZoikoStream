"""Dashboard summary endpoints for the organization and platform landing pages.

Authorization uses the shared dependencies from security.py (require_min_role /
require_super_admin) rather than hand-rolled role comparisons — one authorization
implementation, and a denial is a real 403 instead of a 200 carrying an {"error": ...}
body that a status-checking client reads as success.

Every number here is a query. The viewer counts previously shipped as hardcoded constants
(12530 org / 84120 platform / 3 live events); they now sum BroadcastSession.peak_viewers,
which the analytics sampler writes from real presence records.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BroadcastSession, Event, Organization, User
from ..security import require_min_role, require_super_admin
# Excluded from customer-facing counts — it only holds the super admin. Imported rather
# than re-declared so the two dashboards can't disagree about what a "customer" org is.
from ..services.admin import PLATFORM_ORG_NAME

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/org/stats")
def get_org_stats(
    user: User = Depends(require_min_role("org_admin")),
    db: Session = Depends(get_db),
):
    """Organization-scoped dashboard statistics. Always the caller's own organization —
    org_id comes from the JWT, never from the request."""
    org = db.get(Organization, user.org_id)
    if org is None:
        # An authenticated user whose org row vanished is a data-integrity problem, not a
        # client error; return a valid-but-empty payload rather than a 500.
        return {"organization_id": str(user.org_id), "organization_name": None, "total_users": 0}

    org_users = db.scalar(
        select(func.count(User.id)).where(User.org_id == user.org_id, User.deleted_at.is_(None))
    )

    # One grouped query instead of three COUNT round trips for the same table.
    event_counts = dict(
        db.execute(
            select(Event.status, func.count(Event.id))
            .where(Event.org_id == user.org_id, Event.deleted_at.is_(None))
            .group_by(Event.status)
        ).all()
    )

    return {
        "organization_id": str(user.org_id),
        "organization_name": org.name,
        "upcoming_events": event_counts.get("scheduled", 0),
        "live_events": event_counts.get("live", 0),
        "completed_events": event_counts.get("ended", 0) + event_counts.get("archived", 0),
        # Real peak audience across this org's broadcasts. BroadcastSession.peak_viewers is
        # written by the analytics sampler from actual presence records, so this is a
        # measurement — it replaces a hardcoded 12530 that shipped as if it were real.
        "total_viewers": db.scalar(
            select(func.coalesce(func.sum(BroadcastSession.peak_viewers), 0))
            .where(BroadcastSession.org_id == user.org_id)
        ) or 0,
        "total_users": org_users or 0,
    }


@router.get("/platform/stats")
def get_platform_stats(
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Platform-wide statistics for the super admin dashboard."""
    total_orgs = db.scalar(
        select(func.count(Organization.id)).where(Organization.name != PLATFORM_ORG_NAME)
    )
    total_users = db.scalar(select(func.count(User.id)))

    return {
        "organizations": total_orgs or 0,
        "total_users": total_users or 0,
        # Both were hardcoded placeholders. Counted from the same rows /admin/dashboard and
        # the Command Center use, so the two never disagree.
        "live_events": db.scalar(
            select(func.count(BroadcastSession.id)).where(BroadcastSession.status == "live")
        ) or 0,
        "total_viewers": db.scalar(
            select(func.coalesce(func.sum(BroadcastSession.peak_viewers), 0))
        ) or 0,
    }


@router.get("/platform/organizations")
def get_all_organizations(
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
    limit: int = 10,
):
    """Organizations with their member counts.

    The member count is a GROUP BY, not len(org.users): the relationship form issued one
    lazy SELECT per organization (N+1) for a number the database returns in the same round
    trip.
    """
    member_count = (
        select(User.org_id, func.count(User.id).label("n"))
        .where(User.deleted_at.is_(None))
        .group_by(User.org_id)
        .subquery()
    )
    rows = db.execute(
        select(Organization, func.coalesce(member_count.c.n, 0))
        .outerjoin(member_count, member_count.c.org_id == Organization.id)
        .where(Organization.name != PLATFORM_ORG_NAME)
        .order_by(Organization.created_at.desc())
        .limit(limit)
    ).all()

    return [
        {
            "id": str(o.id),
            "name": o.name,
            "users": int(n),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o, n in rows
    ]
