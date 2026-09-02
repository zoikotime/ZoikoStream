import asyncio
import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jose import JWTError, jwt
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from .routers.auth import router as auth_router
from .routers.dashboard import router as dashboard_router
from .routers.admin import router as admin_router
from .routers.organization import router as organization_router
from .routers.events import router as events_router
from .routers.live import router as live_router
from .routers.commercial import router as commercial_router
from .routers.deliveries import router as deliveries_router
from .routers.contact import router as contact_router
from .security import ALGORITHM
from .services import bus
from .services import platform_settings
from .services import livekit
from .services.broadcast import run_sampler
from .services.moderation import run_scheduler
from .services.org_comms import run_invitation_reminders
from .services.credential_lifecycle import run_credential_sweeper
from .services.org_state import require_operational_org_access
from .services.media_comms import run_media_sweeper
from .services.media_retention import run_retention_sweeper
from .services.signing_rotation import run_signing_rotation_sweeper
from .services.org_governance import run_governance_sweeper
from .services.support_access import run_support_access_sweeper
from .services.ops import request_stats, run_metric_sampler
from .services.webhooks import run_webhook_retries
from .services.delivery import run_watermark_processor
from .services.validation import run_validation_processor
from .config import settings
from .db import (
    DB_MAX_CONNECTIONS, SessionLocal, holds_ticker_leadership,
    release_ticker_leadership, try_acquire_ticker_leadership,
)

log = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Thirteen background tickers, each owning its own domain (which is also what keeps
    moderation and broadcast from having to import each other):
      * scheduler  — fires scheduled polls/announcements, closes timed-out polls
      * sampler    — writes analytics snapshots (the retention graph) and pushes live counters
      * metrics    — writes platform metric samples (the admin console's KPI sparklines)
      * webhooks   — sends/retries pending webhook deliveries (services/webhooks.py)
      * watermark  — burns the policy watermark into pending customer exports (services/delivery.py)
      * validation — compares a dual-recording pair and advances its replay entitlement
                     once real evidence exists (services/validation.py)
      * invites    — sends the ZST-EC-001 ORG-001 Reminder for invitations nearing expiry
                     (services/org_comms.py)
      * support    — warns on and closes expiring authorized support sessions, so ORG-009
                     access is time-bound by the platform rather than by trust
                     (services/support_access.py)
      * governance — access-review reminders/overdue and ownership-transfer expiry
                     (services/org_governance.py)
      * credential — API-credential expiry warnings and dormancy (services/credential_lifecycle.py)
      * signing    — webhook signing-secret rotation overlap windows (services/signing_rotation.py)
      * media      — governed live-input signal transitions (services/media_comms.py)
      * retention  — recording retention warnings and replay-availability expiry, both
                     deadline-driven so neither has a request or webhook that could carry
                     them (services/media_retention.py)
    The bus releases its Redis client on the way out.

    All thirteen run in the LEADER process only, elected by a Postgres advisory lock (see
    db.try_acquire_ticker_leadership). They previously ran in every process, so a deployment
    with more than one instance fired each scheduled poll, webhook delivery and watermark burn
    once per instance. A follower serves HTTP normally and simply runs no tickers; it retries
    election periodically, so a leader that dies is replaced without operator action.

    The default-executor swap is the other half of the DB pool sizing in db.py: every
    socket action reaches Postgres via services.moderation.tx() -> asyncio.to_thread, which
    uses this executor. Left at its default (min(32, cpu+4)) a busy event could park more
    threads on connection checkout than the pool can ever satisfy. Bounding it to the pool
    ceiling makes the queue form in the executor, where it is visible, instead of inside
    SQLAlchemy's checkout timeout."""
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=DB_MAX_CONNECTIONS, thread_name_prefix="zoiko-db")
    loop.set_default_executor(executor)

    # Same "loudly flagged at startup" posture as config.py's SECRET_KEY check: a bad GCS
    # credential (e.g. a Console URL instead of a downloaded service-account key file — the
    # exact misconfiguration the live-streaming audit found in this deployment's .env)
    # otherwise only ever shows up as a silent "not captured" recording, discovered by a host
    # long after the fact. Recording is optional (many events run with none configured at
    # all), so this warns rather than refusing to boot. Runs in every process (leader or
    # follower) — unlike the tickers below, this is a per-process config check, not a job
    # that would duplicate if it ran more than once.
    gcs_error = livekit.gcs_config_error()
    if gcs_error:
        log.warning("Recording uploads will not work: %s", gcs_error)

    supervisor = asyncio.create_task(_ticker_supervisor())
    try:
        yield
    finally:
        supervisor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor
        await bus.shutdown()
        release_ticker_leadership()
        executor.shutdown(wait=False, cancel_futures=True)


