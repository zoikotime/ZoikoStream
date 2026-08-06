import asyncio
import contextlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
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
from .services import bus
from .services.broadcast import run_sampler
from .services.moderation import run_scheduler
from .services.ops import request_stats, run_metric_sampler
from .config import settings
from .db import DB_MAX_CONNECTIONS


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    """Two background tickers, each owning its own domain (which is also what keeps
    moderation and broadcast from having to import each other):
      * scheduler — fires scheduled polls/announcements, closes timed-out polls
      * sampler   — writes analytics snapshots (the retention graph) and pushes live counters
      * metrics   — writes platform metric samples (the admin console's KPI sparklines)
    The bus releases its Redis client on the way out.
    ponytail: both run per PROCESS. With multiple workers, run them in one worker (or a cron
    worker) or a scheduled poll launches once per worker and snapshots are written N times.

    The default-executor swap is the other half of the DB pool sizing in db.py: every
    socket action reaches Postgres via services.moderation.tx() -> asyncio.to_thread, which
    uses this executor. Left at its default (min(32, cpu+4)) a busy event could park more
    threads on connection checkout than the pool can ever satisfy. Bounding it to the pool
    ceiling makes the queue form in the executor, where it is visible, instead of inside
    SQLAlchemy's checkout timeout."""
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=DB_MAX_CONNECTIONS, thread_name_prefix="zoiko-db")
    loop.set_default_executor(executor)

    tasks = [asyncio.create_task(run_scheduler()), asyncio.create_task(run_sampler()),
             asyncio.create_task(run_metric_sampler())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bus.shutdown()
        executor.shutdown(wait=False, cancel_futures=True)


# ponytail: schema is applied by `python create_tables.py`, not on startup — an app boot
# should not be able to mutate the database.
app = FastAPI(title="ZoikoStream API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    # ponytail: any localhost port — Vite bumps to 5175+ when 5173/5174 are taken,
    # and a port outside the allowlist silently kills login (CORS-blocked). Prod uses CORS_ORIGINS above.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


# Everything lives under /api because the SPA is served from the same origin (see the mount
# at the bottom) and its client-side routes — /dashboard, /admin/*, /organization/* — are
# spelled exactly like the router prefixes. Without the namespace a hard refresh on any of
# those pages hits the API and gets JSON instead of the app.
for router in (auth_router, dashboard_router, admin_router, organization_router,
               events_router, live_router):
    app.include_router(router, prefix="/api")


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
