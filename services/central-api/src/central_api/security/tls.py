"""Contextos TLS/mTLS para central-api (stdlib-only, sin dependencias nuevas).

El canal central<->worker usa TLS mutuo: cada servidor presenta su
certificado y exige certificado de cliente firmado por la CA conocida.
El handshake falla antes de HTTP si el par no presenta credencial válida;
la capa de aplicación solo vincula la identidad (CN) al nodo ya
autorizado por inventario + token de servicio (ver ``worker_auth``).

Todo material sensible vive en archivos montados (``/certs`` o Docker
secrets): aquí solo viajan rutas, nunca bytes de claves. Nada se loguea.

Variables (todas rutas a fichero, sin valores por defecto con secreto):

- ``TLS_CA_FILE``: CA que firma las identidades del par.
- ``TLS_CERT_FILE`` / ``TLS_KEY_FILE``: identidad servidora propia.
- ``TLS_CLIENT_CERT_FILE`` / ``TLS_CLIENT_KEY_FILE``: identidad cliente
  propia para llamadas salientes (por defecto, la servidora).
- ``TLS_REQUIRE_PEER`` (``"1"``/``"0"``): exigir certificado de cliente.
- ``TLS_EXPECTED_PEER_CN``: CN esperado del par (``""`` = no vincular).
"""

from __future__ import annotations

import hmac
import os
import ssl
from dataclasses import dataclass

CENTRAL_SERVICE_IDENTITY = "MrBotCentral"
"""CN que la central presenta como cliente ante los workers."""

WORKER_SERVICE_IDENTITY = "worker-local-01"
"""CN de referencia del worker local (solo desarrollo)."""

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
    expected_peer_cn: str = ""


def paths_from_env(prefix: str = "") -> TlsPaths:
    """Lee rutas TLS desde entorno (nombres ``TLS_*`` con prefijo opcional)."""
    base = prefix.upper()

    def _get(name: str) -> str:
        return (os.environ.get(base + name) or "").strip()

    def _flag(name: str, default: bool) -> bool:
        raw = _get(name).lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return default

    cert = _get("TLS_CERT_FILE")
    key = _get("TLS_KEY_FILE")
    return TlsPaths(
        ca_file=_get("TLS_CA_FILE"),
        cert_file=cert,
        key_file=key,
        client_cert_file=_get("TLS_CLIENT_CERT_FILE") or cert,
        client_key_file=_get("TLS_CLIENT_KEY_FILE") or key,
        require_peer=_flag("TLS_REQUIRE_PEER", True),
        expected_peer_cn=_get("TLS_EXPECTED_PEER_CN"),
    )


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
    "WORKER_SERVICE_IDENTITY",
    "TlsPaths",
    "paths_from_env",
    "build_server_context",
    "build_client_context",
    "peer_common_name",
    "check_peer_identity",
    "httpx_tls_kwargs",
    "uvicorn_tls_kwargs",
]
