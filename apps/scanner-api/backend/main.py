import os
from fastapi import FastAPI

app = FastAPI(title="scanner-api", version="0.1.0")

_VERSION = "0.1.0"
_PHASE = "2A-skeleton"


@app.get("/health")
def health():
    return {"status": "ok", "service": "scanner-api"}


@app.get("/version")
def version():
    return {"service": "scanner-api", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    return {
        "service": "scanner-api",
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "massive_configured": bool(os.getenv("MASSIVE_API_KEY")),
        "mode": "skeleton",
        "scanning_enabled": False,
        "scheduler_enabled": False,
    }
