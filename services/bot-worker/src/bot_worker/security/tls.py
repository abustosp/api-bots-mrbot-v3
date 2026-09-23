"""Contextos TLS/mTLS para bot-worker (stdlib-only, sin dependencias nuevas).

Espejo de ``central_api.security.tls`` del lado ejecutor: el servidor
exige certificado de cliente de la CA conocida (la central se identifica
con CN ``MrBotCentral``) y el cliente presenta el certificado del worker
ante la central. Sin base de datos ni secretos ajenos (W-1/SEC-3): aquí
solo viajan rutas a ficheros montados, nunca bytes de claves.

Rutas (desde CLI ``--tls-*``, nunca entorno): CA que firma a la central,
identidad servidora propia y par cliente (por defecto, la servidora).
El servidor exige certificado de cliente con CN ``MrBotCentral``.
"""

from __future__ import annotations

import hmac
import os
import ssl
from dataclasses import dataclass

CENTRAL_SERVICE_IDENTITY = "MrBotCentral"
"""CN que la central debe presentar como cliente ante el worker."""

_TLS_MINIMUM = ssl.TLSVersion.TLSv1_2


@dataclass(frozen=True)
class TlsPaths:
    """Rutas de material TLS. Solo rutas: los bytes nunca salen del disco."""

    ca_file: str = ""
    cert_file: str = ""
    key_file: str = ""
    client_cert_file: str = ""
    client_key_file: str = ""
    require_peer: bool = True
    expected_peer_cn: str = CENTRAL_SERVICE_IDENTITY


def build_server_context(
    *,
    ca_path: str,
    cert_path: str,
    key_path: str,
    require_client: bool = True,
) -> ssl.SSLContext:
    """Crea el contexto servidor con mTLS opcionalmente exigido.

    Falla con ``FileNotFoundError`` si falta material: arrancar sin
    certificados es un error de despliegue, nunca HTTP silencioso.
    """
    for label, path in (("CA", ca_path), ("cert", cert_path), ("key", key_path)):
        if not path or not os.path.isfile(path):
            raise FileNotFoundError(f"TLS: falta el fichero {label}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = _TLS_MINIMUM
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    ctx.load_verify_locations(cafile=ca_path)
    ctx.verify_mode = ssl.CERT_REQUIRED if require_client else ssl.CERT_NONE
    return ctx


def build_client_context(
    *,
    ca_path: str,
    cert_path: str = "",
    key_path: str = "",
) -> ssl.SSLContext:
    """Crea el contexto cliente: confía en la CA y presenta certificado."""
    if not ca_path or not os.path.isfile(ca_path):
        raise FileNotFoundError("TLS: falta el fichero CA")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = _TLS_MINIMUM
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = False  # el llamador (httpx) valida el nombre
    ctx.load_verify_locations(cafile=ca_path)
    if cert_path or key_path:
        if not (cert_path and key_path and os.path.isfile(cert_path)):
            raise FileNotFoundError("TLS: identidad cliente incompleta")
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return ctx


def peer_common_name(cert: dict | None) -> str:
    """Extrae el CN del subject de un certificado ya verificado por TLS."""
    if not cert:
        return ""
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return str(value)
    return ""


def check_peer_identity(cert: dict | None, expected_cn: str) -> bool:
    """Vincula el canal mTLS con la identidad esperada (comparación exacta)."""
    if not expected_cn:
        return cert is not None
    return hmac.compare_digest(peer_common_name(cert), expected_cn)


def httpx_tls_kwargs(paths: TlsPaths) -> dict:
    """Devuelve kwargs para ``httpx.Client(verify=..., cert=...)`` (sin importar httpx)."""
    kwargs: dict = {"verify": paths.ca_file or True}
    if paths.client_cert_file and paths.client_key_file:
        kwargs["cert"] = (paths.client_cert_file, paths.client_key_file)
    return kwargs


def uvicorn_tls_kwargs(paths: TlsPaths) -> dict:
    """Devuelve kwargs de ``uvicorn.run(ssl_certfile=..., ...)`` desde rutas."""
    if not (paths.cert_file and paths.key_file):
        raise FileNotFoundError("TLS: sin identidad servidora configurada")
    kwargs: dict = {
        "ssl_certfile": paths.cert_file,
        "ssl_keyfile": paths.key_file,
    }
    if paths.ca_file:
        kwargs["ssl_ca_certs"] = paths.ca_file
        kwargs["ssl_cert_reqs"] = (
            ssl.CERT_REQUIRED if paths.require_peer else ssl.CERT_NONE
        )
    return kwargs


__all__ = [
    "CENTRAL_SERVICE_IDENTITY",
    "TlsPaths",
    "build_client_context",
    "build_server_context",
    "check_peer_identity",
    "httpx_tls_kwargs",
    "peer_common_name",
    "uvicorn_tls_kwargs",
]
