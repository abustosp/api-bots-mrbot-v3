"""Re-chequeo con dependencias reales: contratos y sobre sellado.

Antes daban ROJO por falta de ``pydantic``/``cryptography``. Verifica
versión de protocolo entera 1, compatibilidad, redacción de secretos en
representaciones y aislamiento del worker (W-1, sin ORM ni drivers).
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import ValidationError

RAIZ = Path(__file__).resolve().parent.parent


def test_protocolo_entero_1_y_compatible():
    """La versión normativa es el entero 1 y solo acepta el 1."""
    from mrbot_contracts.version import (
        PROTOCOL_VERSION,
        is_compatible,
    )

    assert PROTOCOL_VERSION == 1
    assert isinstance(PROTOCOL_VERSION, int)
    assert is_compatible(1) is True
    assert is_compatible(2) is False


def test_mensaje_base_rechaza_extras():
    """Todo mensaje top-level rechaza campos no declarados."""
    from mrbot_contracts.version import ProtocolMessage

    with pytest.raises(ValidationError):
        ProtocolMessage(protocol_version=1, campo_inesperado="x")


def test_sobre_sellado_roundtrip_y_falla_cerrada():
    """El sobre se abre con la privada y los errores son cerrados."""
    from bot_worker.runtime import sealed as s

    privada, publica = s.generate_sealed_keypair()
    seccion = {"credentials": {"cuit": "20-12345678-9"}}
    cruda = json.dumps(seccion).encode("utf-8")
    pub = serialization.load_pem_public_key(publica.encode("ascii"))
    clave = Fernet.generate_key()
    enc_clave = pub.encrypt(
        clave,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    sobre = {
        "alg": "RSA-OAEP-SHA256+Fernet",
        "enc_key_b64": base64.b64encode(enc_clave).decode("ascii"),
        "blob_b64": base64.b64encode(Fernet(clave).encrypt(cruda)).decode("ascii"),
    }
    assert s.decrypt_sealed_section(privada, sobre) == seccion
    with pytest.raises(s.SealedEnvelopeError):
        s.decrypt_sealed_section(privada, {"alg": "otro"})
    with pytest.raises(s.SealedEnvelopeError):
        s.decrypt_sealed_section(privada, dict(sobre, blob_b64="!!no-b64!!"))


def test_tope_5_en_marca_y_ajustes():
    """La capacidad máxima es 5 en central y worker."""
    from central_api.internal.workers import MAX_WORKER_CAPACITY
    from central_api.settings import get_settings

    assert MAX_WORKER_CAPACITY == 5
    texto = (RAIZ / "services/central-api/src/central_api/settings.py").read_text(
        encoding="utf-8"
    )
    assert "worker_capacity: int = 5" in texto


def test_worker_sin_dependencias_de_datos():
    """El worker no importa ORM ni drivers de base de datos (W-1)."""
    prohibidos = ("sqlalchemy", "asyncpg", "psycopg", "create_engine")
    fugas = []
    for ruta in (RAIZ / "services/bot-worker/src").rglob("*.py"):
        if "__pycache__" in ruta.parts:
            continue
        texto = ruta.read_text(encoding="utf-8")
        for palabra in prohibidos:
            if palabra in texto:
                fugas.append(f"{ruta.name}: {palabra}")
    assert fugas == []


def test_credenciales_no_fugan_en_repr():
    """Las representaciones de contexto redactan secretos."""
    from bot_worker.runtime.context import FiscalCredentials, ProxyConfig

    creds = FiscalCredentials(cuit_representante="20-1-9", clave="secreta")
    assert "secreta" not in repr(creds)
    proxy = ProxyConfig(mode="per-job", host="h", password="pw")
    assert "pw" not in repr(proxy)