# How often a follower retries election, and how often the leader re-verifies it still holds
# the lock. One interval of duplicate-free downtime after a leader dies is an acceptable price
# for never running a ticker twice; the alternative (a shorter poll) is a lock round-trip per
# instance per few seconds for no operational gain.
TICKER_ELECTION_INTERVAL = 30.0


def _start_tickers() -> list:
    return [
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_sampler()),
        asyncio.create_task(run_metric_sampler()),
        asyncio.create_task(run_webhook_retries()),
        asyncio.create_task(run_watermark_processor()),
        asyncio.create_task(run_validation_processor()),
        # ZST-EC-001 communication tickers. Every one of these sends email, so running them
        # in more than one process would mail the same person twice - the duplicate-work
        # problem leader election exists to solve, in its most visible form.
        asyncio.create_task(run_invitation_reminders()),
        asyncio.create_task(run_support_access_sweeper()),
        asyncio.create_task(run_governance_sweeper()),
        asyncio.create_task(run_credential_sweeper()),
        asyncio.create_task(run_signing_rotation_sweeper()),
        asyncio.create_task(run_media_sweeper()),
        # MED-009 / MED-011. Retention warnings and replay expiry are both deadline-driven,
        # so neither has a request or a webhook that could carry it.
        asyncio.create_task(run_retention_sweeper()),
    ]


async def _stop_tickers(tasks: list) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _ticker_supervisor() -> None:
    """Run the tickers only while this process is the elected leader.

    One supervisor gates all thirteen rather than each ticker checking for itself: the invariant is
    "these jobs run in one process", so it belongs in one place. Thirteen independent checks
    would be thirteen chances for one of them to drift out of step.

    If leadership is LOST mid-flight (the lock's connection dropped) the tickers are cancelled
    immediately, because by then a follower may already have been promoted. Stopping is always
    safe — every ticker is a periodic sweep that picks up where it left off — whereas
    continuing would mean two processes doing the same work.
    """
    tasks: list = []
    try:
        while True:
            leader = holds_ticker_leadership()
            if not leader:
                try:
                    leader = await asyncio.to_thread(try_acquire_ticker_leadership)
                except Exception as exc:
                    # A database blip must not kill the supervisor: without it this process
                    # could never become leader again, and if it is the only instance the
                    # tickers would stay dead until a restart.
                    log.warning("ticker election failed, retrying: %s", exc)
                    leader = False

            if leader and not tasks:
                tasks = _start_tickers()
                log.info("background tickers started (this process is the ticker leader)")
            elif not leader and tasks:
                await _stop_tickers(tasks)
                tasks = []
                log.warning("ticker leadership lost — background tickers stopped")

            await asyncio.sleep(TICKER_ELECTION_INTERVAL)
    except asyncio.CancelledError:
        await _stop_tickers(tasks)
        raise


# ponytail: schema is applied by `python create_tables.py`, not on startup — an app boot
# should not be able to mutate the database.
app = FastAPI(title="ZoikoStream API", lifespan=lifespan)

