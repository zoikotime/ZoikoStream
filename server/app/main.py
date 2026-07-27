import asyncio
import logging
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from .auth import router as auth_router
from .api.dashboard import router as dashboard_router
from .routers.channels import router as channel_router
from .routers.streams import router as stream_router
from .routers.members import router as members_router
from .routers.chat import router as chat_router
from .routers.registrations import router as registrations_router
from .routers.recordings import router as recordings_router
from .routers.stage import router as stage_router
from .routers.qa import router as qa_router
from .routers.polls import router as polls_router
from .routers.webhooks import router as webhooks_router
from .services.scheduler import run_auto_close_loop
from .sockets import sio
from .config import settings

# Without this, log.info(...) calls across the app (e.g. the temp-password fallback in
# email.py) are silently dropped -- Python's default logging level is WARNING, so only
# uvicorn's own request lines and log.error/log.warning calls were ever visible.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background sweep that auto-closes events once their scheduled end time passes --
    # see services/scheduler.py. Runs once per worker process; harmless if that ever
    # means redundant sweeps (closing an already-closed event is a no-op).
    task = asyncio.create_task(run_auto_close_loop())
    yield
    task.cancel()


# ponytail: tables are created by `python create_tables.py` (or alembic) now, not on startup —
# create_all here would miss the Stream model, which models/__init__.py doesn't register.
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

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(channel_router)
app.include_router(stream_router)
app.include_router(members_router)
app.include_router(chat_router)
app.include_router(registrations_router)
app.include_router(recordings_router)
app.include_router(stage_router)
app.include_router(qa_router)
app.include_router(polls_router)
app.include_router(webhooks_router)


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


# Wraps `app` so Socket.IO's own "/socket.io/" path is handled by `sio` and everything
# else still reaches FastAPI as normal. Run this (not `app`) so live chat works:
#   uvicorn app.main:socket_app --reload
socket_app = socketio.ASGIApp(sio, other_asgi_app=app)
