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


def _maintenance_hours(db: Session, field: str) -> int | None:
    """A maintenance window in hours from the "maintenance_windows" setting, or None.

    None means "this environment has not configured that window", and services/maintenance.py
    reports the job as skipped rather than assuming one. A sweep interval is operational
    policy — how long an abandoned checkout may sit before someone looks at it is a business
    decision, not a number this module gets to pick (doc Section 26).
    """
    row = db.get(PlatformSetting, "maintenance_windows")
    value = (row.value or {}).get(field) if row else None
    if value is None:
        return None
    hours = int(value)
    return hours if hours > 0 else None


def stale_payment_window_hours(db: Session) -> int | None:
    """How long a payment may sit un-settled before it is reported as stale."""
    return _maintenance_hours(db, "stale_payment_hours")


def unmatched_settlement_alert_hours(db: Session) -> int | None:
    """How long unattributed provider money may sit before it is alerted on (doc P5)."""
    return _maintenance_hours(db, "unmatched_settlement_hours")


def require_media_plane(db: Session) -> bool:
    """Whether readiness should refuse a commercial event when no media plane is configured.

    OFF by default, and that is deliberate rather than lazy. A commercial event with no LiveKit
    credentials genuinely cannot deliver, so enforcing it is correct in production — but making
    it unconditional would make the readiness verdict depend on ambient environment variables,
    and dev/CI legitimately run with no media plane (see test_livekit_identity.py). An
    always-on check would turn every go-live test red everywhere except a fully-provisioned
    deployment.

    So it is an explicit Operations switch, same shape as audience_capacity_envelope: unset
    means "this environment does not assert media-plane readiness", set means enforce it.
    Turning it on is the production hardening step; the switch itself invents nothing.

    Stored on "streaming_limits" alongside the other delivery-side controls.
    """
    row = db.get(PlatformSetting, "streaming_limits")
    return bool((row.value or {}).get("require_media_plane")) if row else False


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
