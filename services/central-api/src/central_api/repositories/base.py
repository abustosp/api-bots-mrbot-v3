"""Base de repositorios: sesion, saneamiento y errores (plan 02 §2.2).

Un repositorio contiene SQLAlchemy o SQL preciso y devuelve entidades o DTOs
internos; nunca conoce headers ni modelos Pydantic publicos.
"""

from __future__ import annotations

import re
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


def _normalizar_clave(clave: object) -> str:
    """Reduce el nombre de una clave a letras y digitos en minusculas.

    ``apiKey``, ``API-KEY`` y ``api key`` comparten forma normalizada, de modo
    que una misma frontera cubre camelCase, guiones y espacios.
    """
    return re.sub(r"[^a-z0-9]", "", str(clave).strip().casefold())


_FORBIDDEN_NORMALIZADAS = frozenset(
    _normalizar_clave(clave) for clave in _FORBIDDEN_PAYLOAD_KEYS
)

#: Prefijos que delatan cabeceras, capacidades o sobres sellados.
_FORBIDDEN_PREFIJOS = (
    "authorization",
    "bearer",
    "cookie",
    "urlprefirmada",
    "presignedurl",
    "uploadurl",
    "downloadurl",
    "sealed",
)

#: Sufijos que delatan material sensible aunque la clave lleve contexto.
_FORBIDDEN_SUFIJOS = (
    "apikey",
    "apisecret",
    "clientsecret",
    "privatekey",
    "secretkey",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "tokenproveedor",
    "password",
    "passwd",
    "passphrase",
    "credentials",
    "credential",
    "token",
    "secret",
)

#: Marcas inequívocas que se rechazan en cualquier posición del nombre
#: (``usar_api_key_v2``). Son deliberadamente pocas: palabras débiles como
#: ``token`` o ``clave`` solo se juzgan al final del nombre para no rechazar
#: compuestos legítimos del dominio (``incluir_token_fiscal``, ``sin_clave``);
#: las vistas de lectura las ocultan igual porque filtran por subcadena.
_FORBIDDEN_CONTENIDOS = (
    "apikey",
    "apisecret",
    "clientsecret",
    "privatekey",
    "secretkey",
    "password",
    "passwd",
    "passphrase",
    "authorization",
    "presignedurl",
    "urlprefirmada",
    "sealed",
    "ciphertext",
)


def _clave_prohibida(clave: object) -> str | None:
    """Devuelve la marca que prohibe la clave, o ``None`` si es inocua.

    ``clave`` se compara solo por igualdad (no por sufijo) porque en el dominio
    fiscal aparece en nombres legítimos compuestos; el resto de las marcas
    tolera prefijos y sufijos (``authorization_header``, ``usar_api_key_v2``).
    """
    nombre = _normalizar_clave(clave)
    if not nombre:
        return None
    if nombre in _FORBIDDEN_NORMALIZADAS:
        return nombre
    for marca in _FORBIDDEN_CONTENIDOS:
        if marca in nombre:
            return marca
    for prefijo in _FORBIDDEN_PREFIJOS:
        if nombre.startswith(prefijo):
            return prefijo
    for sufijo in _FORBIDDEN_SUFIJOS:
        if nombre.endswith(sufijo):
            return sufijo
    return None


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
                marca = _clave_prohibida(clave)
                if marca is not None:
                    raise RepositoryError(
                        f"{etiqueta} contiene {marca!r}: use el sobre sellado"
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
