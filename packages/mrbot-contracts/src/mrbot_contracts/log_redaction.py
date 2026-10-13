"""Redaccion de URLs en logs: nunca URLs, solo ubicacion del objeto.

Una URL prefirmada es una credencial temporal. En los logs no debe aparecer
ninguna URL (ni host, ni query, ni firma): se reemplaza por la ubicacion del
objeto en el bucket (``bucket/clave``), que alcanza para volver a generar el
enlace cuando haga falta. Si la URL no apunta a un objeto, queda ``[url]``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlsplit

_URL_RE = re.compile(r"""https?://[^\s"'<>)\]]+""", re.IGNORECASE)
_BARE_QUERY_RE = re.compile(
    r"([?&]?)(X-Amz-[A-Za-z-]+|Signature|AWSAccessKeyId|Expires)=[^&\s\"']+",
    re.IGNORECASE,
)
#: Mensaje publico cuando el objeto ya no existe en el bucket.
OBJETO_ELIMINADO = "objeto eliminado del bucket"


def ubicacion_de_objeto(url: str) -> str:
    """``bucket/clave`` de una URL de almacenamiento, sin host ni query."""
    partes = urlsplit(url)
    ruta = unquote(partes.path or "").strip("/")
    return ruta or "[url]"


def redactar_urls(texto: str) -> str:
    """Reemplaza cada URL del texto por la ubicacion de su objeto."""
    if not texto:
        return texto

    def _sub(m: re.Match[str]) -> str:
        ubicacion = ubicacion_de_objeto(m.group(0).rstrip(".,;"))
        return f"<{ubicacion}>" if ubicacion != "[url]" else "[url]"

    limpio = _URL_RE.sub(_sub, str(texto))
    return _BARE_QUERY_RE.sub("", limpio)


class RedactorDeUrls(logging.Filter):
    """Filtro de logging: ningun registro sale con URLs."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            mensaje = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if "://" in mensaje or "X-Amz-" in mensaje:
            record.msg = redactar_urls(mensaje)
            record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            texto = str(record.exc_info[1])
            if "://" in texto or "X-Amz-" in texto:
                record.exc_text = redactar_urls(texto)
        return True


def instalar_redaccion_de_urls() -> None:
    """Instala el filtro en todos los handlers actuales y futuros del root.

    Tambien sube ``httpx``/``httpcore`` a WARNING: su log INFO imprime la URL
    completa de cada request.
    """
    for nombre in ("httpx", "httpcore"):
        logging.getLogger(nombre).setLevel(logging.WARNING)
    filtro = RedactorDeUrls()
    raiz = logging.getLogger()
    if not any(isinstance(f, RedactorDeUrls) for f in raiz.filters):
        raiz.addFilter(filtro)
    for handler in raiz.handlers:
        if not any(isinstance(f, RedactorDeUrls) for f in handler.filters):
            handler.addFilter(filtro)
    for nombre in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        for handler in logging.getLogger(nombre).handlers:
            if not any(isinstance(f, RedactorDeUrls) for f in handler.filters):
                handler.addFilter(filtro)
    _fabrica_con_redaccion()


_FABRICA_INSTALADA = False


def _fabrica_con_redaccion() -> None:
    """Redacta en la fabrica de registros: cubre loggers con handlers propios."""
    global _FABRICA_INSTALADA
    if _FABRICA_INSTALADA:
        return
    original = logging.getLogRecordFactory()

    def fabrica(*args, **kwargs):  # type: ignore[no-untyped-def]
        registro = original(*args, **kwargs)
        try:
            mensaje = registro.getMessage()
        except Exception:  # noqa: BLE001
            return registro
        if "://" in mensaje or "X-Amz-" in mensaje:
            registro.msg = redactar_urls(mensaje)
            registro.args = ()
        return registro

    logging.setLogRecordFactory(fabrica)
    _FABRICA_INSTALADA = True


__all__ = [
    "OBJETO_ELIMINADO",
    "RedactorDeUrls",
    "instalar_redaccion_de_urls",
    "redactar_urls",
    "ubicacion_de_objeto",
]
