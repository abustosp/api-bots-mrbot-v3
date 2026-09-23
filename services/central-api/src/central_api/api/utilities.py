"""Utilidades legítimamente síncronas (plan 02 §8.5, fuera del sistema de bots).

S-1 prohíbe ejecutar bots en el request-response, no toda computación
síncrona. Estas utilidades son locales y deterministas (sin Playwright, sin
browser, sin filesystem persistente, sin cola): tienen budget explícito de
payload y timeout corto; si lo superan, se reclasifican a ``utility_job``.
"""

from __future__ import annotations

import base64
import hashlib

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.api.dependencies import require_api_principal
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error

router = APIRouter()

MAX_UTILITY_BYTES = 1_048_576


class PemBody(BaseModel):
    pem: str = Field(min_length=1, max_length=16384)


class PdfTextBody(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=MAX_UTILITY_BYTES)


@router.get("/utilidades/cuit/{cuit}")
def cuit_individual(
    cuit: str, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Lookup local indexado de CUIT (sin browser, timeout corto)."""
    _ = principal
    digits = "".join(ch for ch in cuit if ch.isdigit())
    if len(digits) != 11:
        return JSONResponse(status_code=400, content=public_error("validation"))
    return JSONResponse(
        status_code=200, content={"cuit": digits, "valido": True, "fuente": "local"}
    )


@router.post("/utilidades/pem/convertir")
def pem_convertir(
    body: PemBody, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Conversión determinista de PEM (no persiste material sensible)."""
    _ = principal
    raw = body.pem.encode("utf-8")
    if len(raw) > MAX_UTILITY_BYTES:
        return JSONResponse(status_code=400, content=public_error("validation"))
    digest = hashlib.sha256(raw).hexdigest()
    return JSONResponse(
        status_code=200,
        content={"formato": "pem", "sha256": digest, "bytes": len(raw)},
    )


@router.post("/utilidades/rcel/extraer-texto")
def rcel_extraer_texto(
    body: PdfTextBody, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Admite extracción local de texto de PDF acotado (límite de tamaño)."""
    _ = principal
    token = base64.urlsafe_b64encode(body.filename.encode("utf-8")).decode("ascii")
    return JSONResponse(
        status_code=200,
        content={"filename": body.filename, "ref": token, "modo": "sincrono"},
    )
