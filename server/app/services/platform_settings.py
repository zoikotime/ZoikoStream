"""Read-side helpers for PlatformSetting (models/platform_setting.py) — the key/value store
the Super Admin Settings page writes through GET/PATCH /admin/settings. Split out from
crud/admin.py because callers outside the admin API (the maintenance-mode middleware in
main.py, the signup gate in routers/auth.py) need to read a setting without importing the
admin CRUD module."""

from sqlalchemy.orm import Session

from ..models import PlatformSetting

# Key "global" holds the two platform-wide switches Settings.jsx exposes:
#   signups_enabled  — POST /auth/register refuses new accounts when False.
#   maintenance_mode — the organization/host console (see main.py's maintenance_gate
#                       middleware) refuses non-super-admin requests when True.
_GLOBAL_KEY = "global"
_GLOBAL_DEFAULTS = {"signups_enabled": True, "maintenance_mode": False}


def get_global_settings(db: Session) -> dict:
    row = db.get(PlatformSetting, _GLOBAL_KEY)
    return {**_GLOBAL_DEFAULTS, **(row.value or {})} if row else dict(_GLOBAL_DEFAULTS)


def signups_enabled(db: Session) -> bool:
    return bool(get_global_settings(db)["signups_enabled"])


def maintenance_mode(db: Session) -> bool:
    return bool(get_global_settings(db)["maintenance_mode"])


# The two unambiguous ceilings in the "Storage & Streaming Limits" panel — platform-wide
# hard caps on top of whatever an org's own Subscription/Plan allows (services/org.py's
# plan-based max_storage_gb). Only tightens: an org already within its plan's own limit is
# never blocked by these unless the platform ceiling is set lower than the plan.
# (default_gb/default_hours are new-organization provisioning defaults, not enforcement —
# left alone this pass; see the Settings.jsx panel copy.)
def storage_ceiling_gb(db: Session) -> float | None:
    row = db.get(PlatformSetting, "storage_limits")
    value = (row.value or {}).get("max_gb") if row else None
    return float(value) if value is not None else None


def max_bitrate_kbps(db: Session) -> int | None:
    row = db.get(PlatformSetting, "streaming_limits")
    value = (row.value or {}).get("max_bitrate_kbps") if row else None
    return int(value) if value is not None else None


def audience_capacity_envelope(db: Session) -> int | None:
    """Approved platform-wide audience qualification band — the peak concurrent-viewer count
    an event may expect WITHOUT an explicit, hard-reserved capacity commitment.

    Returns None when Operations has not published a band. None is not a licence to assume
    one: crud/commercial.py's readiness gate fails closed and requires an approved capacity
    reservation for any event that states an expected audience at all (ZST-LE-COM-001 C4
    "the system must fail closed when capacity is unavailable", and Section 26's "no
    hard-coded fallback ... exists"). This replaced a hard-coded DEFAULT_CAPACITY_ENVELOPE
    constant, which silently qualified every event under an unapproved number.

    Stored on the existing "streaming_limits" setting rather than a new row, alongside the
    other delivery-side ceilings the Settings console already owns. Deliberately NOT seeded
    with a value (see seed.py DEFAULT_SETTINGS) — seeding one would re-invent the constant.
    """
    row = db.get(PlatformSetting, "streaming_limits")
    value = (row.value or {}).get("audience_envelope") if row else None
    return int(value) if value is not None else None
