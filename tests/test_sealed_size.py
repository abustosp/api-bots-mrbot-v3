from __future__ import annotations

import pytest

from central_api.security.sealed import (
    MAX_SEALED_SECTION_BYTES as CENTRAL_LIMIT,
    SealedSectionError,
    encrypt_sealed_section,
)
from bot_worker.runtime.sealed import (
    MAX_SEALED_SECTION_BYTES as WORKER_LIMIT,
    SealedEnvelopeError,
    decrypt_sealed_section,
    generate_sealed_keypair,
)


def test_limite_sellado_coincide_y_permite_cache_apoc_grande():
    private_key, public_key = generate_sealed_keypair()
    section = {"apoc_table": "x" * (1_700_000 - len('{"apoc_table":""}'))}
    assert CENTRAL_LIMIT == WORKER_LIMIT == 4_194_304
    sealed = encrypt_sealed_section(public_key, section)
    assert decrypt_sealed_section(private_key, sealed) == section


def test_central_rechaza_apenas_por_encima_del_limite():
    _, public_key = generate_sealed_keypair()
    # El JSON añade 8 bytes a la cadena de valor.
    section = {"x": "x" * (CENTRAL_LIMIT - 7)}
    with pytest.raises(SealedSectionError, match="excede 4 MiB"):
        encrypt_sealed_section(public_key, section)


def test_worker_rechaza_sobre_cifrado_que_supera_limite():
    private_key, public_key = generate_sealed_keypair()
    # Se cifra directamente para simular un emisor que evita la validación central.
    from cryptography.fernet import Fernet
    import base64
    import json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = Fernet.generate_key()
    pub = serialization.load_pem_public_key(public_key.encode())
    enc_key = pub.encrypt(
        key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    raw = json.dumps({"x": "x" * (WORKER_LIMIT - 7)}, separators=(",", ":")).encode()
    sealed = {
        "alg": "RSA-OAEP-SHA256+Fernet",
        "enc_key_b64": base64.b64encode(enc_key).decode(),
        "blob_b64": base64.b64encode(Fernet(key).encrypt(raw)).decode(),
    }
    with pytest.raises(SealedEnvelopeError, match="excede 4 MiB"):
        decrypt_sealed_section(private_key, sealed)
