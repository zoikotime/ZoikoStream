from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401 — registers tables on Base before create_all
from .auth import router as auth_router
from .config import settings
from .db import Base, engine

# ponytail: create_all on startup is enough for phase 1. Switch to alembic when the schema churns.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="ZoikoStream API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}
