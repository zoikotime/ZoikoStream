"""
Dashboard endpoints for organization and platform stats.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Organization, User
from ..security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class OrgStats:
    """Organization-specific dashboard statistics."""
    upcoming_events: int = 0
    live_events: int = 0
    completed_events: int = 0
    total_viewers: int = 0


class PlatformStats:
    """Platform-wide dashboard statistics."""
    organizations: int = 0
    total_users: int = 0
    live_events: int = 0
    total_viewers: int = 0


@router.get("/org/stats")
def get_org_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get organization-specific statistics for org_admin dashboard.
    Only return stats for the user's own organization.
    """
    if user.role not in ["org_admin", "super_admin"]:
        return {
            "error": "Unauthorized",
            "message": "Only admins can access dashboard stats"
        }
    
    # Get user's organization
    org = db.get(Organization, user.org_id)
    if not org:
        return {"error": "Organization not found"}
    
    # Count organization stats
    org_users = db.scalar(
        select(func.count(User.id)).where(User.org_id == user.org_id)
    )
    
    return {
        "organization_id": str(user.org_id),
        "organization_name": org.name,
        "upcoming_events": 5,  # ponytail: fetch from events table when it exists
        "live_events": 1,
        "completed_events": 28,
        "total_viewers": 12530,
        "total_users": org_users or 0,
    }


@router.get("/platform/stats")
def get_platform_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get platform-wide statistics for super_admin dashboard."""
    if user.role != "super_admin":
        return {"error": "Unauthorized - only super_admin can access"}
    
    total_orgs = db.scalar(select(func.count(Organization.id)).where(Organization.name != "ZoikoStream Platform"))
    total_users = db.scalar(select(func.count(User.id)))
    
    return {
        "organizations": total_orgs or 0,
        "total_users": total_users or 0,
        "live_events": 3,
        "total_viewers": 84120,
    }


@router.get("/platform/organizations")
def get_all_organizations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 10,
):
    """Get all organizations with user counts for super_admin."""
    if user.role != "super_admin":
        return {"error": "Unauthorized"}
    
    orgs = db.query(Organization).filter(Organization.name != "ZoikoStream Platform").limit(limit).all()
    return [
        {
            "id": str(o.id),
            "name": o.name,
            "users": len(o.users),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orgs
    ]
