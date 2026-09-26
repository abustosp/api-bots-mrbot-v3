"""Agregador de rutas públicas /api/v3 (S-1: nunca ejecutan bots)."""

from fastapi import APIRouter

from central_api.api.account import router as account_router
from central_api.api.auth import router as auth_router
from central_api.api.billing import router as billing_router
from central_api.api.bot_compat import router as bot_compat_router
from central_api.api.bot_routes import router as bot_routes_router
from central_api.api.bots import router as bots_router
from central_api.api.jobs import router as jobs_router
from central_api.api.security import router as security_router
from central_api.api.uploads import router as uploads_router
from central_api.api.users import router as users_router
from central_api.api.utilities import router as utilities_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(bots_router, tags=["bots"])
router.include_router(bot_routes_router)
router.include_router(bot_compat_router)
router.include_router(jobs_router, tags=["jobs"])
router.include_router(account_router, tags=["cuenta"])
router.include_router(uploads_router, tags=["uploads"])
router.include_router(utilities_router, tags=["utilidades"])
router.include_router(billing_router, tags=["facturacion"])
router.include_router(security_router, tags=["seguridad"])
