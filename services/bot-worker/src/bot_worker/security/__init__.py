"""Seguridad de transporte del worker (plan 03 §4.4, camino a mTLS).

El worker es ejecutor puro sin base de datos (W-1): este paquete solo
maneja TLS mutuo (stdlib) y nunca importa drivers, ORM ni secretos de la
central. El servidor exige certificado de cliente firmado por la CA
conocida y la capa de aplicación vincula el CN ``MrBotCentral``; el
cliente presenta su certificado ante la central.
"""

from bot_worker.security.tls import (
    CENTRAL_SERVICE_IDENTITY,
    TlsPaths,
    build_client_context,
    build_server_context,
    check_peer_identity,
    httpx_tls_kwargs,
    peer_common_name,
    uvicorn_tls_kwargs,
)

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
