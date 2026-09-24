"""Ayudas compartidas del panel /admin (autenticación, validación, redacción).

Toda ruta mutante del panel exige ``ADMIN_TOKEN`` en cabecera Bearer, con la
misma semántica que ``central_api.admin.workers.require_admin``: sin token
configurado es 403, sin cabecera es 401 y con token distinto es 403.
"""

from __future__ import annotations

import hmac
import re

from fastapi import HTTPException

from central_api.settings import get_settings

# Subcadenas que marcan un valor como sensible y obligan a enmascararlo.
_CLAVES_SENSIBLES = (
    "password",
    "passwd",
    "contrasena",
    "contraseña",
    "clave",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "firmada",
    "presigned",
    "mfa",
    "cookie",
)


def require_admin(authorization: str | None) -> str:
    """Valida el Bearer [REDACTED] y devuelve el actor ("admin:token")."""
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN no configurado")
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="Falta Bearer [REDACTED]")
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN inválido")
    return "admin:token"


def validar_motivo(motivo: str | None, *, obligatorio: bool = True) -> str:
    """Valida el motivo de una operación sensible (10 a 500 caracteres)."""
    texto = (motivo or "").strip()
    if not texto:
        if obligatorio:
            raise HTTPException(status_code=400, detail="motivo obligatorio")
        return ""
    if not 10 <= len(texto) <= 500:
        raise HTTPException(
            status_code=400, detail="motivo debe tener entre 10 y 500 caracteres"
        )
    return texto


def es_clave_sensible(nombre: str) -> bool:
    """Indica si un nombre de campo pertenece a la clase de valores sensibles."""
    minus = nombre.lower()
    return any(k in minus for k in _CLAVES_SENSIBLES)


#: Cualquier valor que parezca una URL se oculta completo: una query prefirmada
#: lleva firma, bucket y host, y el panel no debe exhibir capacidades.
_URL_EN_VALOR = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://|www[.]")
#: ``authorization: Bearer x`` / ``api_key=...`` dentro de un texto libre.
_SECRETO_ASIGNADO_EN_VALOR = re.compile(
    r"((?:^|[^A-Za-z0-9_])"
    r"(?:authorization|password|passwd|passphrase|secret|token|credential|"
    r"credencial|clave|api[_ -]?key|access[_ -]?key|client[_ -]?secret|"
    r"private[_ -]?key)"
    r"\s*[:=]\s*)(?:bearer\s+|basic\s+)?([^\s,;]+)",
    re.IGNORECASE,
)
#: ``Bearer <token>`` suelto, sin clave a la izquierda.
_ESQUEMA_AUTH_EN_VALOR = re.compile(
    r"((?:^|[^A-Za-z0-9_])(?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)

MARCA_URL_OCULTA = "[REDACTED_URL]"
MARCA_SECRETO = "[REDACTED_SECRET]"


def sanear_valor_texto(texto: str) -> str:
    """Enmascara URLs y credenciales embebidas en un valor de texto.

    Espeja el saneamiento SQL de la migración 0015: ocultar la clave no basta
    cuando el secreto viaja dentro del valor (``{"notas": "Bearer eyJ..."}``).
    """
    if _URL_EN_VALOR.search(texto):
        return MARCA_URL_OCULTA
    limpio = _ESQUEMA_AUTH_EN_VALOR.sub(r"\1" + MARCA_SECRETO, texto)
    return _SECRETO_ASIGNADO_EN_VALOR.sub(r"\1" + MARCA_SECRETO, limpio)


def redactar_metadata(valor: object) -> object:
    """Enmascara recursivamente valores sensibles de un JSON de auditoría.

    Aplica las dos capas del saneamiento: la clave decide (``token``,
    ``clave_fiscal``) y, cuando la clave es inocua (``notas``, ``destino``),
    el valor todavía se revisa por si lleva URL o credencial embebida.
    """
    if isinstance(valor, dict):
        salida: dict = {}
        for clave, item in valor.items():
            if es_clave_sensible(str(clave)):
                salida[clave] = "[REDACTED]"
            else:
                salida[clave] = redactar_metadata(item)
        return salida
    if isinstance(valor, list):
        return [redactar_metadata(item) for item in valor]
    if isinstance(valor, str):
        return sanear_valor_texto(valor)
    return valor


def enmascarar(valor: str | None, visibles: int = 4) -> str | None:
    """Muestra solo el prefijo de un identificador no secreto (p. ej. API key)."""
    if not valor:
        return valor
    if len(valor) <= visibles:
        return valor[:1] + "***"
    return valor[:visibles] + "***"
