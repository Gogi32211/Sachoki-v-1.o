import os
from fastapi import FastAPI

app = FastAPI(title="research-api", version="0.1.0")

_VERSION = "0.1.0"
_PHASE = "2A-skeleton"


@app.get("/health")
def health():
    return {"status": "ok", "service": "research-api"}


@app.get("/version")
def version():
    return {"service": "research-api", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    return {
        "service": "research-api",
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "scanner_api_url_configured": bool(os.getenv("SCANNER_API_URL")),
        "mode": "skeleton",
        "research_jobs_enabled": False,
    }