# CORS. The localhost escape hatch exists because Vite bumps to 5175+ when 5173/5174 are
# taken, and an origin outside the allowlist silently kills login (CORS-blocked) rather than
# erroring usefully.
#
# It is now GATED ON ENVIRONMENT. Previously the regex applied unconditionally, so a production
# deployment permanently accepted every `http://localhost:*` origin WITH allow_credentials —
# meaning any page served from the victim's own machine could make credentialed cross-origin
# calls against the live API and read the responses. In production the configured
# CORS_ORIGINS allowlist is the only thing honoured.
_CORS_KWARGS = {
    "allow_origins": [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if not settings.is_production():
    _CORS_KWARGS["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

app.add_middleware(CORSMiddleware, **_CORS_KWARGS)

@app.middleware("http")
async def measure_requests(request: Request, call_next):
    """Feeds services.ops.request_stats so the admin console's API-health tile reports this
    process's real error rate and p95 latency instead of a placeholder. A failed request
    still gets recorded (as a 500) before the exception continues to the handlers below."""
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        request_stats.record((time.perf_counter() - start) * 1000, 500)
        raise
    request_stats.record((time.perf_counter() - start) * 1000, response.status_code)
    return response


# The management-console surfaces this gate protects: the org/host self-service console
# (create events, manage members, billing) and its legacy dashboard alias. Scoped
# deliberately narrow rather than "every /api/ route" — a sweeping gate risks silently
# blocking something viewer- or webhook-facing that a live broadcast depends on: public
# event watch/registration pages (/api/events), the live WebSocket + LiveKit's signed
# webhook receiver (/api/live — the WebSocket scope is "websocket" not "http" anyway, so
# @app.middleware("http") never sees it), login (/api/auth — a super admin must still be
# able to sign in to turn maintenance back off; POST /auth/register has its own independent
# signups_enabled gate, not tied to maintenance), and the admin console itself (/api/admin,
# already self-gated to super_admin by the router's own dependency).
_MAINTENANCE_GATED_PREFIXES = ("/api/organization", "/api/dashboard")


def _bearer_role(request: Request) -> str | None:
    """Best-effort role lookup straight from the JWT, without a DB round trip: middleware
    runs before route dependencies, so there is no `User` from get_current_user yet, and a
    gate that fires on almost every request should not add a second query on top of the one
    below. The role claim is the same one create_access_token signs (routers/auth.py) — good
    enough to answer "is this caller a super admin", which is all this check needs."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth[7:], settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    return payload.get("role")


@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    path = request.url.path
    if not path.startswith(_MAINTENANCE_GATED_PREFIXES):
        return await call_next(request)
    if _bearer_role(request) == "super_admin":
        return await call_next(request)

    def check():
        db = SessionLocal()
        try:
            return platform_settings.maintenance_mode(db)
        finally:
            db.close()

    if await asyncio.to_thread(check):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The platform is under maintenance. Please try again shortly."},
        )
    return await call_next(request)


# Everything lives under /api because the SPA is served from the same origin (see the mount
# at the bottom) and its client-side routes — /dashboard, /admin/*, /organization/* — are
# spelled exactly like the router prefixes. Without the namespace a hard refresh on any of
# those pages hits the API and gets JSON instead of the app.
# ZST-EC-001 ORG-010. Organization-scoped routers carry the operational-state gate, so a
# restricted or suspended tenant genuinely loses operational access instead of only being
# told it did. The dependency resolves the caller optionally, so the public routes in these
# routers (invitation preview/accept, event registration, watch) are unaffected, and it
# allows an explicit preserved allowlist — billing, export, privacy, security settings and
# support — so a restricted customer can still pay, retrieve their data or appeal.
#
# auth_router and admin_router are deliberately NOT gated: identity must keep working
# (IDN-008 owns identity restriction, not this), and platform staff must still be able to
# act on a restricted tenant.
#
# live_router is NOT gated here either, and that is a correctness requirement rather than an
# exemption from ORG-010. It carries the live WebSocket (routers/live.py::live_socket), and
# this dependency is HTTP-only: it takes a `Request` and resolves the caller through
# HTTPBearer. FastAPI cannot build either for a WebSocket scope, so applying it to that
# router made EVERY live socket fail dependency resolution with
# `TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'`,
# answered as HTTP 500 before the endpoint ran — the console never received a snapshot, so
# can_host never arrived and Go Live could not start at all.
#
# ORG-010 is still enforced on that socket. live_socket calls the same policy core this
# dependency wraps (services/org_state.blocked_reason) at connect time and closes with a
# policy-violation code, which is the only way a WebSocket can carry a refusal to the
# browser. The other route on live_router is the LiveKit webhook, which is server-to-server
# and carries no session, so the dependency was already a no-op there (user is None).
_ORG_STATE_GATE = [Depends(require_operational_org_access)]

for router in (auth_router, dashboard_router, admin_router, contact_router, live_router):
    app.include_router(router, prefix="/api")
for router in (organization_router, events_router, commercial_router,
               deliveries_router):
    app.include_router(router, prefix="/api", dependencies=_ORG_STATE_GATE)


# A DB outage (e.g. Supabase paused, DNS blip) raises OperationalError. Without this,
# it bubbles to Starlette's outermost error middleware as a 500 with NO CORS headers,
# so the browser blocks it and axios reports a cryptic "Network Error". Handling it here
# (inside CORSMiddleware) returns a clean 503 that keeps its CORS headers.
@app.exception_handler(OperationalError)
def db_unavailable(request: Request, exc: OperationalError):
    return JSONResponse(
        status_code=503,
        content={"detail": "Service temporarily unavailable - the database is unreachable. Please try again."},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


# Single-container deploy (see Dockerfile): the built SPA is served by this process, so the
# browser talks to one origin and CORS never enters the picture. Absent in dev (Vite serves
# it on 5173), which is why this is conditional.
# ponytail: the catch-all is registered LAST, so every router above wins; the cost is that an
# unknown /api-ish GET returns index.html instead of a JSON 404. That is standard SPA routing.
DIST = Path(__file__).resolve().parents[2] / "client" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        # An unmatched /api GET is a bug, not a page: answering it with index.html would hand
        # axios 200 + HTML and turn a typo'd endpoint into an unreadable parse error.
        if path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        file = DIST / path
        return FileResponse(file if file.is_file() else DIST / "index.html")
