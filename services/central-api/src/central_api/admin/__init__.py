"""Agregador de rutas de administración (/admin, excluido de OpenAPI pública).

Conserva el panel de workers existente (``workers.derive_state`` y
``/admin/workers``) y suma usuarios, jobs, flota con alertas, suscripciones
y auditoría según ``plans/05-admin-panel/plan.md``.
"""

from fastapi import APIRouter

from central_api.admin.audit import router as audit_router
from central_api.admin.fleet import router as fleet_router
from central_api.admin.jobs import router as jobs_router
from central_api.admin.panel import router as panel_router
from central_api.admin.subscriptions import router as billing_router
from central_api.admin.users import router as users_router
from central_api.admin.workers import router as workers_admin_router

router = APIRouter()
router.include_router(panel_router, tags=["admin"])
router.include_router(workers_admin_router, tags=["admin-workers"])
router.include_router(users_router, tags=["admin-users"])
router.include_router(jobs_router, tags=["admin-jobs"])
router.include_router(fleet_router, tags=["admin-fleet"])
router.include_router(billing_router, tags=["admin-billing"])
router.include_router(audit_router, tags=["admin-audit"])
