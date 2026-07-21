from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from .auth import router as auth_router
from .api.dashboard import router as dashboard_router
from .routers.channels import router as channel_router
from .routers.streams import router as stream_router
from .config import settings

# ponytail: tables are created by `python create_tables.py` (or alembic) now, not on startup —
# create_all here would miss the Stream model, which models/__init__.py doesn't register.
app = FastAPI(title="ZoikoStream API")

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
