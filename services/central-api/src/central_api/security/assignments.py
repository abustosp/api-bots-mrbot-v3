"""Asignaciones firmadas central -> worker (Ed25519).

Garantía de ejecución: el worker solo acepta trabajo cuya firma produzca la
central con su clave privada de asignación. Sin esa firma (o vencida,
adulterada o de otra clave) el worker rechaza con 403/422 sin ejecutar nada.
Es la pieza que hace que el worker solo corra cuando lo llama la central,
aun si su puerto fuese alcanzable por terceros.

Formato canónico (idéntico en el verificador del worker; hay test de
interoperabilidad en ``tests/test_worker_noenv.py``)::

    mensaje = "alcance|job_id|attempt|lease_id|expires_at|sealed_hash"
    firma   = base64(ed25519(mensaje))

- ``alcance``: ``"assign"`` o ``"cancel"``.
- ``sealed_hash``: sha256 hex del ``blob_b64`` sellado, o ``"none"``.
- ``expires_at``: ISO-8601 UTC (el worker rechaza vencidas con tolerancia
  de 60 s de reloj).

La clave privada vive solo en la central (secreto ``ASSIGNMENT_SIGNING_KEY``;
en desarrollo se genera una efímera por proceso con aviso). La pública viaja
en la respuesta de registro y el worker la fija en memoria.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

log = logging.getLogger("central_api.assignments")

ASSIGNMENT_SCOPE_ASSIGN = "assign"
ASSIGNMENT_SCOPE_CANCEL = "cancel"
CLOCK_SKEW_SECONDS = 60


class AssignmentSignError(ValueError):
    """No pudo firmarse la asignación (clave inválida)."""


def canonical_message(
    scope: str,
    job_id: str,
    attempt: int,
    lease_id: str,
    expires_at: str,
    sealed_hash: str,
) -> bytes:
    return "|".join(
        [scope, job_id, str(attempt), lease_id, expires_at, sealed_hash]
    ).encode("utf-8")


def sealed_hash_of(sealed_section: dict | None) -> str:
    if not sealed_section:
        return "none"
    blob = str(sealed_section.get("blob_b64") or "")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_signing_key(pem: str) -> Ed25519PrivateKey:
    """Carga la privada Ed25519 (PEM PKCS8). Falla cerrado si no es válida."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise AssignmentSignError("ASSIGNMENT_SIGNING_KEY inválida") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AssignmentSignError("la clave debe ser Ed25519")
    return key


def ensure_signing_key(configured_pem: str) -> tuple[Ed25519PrivateKey, bool]:
    """Devuelve ``(privada, efimera)``. Sin clave configurada (vacía o
    ``placeholder`` de desarrollo) genera una por llamada y lo avisa: quien
    firme y quien entregue la pública deben compartirla vía
    :func:`process_signing_key`. Una clave presente pero inválida falla
    cerrado: en producción un secreto corrupto no debe degradar en silencio.
    """
    text = (configured_pem or "").strip()
    if not text or text == "placeholder":
        log.warning("ASSIGNMENT_SIGNING_KEY ausente: clave efímera de desarrollo")
        return Ed25519PrivateKey.generate(), True
    return load_signing_key(text), False


_signing_singleton: tuple[str, Ed25519PrivateKey, bool] | None = None


def process_signing_key(configured_pem: str) -> tuple[Ed25519PrivateKey, bool]:
    """Clave única por proceso: el registro y el dispatcher firman y publican
    con la misma. Se renueva solo si cambia la configuración."""
    global _signing_singleton
    if _signing_singleton is None or _signing_singleton[0] != (configured_pem or ""):
        private, ephemeral = ensure_signing_key(configured_pem or "")
        _signing_singleton = (configured_pem or "", private, ephemeral)
    assert _signing_singleton is not None
    return _signing_singleton[1], _signing_singleton[2]


def public_pem(private: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    return (
        private.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def sign_assignment(
    private: Ed25519PrivateKey,
    *,
    scope: str,
    job_id: str,
    attempt: int,
    lease_id: str,
    expires_at: str,
    sealed_hash: str,
) -> str:
    msg = canonical_message(scope, job_id, attempt, lease_id, expires_at, sealed_hash)
    return base64.b64encode(private.sign(msg)).decode("ascii")


def default_expiry(seconds: int = 300) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def verify_with_public_key(
    public: Ed25519PublicKey,
    *,
    scope: str,
    job_id: str,
    attempt: int,
    lease_id: str,
    expires_at: str,
    sealed_hash: str,
    signature_b64: str,
) -> bool:
    """Verificación del lado central (tests y doble chequeo). El worker usa su
    propio verificador; el formato es el mismo y hay test cruzado."""
    try:
        sig = base64.b64decode(signature_b64)
        msg = canonical_message(scope, job_id, attempt, lease_id, expires_at, sealed_hash)
        public.verify(sig, msg)
    except (InvalidSignature, ValueError):
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return exp + timedelta(seconds=CLOCK_SKEW_SECONDS) >= now


__all__ = [
    "ASSIGNMENT_SCOPE_ASSIGN",
    "ASSIGNMENT_SCOPE_CANCEL",
    "CLOCK_SKEW_SECONDS",
    "AssignmentSignError",
    "canonical_message",
    "sealed_hash_of",
    "load_signing_key",
    "ensure_signing_key",
    "process_signing_key",
    "public_pem",
    "sign_assignment",
    "default_expiry",
    "verify_with_public_key",
]
