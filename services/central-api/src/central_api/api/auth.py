"""Intercambio de API key por token Bearer."""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from central_api.api.dependencies import _no_autorizado, authenticate_api_key
from central_api.security.bearer import encode_bearer

router = APIRouter(prefix="/auth", tags=["autenticación"])


class TokenBody(BaseModel):
    usuario: str = Field(
        min_length=2,
        max_length=254,
        description="Email o alias asociado a la API key.",
        examples=["persona@example.com"],
    )
    api_key: str = Field(
        min_length=1,
        description="API key vigente del usuario.",
        examples=["mbk_0123456789abcdef_abcdef0123456789"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "usuario": "persona@example.com",
                    "api_key": "mbk_0123456789abcdef_abcdef0123456789",
                }
            ]
        }
    }


@router.post("/token", summary="Intercambiar API key por Bearer token")
async def emitir_token(body: TokenBody, response: Response) -> dict[str, str]:
    """Valida la pareja usuario/API key y devuelve un token sin estado."""
    identidad = body.usuario.strip().casefold()
    principal = await authenticate_api_key(identidad, body.api_key.strip())
    if principal is None:
        raise _no_autorizado()
    response.headers["Cache-Control"] = "no-store"
    return {
        "access_token": encode_bearer(identidad, body.api_key.strip()),
        "token_type": "bearer",
        "usuario": identidad,
    }


__all__ = ["router"]
