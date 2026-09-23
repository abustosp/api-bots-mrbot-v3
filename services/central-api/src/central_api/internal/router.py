"""Agregador de rutas internas /internal/v1 (no se exponen a internet)."""

from fastapi import APIRouter

from central_api.internal.job_events import router as events_router
from central_api.internal.job_results import router as results_router
from central_api.internal.uploads import router as uploads_router
from central_api.internal.workers import router as workers_router

router = APIRouter()
router.include_router(workers_router, tags=["internal-workers"])
router.include_router(events_router, tags=["internal-events"])
router.include_router(results_router, tags=["internal-jobs"])
router.include_router(uploads_router, tags=["internal-artifacts"])
