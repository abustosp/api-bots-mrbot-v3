"""Clave pública de la central para cifrar credenciales fiscales.

Los clientes cifran ``clave`` con esta pública (RSA-OAEP-SHA256) y la envían
como ``clave_encriptada`` (Base64). La central persiste solo el ciphertext y
el panel lo descifra bajo demanda con auditoría
(``GET /admin/jobs/{job_id}/credentials``).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.security.secret_redaction import public_error

router = APIRouter()


class ClavePublicaRespuesta(BaseModel):
    success: bool = Field(description="Indica que la clave pública está disponible.", examples=[True])
    algoritmo: str = Field(description="Algoritmo que debe utilizarse para proteger la clave fiscal antes de enviarla.", examples=["RSA-OAEP-SHA256"])
    campo: str = Field(description="Nombre del campo que recibe el valor cifrado.", examples=["clave_encriptada"])
    formato: str = Field(description="Representación requerida para el contenido cifrado.", examples=["Base64(RSA_OAEP_SHA256(clave_utf8))"])
    huella_sha256: str = Field(description="Huella SHA-256 para identificar esta clave pública.", examples=["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"])
    clave_publica_pem: str = Field(
        description="Clave pública en formato PEM, que el cliente debe usar para cifrar la clave fiscal.",
        examples=["-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "success": True,
                    "algoritmo": "RSA-OAEP-SHA256",
                    "campo": "clave_encriptada",
                    "formato": "Base64(RSA_OAEP_SHA256(clave_utf8))",
                    "huella_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                    "clave_publica_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----",
                }
            ]
        }
    }


@router.get(
    "/seguridad/clave-publica",
    summary="Obtener la clave pública para proteger credenciales",
    description=(
        "Devuelve la clave pública y los datos necesarios para cifrar una clave "
        "fiscal antes de enviarla. No requiere autenticación. Si el servicio no "
        "puede proporcionar la clave pública, no envíes la credencial y vuelve "
        "a intentarlo más tarde."
    ),
    responses={
        200: {
            "model": ClavePublicaRespuesta,
            "description": "Clave pública disponible junto con su algoritmo, formato y huella de identificación.",
        },
        503: {
            "description": "La clave pública no está disponible temporalmente. No envíes credenciales fiscales sin proteger; vuelve a consultar cuando el servicio esté disponible.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error_code": "service_not_enabled",
                            "message": "Servicio temporalmente no disponible.",
                        }
                    }
                }
            },
        },
    },
)
def clave_publica() -> JSONResponse:
    """Devuelve la clave pública para cifrar credenciales fiscales antes del envío.

    No requiere autenticación. Si la clave pública no está disponible, responde
    503 y el cliente debe posponer el envío de credenciales fiscales.
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
