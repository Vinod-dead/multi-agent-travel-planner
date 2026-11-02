"""FastAPI application: JSON API under /api plus the static web UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from travel_planner import __version__
from travel_planner.config import Settings, get_settings
from travel_planner.export import to_markdown
from travel_planner.jobs import JobManager
from travel_planner.llm import LLMClient
from travel_planner.models import TripPlan, TripRequest
from travel_planner.store import PlanStore

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent / "web"

_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, store: PlanStore | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.llm = llm or LLMClient(settings)
        app.state.store = store or PlanStore(settings.db_path)
        stale = app.state.store.fail_stale()
        if stale:
            log.info("Marked %d interrupted plan(s) as failed", stale)
        app.state.jobs = JobManager(app.state.store, app.state.llm)
        yield
        await app.state.jobs.shutdown()
        await app.state.llm.aclose()
        if store is None:
            app.state.store.close()

    app = FastAPI(title="Multi-Agent Travel Planner", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health(refresh: bool = False) -> dict[str, Any]:
        llm_client: LLMClient = app.state.llm
        ping = await llm_client.ping(use_cache=not refresh)
        return {
            "status": "ok",
            "version": __version__,
            "llm": {
                "enabled": settings.llm_enabled,
                "ok": ping["ok"],
                "host": llm_client.host,
                "model": settings.llm_model,
                "models": ping["models"],
                "error": ping["error"],
            },
            "web_research": settings.tavily_enabled,
        }

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {"default_currency": settings.default_currency, "max_days": settings.max_days, "model": settings.llm_model}

    # ------------------------------------------------------------------ plans
    @app.post("/api/plans", status_code=202)
    async def create_plan(request: TripRequest) -> dict[str, str]:
        return {"id": app.state.jobs.submit(request)}

    @app.get("/api/plans")
    async def list_plans(limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
        return app.state.store.list(limit)

    @app.get("/api/plans/{plan_id}")
    async def get_plan(plan_id: str) -> dict[str, Any]:
        found = app.state.store.get(plan_id)
        if not found:
            raise HTTPException(404, "Plan not found")
        return found

    @app.post("/api/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: str) -> dict[str, bool]:
        if not app.state.store.get(plan_id):
            raise HTTPException(404, "Plan not found")
        return {"cancelled": app.state.jobs.cancel(plan_id)}

    @app.delete("/api/plans/{plan_id}", status_code=204)
    async def delete_plan(plan_id: str) -> None:
        app.state.jobs.cancel(plan_id)
        if not app.state.store.delete(plan_id):
            raise HTTPException(404, "Plan not found")

    @app.get("/api/plans/{plan_id}/export")
    async def export_plan(plan_id: str, format: str = Query("md", pattern="^(md|json)$")):
        found = app.state.store.get(plan_id)
        if not found:
            raise HTTPException(404, "Plan not found")
        if found["status"] != "done" or not found["result"]:
            raise HTTPException(409, "Plan is not finished yet")
        if format == "json":
            return JSONResponse(found["result"], headers={"Content-Disposition": f'attachment; filename="trip-{plan_id}.json"'})
        text = to_markdown(TripPlan.model_validate(found["result"]))
        return PlainTextResponse(text, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="trip-{plan_id}.md"'})

    # ------------------------------------------------------------------ web UI
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def get_app() -> FastAPI:
    """Factory for ``uvicorn travel_planner.api:get_app --factory``."""
    return create_app()
