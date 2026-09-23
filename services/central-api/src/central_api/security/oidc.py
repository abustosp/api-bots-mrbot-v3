"""Autenticación de administradores vía OIDC (plan 02 §4.6).

Contrato: OIDC Authorization Code con PKCE contra el proveedor
corporativo, sesión en cookie ``HttpOnly``/``Secure``/``SameSite=Lax`` con
identificador server-side, MFA según IdP; sin HTTP Basic ni contraseña
global de entorno como identidad durable. Este módulo expone el mapeo
``subject -> admin``, la guarda de entorno para el proveedor local de
emergencia (solo si está activado explícitamente, Argon2id + TOTP) y el
flujo genérico contra un issuer configurable (descubrimiento, URL de
autorización con PKCE, intercambio de código y userinfo).

El secreto de cliente se lee SOLO desde archivo (``*_FILE``): nunca viaja
en el entorno en claro ni se loguea (ver ``_read_secret``). El transporte
HTTP es inyectable para probar con un mock sin red.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AdminIdentity:
    """Identidad administrativa resuelta desde el IdP."""

    subject: str
    email: str = ""
    roles: frozenset[str] = frozenset()


class OidcError(ValueError):
    """El login OIDC no pudo completarse. Sin detalle del secreto."""


def local_emergency_provider_enabled(flag: str | None) -> bool:
    """El proveedor local solo existe si se activa explícitamente."""
    return (flag or "").strip().lower() in ("1", "true", "yes", "on")


def map_oidc_subject(subject: str, email: str = "") -> AdminIdentity:
    """Mapea el ``subject`` OIDC a identidad admin (roles vía base en PG)."""
    if not subject:
        raise ValueError("subject OIDC vacío")
    return AdminIdentity(subject=subject, email=email)


def _read_secret(value: str = "", *, file_path: str = "") -> str:
    """Lee un secreto solo desde archivo; el valor directo se ignora.

    ``value`` existe solo por compatibilidad de firma y nunca se usa como
    secreto: si el archivo falta o está vacío, falla cerrado.
    """
    _ = value  # el secreto en claro por entorno está prohibido
    path = (file_path or "").strip()
    if not path:
        raise OidcError("secreto OIDC sin fichero configurado")
    try:
        with open(path, encoding="utf-8") as fh:
            secret = fh.read().strip()
    except OSError as exc:
        raise OidcError("no pudo leerse el secreto OIDC") from exc
    if not secret:
        raise OidcError("secreto OIDC vacío")
    return secret


@dataclass(frozen=True)
class OidcConfig:
    """Issuer configurable + cliente confidencial (secreto solo por archivo)."""

    issuer_url: str
    client_id: str
    client_secret_file: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")

    def validated(self) -> OidcConfig:
        """Falla cerrado si el issuer o el cliente no están configurados."""
        if not self.issuer_url.startswith(("https://", "http://localhost")):
            raise OidcError("issuer OIDC no configurado o no https")
        if not self.client_id:
            raise OidcError("cliente OIDC no configurado")
        return self


def config_from_env(prefix: str = "OIDC_") -> OidcConfig:
    """Arma la configuración desde entorno; el secreto, solo por fichero."""
    issuer = (os.environ.get(prefix + "ISSUER_URL") or "").strip().rstrip("/")
    client_id = (os.environ.get(prefix + "CLIENT_ID") or "").strip()
    secret_file = (os.environ.get(prefix + "CLIENT_SECRET_FILE") or "").strip()
    redirect = (os.environ.get(prefix + "REDIRECT_URI") or "").strip()
    return OidcConfig(
        issuer_url=issuer,
        client_id=client_id,
        client_secret_file=secret_file,
        redirect_uri=redirect,
    ).validated()


@dataclass(frozen=True)
class PkcePair:
    """Par PKCE S256: el verificador vive solo en la sesión server-side."""

    verifier: str
    challenge: str


def new_pkce_pair() -> PkcePair:
    """Genera un par PKCE S256 (RFC 7636)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return PkcePair(verifier=verifier, challenge=challenge)


