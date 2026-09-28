"""Background health probes for the providers the request path must not call.

── WHY THESE ARE BACKGROUND, NOT INLINE ─────────────────────────────────────────────────
services/admin.platform_health runs inside /admin/console-state, which every admin page polls
every 30s, and inside the organization console too. A network round trip to LiveKit, GCS and
Resend there would put three third-party latencies on every page load. So that function reports
configured-but-unprobed providers as `unmonitored` - honestly - and THIS module is what can
move them to a real verdict.

It runs on the existing leader-elected metric sampler (services/ops.run_metric_sampler), so there
is no new scheduler, and it probes at most once per PROBE_INTERVAL rather than every tick.

── WHERE RESULTS LIVE ───────────────────────────────────────────────────────────────────
Tickers run on the elected leader only, but platform_health is read by every process. An
in-memory cache would therefore be invisible to every follower, which is most of them, and they
would keep reporting a stale or absent verdict. The result is written to one platform_settings
row (PROBE_KEY) that every process reads. That key is not in routers/admin.EDITABLE_SETTING_KEYS,
so it cannot be forged through the settings endpoint.

── WHAT IS RECORDED ─────────────────────────────────────────────────────────────────────
Per provider: status, checked_at, latency_ms and an error CATEGORY - an exception class name or
an HTTP status. Never a message body, URL, bucket path or credential: provider errors routinely
embed all of those.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from ..config import settings

log = logging.getLogger(__name__)

PROBE_KEY = "provider_health_probes"
PROBE_INTERVAL = 300          # seconds between probe rounds
PROBE_TIMEOUT = 5.0           # per provider
# A result older than this is not evidence of current health. Three missed rounds.
STALE_AFTER = timedelta(seconds=PROBE_INTERVAL * 3)

_last_round = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _result(status: str, started: float, error: str | None = None) -> dict:
    return {"status": status, "checked_at": _now().isoformat(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error}


async def _probe_livekit() -> dict:
    from livekit import api

    t = time.perf_counter()
    client = api.LiveKitAPI(settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY,
                            settings.LIVEKIT_API_SECRET)
    try:
        # A name that cannot exist: an authenticated round trip that returns nothing, rather
        # than listing every live room just to learn the server is up.
        await asyncio.wait_for(
            client.room.list_rooms(api.ListRoomsRequest(names=["__zoiko_health_probe__"])),
            PROBE_TIMEOUT)
        return _result("ok", t)
    except asyncio.TimeoutError:
        return _result("unknown", t, "timeout")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not propagate
        return _result("down", t, type(exc).__name__)
    finally:
        await client.aclose()


def _probe_gcs_sync() -> dict:
    from . import livekit as lk

    t = time.perf_counter()
    client = lk._gcs_client()
    if client is None:
        # Configured on paper but no usable credential - which is itself the finding.
        return _result("down", t, "no_usable_credentials")
    try:
        exists = client.bucket(settings.GCS_BUCKET).exists(timeout=PROBE_TIMEOUT)
        return _result("ok" if exists else "down", t, None if exists else "bucket_not_found")
    except Exception as exc:  # noqa: BLE001
        return _result("unknown", t, type(exc).__name__)


async def _probe_resend() -> dict:
    import httpx

    t = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get("https://api.resend.com/domains",
                                    headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
    except httpx.TimeoutException:
        return _result("unknown", t, "timeout")
    except Exception as exc:  # noqa: BLE001
        return _result("unknown", t, type(exc).__name__)
    if resp.status_code == 200:
        return _result("ok", t)
    if resp.status_code in (401, 403):
        return _result("down", t, f"http_{resp.status_code}_credential_rejected")
    return _result("warn", t, f"http_{resp.status_code}")


def configured_providers() -> dict[str, bool]:
    from . import livekit as lk
    return {
        "streaming": bool(settings.LIVEKIT_URL and settings.LIVEKIT_API_KEY
                          and settings.LIVEKIT_API_SECRET),
        "storage": lk.gcs_configured(),
        "email": bool(settings.RESEND_API_KEY),
    }


async def probe_round(session_factory) -> dict | None:
    """Probe every CONFIGURED provider concurrently and persist the results.

    Returns None when throttled. An unconfigured provider is not probed and not recorded -
    platform_health already reports it as not_configured from settings.
    """
    global _last_round
    if time.monotonic() - _last_round < PROBE_INTERVAL:
        return None
    _last_round = time.monotonic()

    configured = configured_providers()
    tasks = {}
    if configured["streaming"]:
        tasks["streaming"] = _probe_livekit()
    if configured["storage"]:
        tasks["storage"] = asyncio.to_thread(_probe_gcs_sync)
    if configured["email"]:
        tasks["email"] = _probe_resend()
    if not tasks:
        return {}
    done = await asyncio.gather(*tasks.values(), return_exceptions=True)
    results = {}
    for name, outcome in zip(tasks, done):
        if isinstance(outcome, BaseException):
            results[name] = {"status": "unknown", "checked_at": _now().isoformat(),
                             "latency_ms": None, "error": type(outcome).__name__}
        else:
            results[name] = outcome
    await asyncio.to_thread(_persist, session_factory, results)
    return results


def _persist(session_factory, results: dict) -> None:
    from ..models import PlatformSetting

    db = session_factory()
    try:
        row = db.get(PlatformSetting, PROBE_KEY)
        merged = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
        merged.update(results)
        if row is None:
            db.add(PlatformSetting(key=PROBE_KEY, value=merged, category="health",
                                   updated_by="provider_health"))
        else:
            row.value = merged
            row.updated_by = "provider_health"
        db.commit()
    except Exception:  # noqa: BLE001 - a failed write must not kill the sampler
        db.rollback()
        log.exception("provider health probe result could not be stored")
    finally:
        db.close()


def latest(db, provider: str) -> dict | None:
    """The stored result for one provider, or None if absent or STALE.

    Stale is treated as absent on purpose: a probe that last succeeded an hour ago is not
    evidence the provider is healthy now, and reporting it would be the fabricated green this
    whole mechanism exists to replace.
    """
    from ..models import PlatformSetting

    try:
        row = db.get(PlatformSetting, PROBE_KEY)
    except Exception:  # noqa: BLE001
        return None
    data = row.value if row and isinstance(row.value, dict) else {}
    entry = data.get(provider)
    if not isinstance(entry, dict):
        return None
    try:
        checked = datetime.fromisoformat(entry["checked_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    if _now() - checked > STALE_AFTER:
        return None
    return entry
