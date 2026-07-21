from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.routers.channels import router as channel_router
from app.routers.streams import router as stream_router

app = FastAPI(title="ZoikoStream API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(channel_router)
app.include_router(stream_router)

@app.get("/health")
def health():
    return {"status": "ok"}