def build_authorize_url(
    config: OidcConfig,
    authorization_endpoint: str,
    *,
    state: str,
    code_challenge: str,
) -> str:
    """Arma la URL de autorización (code + PKCE S256, sin secreto)."""
    if not state or not code_challenge:
        raise OidcError("state o challenge PKCE vacíos")
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "scope": " ".join(config.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{authorization_endpoint}?{query}"


def _default_post_form(url: str, data: dict[str, str], timeout: float) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _default_get_json(url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover_configuration(
    issuer_url: str,
    *,
    http_get: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Lee el documento de descubrimiento y valida que sea del issuer pedido."""
    issuer = issuer_url.rstrip("/")
    getter = http_get or _default_get_json
    try:
        doc = getter(f"{issuer}/.well-known/openid-configuration", {}, timeout)
    except OidcError:
        raise
    except Exception as exc:
        raise OidcError("descubrimiento OIDC sin respuesta") from exc
    if not isinstance(doc, dict) or doc.get("issuer", "").rstrip("/") != issuer:
        raise OidcError("descubrimiento OIDC con issuer distinto")
    for endpoint in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        if not doc.get(endpoint):
            raise OidcError("descubrimiento OIDC incompleto")
    return doc


def exchange_code(
    config: OidcConfig,
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    http_post: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Intercambia el código por tokens (el secreto sale solo hacia el IdP)."""
    if not code or not code_verifier:
        raise OidcError("código o verificador PKCE vacíos")
    poster = http_post or _default_post_form
    try:
        tokens = poster(
            token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.redirect_uri,
                "client_id": config.client_id,
                "client_secret": _read_secret(file_path=config.client_secret_file),
                "code_verifier": code_verifier,
            },
            timeout,
        )
    except OidcError:
        raise
    except Exception as exc:
        raise OidcError("intercambio de código sin respuesta") from exc
    if not isinstance(tokens, dict) or not tokens.get("access_token"):
        raise OidcError("intercambio de código rechazado")
    return tokens


def fetch_userinfo(
    userinfo_endpoint: str,
    access_token: str,
    *,
    http_get: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Lee los claims con el access token (el token nunca se loguea)."""
    if not access_token:
        raise OidcError("access token vacío")
    getter = http_get or _default_get_json
    try:
        # El token viaja solo hacia el IdP; este módulo nunca lo loguea.
        claims = getter(
            userinfo_endpoint, {"Authorization": f"Bearer {access_token}"}, timeout
        )
    except OidcError:
        raise
    except Exception as exc:
        raise OidcError("userinfo sin respuesta") from exc
    if not isinstance(claims, dict) or not claims.get("sub"):
        raise OidcError("userinfo sin subject")
    return claims


def login_admin(
    config: OidcConfig,
    *,
    code: str,
    code_verifier: str,
    http_post: Callable[..., dict[str, Any]] | None = None,
    http_get: Callable[..., dict[str, Any]] | None = None,
    timeout: float = 5.0,
) -> AdminIdentity:
    """Login genérico contra el issuer configurado; devuelve la identidad admin.

    Flujo: descubrimiento -> intercambio de código -> userinfo -> mapeo de
    ``sub``. Falla cerrado si el email verificado resulta falso.
    """
    config.validated()
    doc = discover_configuration(config.issuer_url, http_get=http_get, timeout=timeout)
    tokens = exchange_code(
        config,
        str(doc["token_endpoint"]),
        code=code,
        code_verifier=code_verifier,
        http_post=http_post,
        timeout=timeout,
    )
    claims = fetch_userinfo(
        str(doc["userinfo_endpoint"]),
        str(tokens["access_token"]),
        http_get=http_get,
        timeout=timeout,
    )
    if "email_verified" in claims and not claims["email_verified"]:
        raise OidcError("email del IdP sin verificar")
    return map_oidc_subject(str(claims["sub"]), str(claims.get("email", "")))


__all__ = [
    "AdminIdentity",
    "OidcConfig",
    "OidcError",
    "PkcePair",
    "local_emergency_provider_enabled",
    "map_oidc_subject",
    "config_from_env",
    "new_pkce_pair",
    "build_authorize_url",
    "discover_configuration",
    "exchange_code",
    "fetch_userinfo",
    "login_admin",
]
