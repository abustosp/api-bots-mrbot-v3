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
    "clave_fiscal",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "client_secret",
    "api_secret",
    "private_key",
    "token",
    "access_token",
    "refresh_token",
    "token_proveedor",
    "api_key",
    "authorization",
    "cookie",
    "credentials",
    "url_prefirmada",
    "presigned_url",
    "upload_url",
    "download_url",
    "sealed_section",
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
    eventos ni auditoria. El recorrido es recursivo para que anidar una clave
    prohibida dentro de objetos o listas no evada la frontera de persistencia.
    """
    if payload is None:
        return
    pendientes: list[Any] = [payload]
    while pendientes:
        valor = pendientes.pop()
        if isinstance(valor, Mapping):
            for clave, anidado in valor.items():
                nombre = str(clave).strip().casefold().replace("-", "_").replace(" ", "_")
                if nombre in _FORBIDDEN_PAYLOAD_KEYS:
                    raise RepositoryError(
                        f"{etiqueta} contiene {nombre!r}: use el sobre sellado"
                    )
                pendientes.append(anidado)
        elif isinstance(valor, (list, tuple)):
            pendientes.extend(valor)


def scope_in(allowed: Collection[str], node: str) -> bool:
    """Indica si ``node`` pertenece al inventario admitido."""
    return node in set(allowed)


__all__ = [
    "NotFoundError",
    "RepositoryError",
    "assert_no_secretos",
    "scope_in",
]
