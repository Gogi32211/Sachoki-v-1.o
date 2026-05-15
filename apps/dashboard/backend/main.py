import os
from fastapi import FastAPI

app = FastAPI(title="dashboard", version="0.1.0")

_VERSION = "0.1.0"
_PHASE = "2A-skeleton"


@app.get("/health")
def health():
    return {"status": "ok", "service": "dashboard"}


@app.get("/version")
def version():
    return {"service": "dashboard", "version": _VERSION, "phase": _PHASE}


@app.get("/api/debug/status")
def debug_status():
    return {
        "service": "dashboard",
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "scanner_api_url_configured": bool(os.getenv("SCANNER_API_URL")),
        "research_api_url_configured": bool(os.getenv("RESEARCH_API_URL")),
        "massive_configured": bool(os.getenv("MASSIVE_API_KEY")),
        "anthropic_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
        "mode": "skeleton",
    }
