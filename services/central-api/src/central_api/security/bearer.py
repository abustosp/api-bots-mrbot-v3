"""Codificación del token Bearer público de central-api.

El token es ``base64url(usuario).base64url(api_key)`` sin requisito de
padding. El decodificador también admite base64 estándar para interoperar con
clientes que no usan el alfabeto URL-safe.
"""

from __future__ import annotations

import base64
import binascii


def _encode_part(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("usuario y api_key deben ser strings no vacíos")
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_part(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise ValueError("parte base64 inválida")
    try:
        encoded = value.encode("ascii")
        unpadded = encoded.rstrip(b"=")
        padding = encoded[len(unpadded):]
        if len(padding) > 2 or b"=" in unpadded:
            raise ValueError("padding base64 inválido")
        padded = unpadded + b"=" * ((-len(unpadded)) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        return raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("parte base64 inválida") from exc


def encode_bearer(usuario: str, api_key: str) -> str:
    """Codifica identidad y API key en el valor del header ``Bearer``."""
    return f"{_encode_part(usuario)}.{_encode_part(api_key)}"


def decode_bearer(token: str) -> tuple[str, str]:
    """Decodifica el token a ``(usuario, api_key)`` o falla con ``ValueError``."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise ValueError("token Bearer inválido")
    usuario_raw, api_key_raw = token.split(".", 1)
    usuario = _decode_part(usuario_raw)
    api_key = _decode_part(api_key_raw)
    if not usuario or not api_key:
        raise ValueError("token Bearer inválido")
    return usuario, api_key


__all__ = ["encode_bearer", "decode_bearer"]
