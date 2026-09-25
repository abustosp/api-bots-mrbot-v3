"""Clave pública de la central para cifrar credenciales fiscales.

Los clientes cifran ``clave`` con esta pública (RSA-OAEP-SHA256) y la envían
como ``clave_encriptada`` (Base64). La central persiste solo el ciphertext y
el panel lo descifra bajo demanda con auditoría
(``GET /admin/jobs/{job_id}/credentials``).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from central_api.security.secret_redaction import public_error

router = APIRouter()


@router.get("/seguridad/clave-publica")
def clave_publica() -> JSONResponse:
    """Expone la pública RSA de custodia (sin autenticación).

    Sin ``RSA_PRIVATE_KEY`` configurada responde 503 sanitizado: no hay
    custodia posible y el cliente no debe enviar secretos en claro.
    """
    try:
        from central_api.security.rsa_credentials import (
            configured_public_pem,
            public_key_fingerprint,
        )

        pem = configured_public_pem()
        huella = public_key_fingerprint(pem)
    except (RuntimeError, ValueError):
        return JSONResponse(
            status_code=503, content=public_error("service_not_enabled")
        )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "algoritmo": "RSA-OAEP-SHA256",
            "campo": "clave_encriptada",
            "formato": "Base64(RSA_OAEP_SHA256(clave_utf8))",
            "huella_sha256": huella,
            "clave_publica_pem": pem,
        },
    )
