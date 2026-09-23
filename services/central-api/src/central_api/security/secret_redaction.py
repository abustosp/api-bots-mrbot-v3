"""Sanitizador de errores y redacción de secretos (SEC-1, plan 02 §10).

Disciplina heredada de ``app/utils/public_errors.py`` de V2: ninguna
respuesta pública contiene ``str(exc)``, trazas, selectores Playwright, URLs
internas o prefirmadas, rutas locales, nombres de tabla/host, llaves,
passwords ni body crudo. Solo salen ``error_code``, mensaje de clase,
``correlation_id`` y, cuando aplica, ``job_id`` visible al principal.
"""

from __future__ import annotations

from typing import Any

DANGEROUS_KEYS = frozenset(
    {
        "traceback", "exception", "request", "response", "raw_input",
        "cause", "debug", "api_key", "x-api-key", "authorization",
        "credentials", "clave", "password", "secret", "token",
        "upload_url", "presigned_url", "database_url",
    }
)

CLASS_MESSAGES: dict[str, str] = {
    "validation": "Datos de entrada inválidos.",
    "authentication": "Credenciales inválidas.",
    "additional_validation": "No se pudo validar la solicitud.",
    "service_not_enabled": "Servicio temporalmente no disponible.",
    "external_timeout": "El servicio externo no respondió a tiempo.",
    "navigation": "No se pudo completar la navegación requerida.",
    "query": "No se encontró la información solicitada.",
    "download": "No se pudo obtener el archivo solicitado.",
    "processing": "No se pudo procesar la solicitud.",
    "storage": "No se pudo guardar o recuperar la información.",
    "unexpected": "Ocurrió un error interno.",
    "not_found": "Recurso no encontrado.",
    "internal": "Ocurrió un error interno.",
    "forbidden": "Operación no permitida.",
    "idempotency_conflict": "La clave de idempotencia ya se usó con otro contenido.",
    "quota_exhausted": "Cuota agotada.",
}


def redact_mapping(data: Any) -> Any:
    """Redacta recursivamente claves peligrosas (listas y dicts incluidos)."""
    if isinstance(data, dict):
        clean: dict[str, Any] = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if lowered in DANGEROUS_KEYS or "secre" in lowered or "passw" in lowered:
                clean[key] = "[REDACTED]"
            else:
                clean[key] = redact_mapping(value)
        return clean
    if isinstance(data, list):
        return [redact_mapping(item) for item in data]
    return data


def public_error(
    error_code: str, correlation_id: str | None = None, job_id: str | None = None
) -> dict:
    """Construye el envelope público ``{"detail": {...}}`` (plan 02 §10.4)."""
    detail: dict[str, Any] = {
        "error_code": error_code,
        "message": CLASS_MESSAGES.get(error_code, CLASS_MESSAGES["unexpected"]),
    }
    if correlation_id:
        detail["correlation_id"] = correlation_id
    if job_id:
        detail["job_id"] = job_id
    return {"detail": detail}


__all__ = ["DANGEROUS_KEYS", "CLASS_MESSAGES", "redact_mapping", "public_error"]
