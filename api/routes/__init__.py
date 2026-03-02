"""Route registration for the FastAPI app."""

from __future__ import annotations

from fastapi import FastAPI

from api.routes.analyze import router as analyze_router
from api.routes.config import router as config_router
from api.routes.files import router as files_router
from api.routes.health import router as health_router
from api.routes.jobs import router as jobs_router
from api.routes.organizations import router as organizations_router
from api.routes.reports import router as reports_router
from api.routes.upload import router as upload_router


def register_routes(app: FastAPI) -> None:
    """Register all API routers."""
    app.include_router(health_router)
    app.include_router(upload_router)
    app.include_router(config_router)
    app.include_router(analyze_router)
    app.include_router(jobs_router)
    app.include_router(organizations_router)
    app.include_router(files_router)
    app.include_router(reports_router)
