"""Verificación de asignaciones firmadas (lado worker, Ed25519).

El worker solo ejecuta lo que la central firmó: cada ``POST /jobs`` y cada
cancelación trae ``assignment_signature`` + ``assignment_expires_at`` y se
rechaza sin ejecutar si la firma no verifica con la clave pública que la
central entregó en el registro (fijada en memoria), si venció o si el
``sealed_hash`` no coincide con la sección sellada recibida (impide
trasplantar secciones selladas entre asignaciones).

El formato canónico es el mismo que firma la central
(``central_api.security.assignments``); hay test de interoperabilidad en
``tests/test_worker_noenv.py``. Sin clave fijada (registro pendiente) todo
se rechaza: falla cerrado.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CLOCK_SKEW_SECONDS = 60

#: Alcance de una asignación forzada desde el panel: la única vía que puede
#: superar el cupo del worker. Viaja firmado dentro del mensaje canónico.
ASSIGNMENT_SCOPE_ASSIGN_FORCE = "assign-force"


class AssignmentDenied(ValueError):
    """La asignación no es ejecutable. Sin detalle del porqué hacia afuera."""


def _message(
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


def load_verify_key(public_pem: str) -> Ed25519PublicKey:
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(public_pem.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise AssignmentDenied("clave de central inválida") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AssignmentDenied("clave de central inválida")
    return key


def verify_assignment(
    public_key: Ed25519PublicKey | None,
    *,
    scope: str,
    job_id: str,
    attempt: int,
    lease_id: str,
    expires_at: str,
    sealed_section: dict | None,
    signature_b64: str,
) -> None:
    """Verifica firma, vigencia y ligadura con la sección sellada.

    Lanza :class:`AssignmentDenied` ante cualquier falla (sin clave fijada,
    firma inválida, vencimiento o hash dispar).
    """
    if public_key is None:
        raise AssignmentDenied("sin clave de central fijada")
    if not signature_b64:
        raise AssignmentDenied("sin firma de asignación")
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise AssignmentDenied("vencimiento inválido") from None
    now = datetime.now(timezone.utc)
    if exp.tzinfo is None:
        raise AssignmentDenied("vencimiento sin zona horaria")
    if exp + timedelta(seconds=CLOCK_SKEW_SECONDS) < now:
        raise AssignmentDenied("asignación vencida")
    msg = _message(scope, job_id, attempt, lease_id, expires_at, sealed_hash_of(sealed_section))
    try:
        public_key.verify(base64.b64decode(signature_b64), msg)
    except (InvalidSignature, ValueError) as exc:
        raise AssignmentDenied("firma inválida") from exc


__all__ = [
    "CLOCK_SKEW_SECONDS",
    "ASSIGNMENT_SCOPE_ASSIGN_FORCE",
    "AssignmentDenied",
    "sealed_hash_of",
    "load_verify_key",
    "verify_assignment",
]
