"""Base de repositorios: sesion, saneamiento y errores (plan 02 §2.2).

Un repositorio contiene SQLAlchemy o SQL preciso y devuelve entidades o DTOs
internos; nunca conoce headers ni modelos Pydantic publicos.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

#: Claves que jamas deben persistirse (credenciales, secretos, capacidades).
_FORBIDDEN_PAYLOAD_KEYS = (
    "clave",
    "clave_representante",
    "clave_encriptada",
    "password",
    "secret",
    "token_proveedor",
    "url_prefirmada",
    "presigned_url",
    "sealed_privkey",
)


class RepositoryError(RuntimeError):
    """Fallo persistente del repositorio (restriccion, FK o estado ilegal)."""


class NotFoundError(RepositoryError):
    """La entidad solicitada no existe o escapa al ambito del principal."""


def assert_no_secretos(payload: Mapping[str, Any] | None, etiqueta: str) -> None:
    """Rechaza payloads que intenten persistir material sensible en claro.

    Lo sensible viaja solo en el sobre sellado RSA+Fernet
    (``central_api.security.sealed``) y nunca queda en columnas, JSONB,
    eventos ni auditoria.
    """
    if not payload:
        return
    lowered = {str(k).lower() for k in payload}
    for forbidden in _FORBIDDEN_PAYLOAD_KEYS:
        if forbidden in lowered:
            raise RepositoryError(
                f"{etiqueta} contiene {forbidden!r}: use el sobre sellado"
            )


def scope_in(allowed: Collection[str], node: str) -> bool:
    """Indica si ``node`` pertenece al inventario admitido."""
    return node in set(allowed)


__all__ = [
    "NotFoundError",
    "RepositoryError",
    "assert_no_secretos",
    "scope_in",
]
