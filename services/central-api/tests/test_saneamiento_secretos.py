"""Frontera de secretos: claves, valores embebidos y códigos de respuesta.

Cubre las tres capas que impiden que un secreto termine visible:

* ``assert_no_secretos`` (persistencia) rechaza claves sensibles con variantes
  de formato (``apiKey``, ``API-KEY``, ``authorization_header``);
* ``sanear_valor_texto`` (panel) oculta URLs completas y credenciales que
  viajan dentro de un valor cuya clave es inocua;
* ``POST /api/v3/bots/{bot}/{operacion}`` responde 422 (no 503) cuando el
  propio cliente manda material sensible, porque no es una caída reintentable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin._common import (  # noqa: E402
    MARCA_SECRETO,
    MARCA_URL_OCULTA,
    redactar_metadata,
    sanear_valor_texto,
)
from central_api.main import create_app  # noqa: E402
from central_api.repositories.base import RepositoryError, assert_no_secretos  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


@pytest.fixture
def entorno_limpio(monkeypatch):
    """Sin DATABASE_URL ni secretos: modo desarrollo en memoria."""
    for var in (
        "API_KEY_HMAC_SECRET",
        "DATABASE_URL",
        "ADMIN_TOKEN",
        "SEALED_PRIVATE_KEY_PATH",
        "S3_ENDPOINT",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "clave",
    [
        "apiKey",
        "API-KEY",
        "api key",
        "usar_api_key_v2",
        "Authorization",
        "authorization_header",
        "Authorization-Header",
        "session_token",
        "BOT_TOKEN",
        "refreshToken",
        "client_secret",
        "myPrivateKey",
        "x-passwd",
        "presigned_url_descarga",
        "uploadUrl",
        "sealed_section_2",
        "cookie_sesion",
    ],
)
def test_assert_no_secretos_rechaza_variantes_de_formato(clave: str) -> None:
    """El nombre de la clave no puede evadir la frontera cambiando de formato."""
    with pytest.raises(RepositoryError, match="contiene"):
        assert_no_secretos({"nivel": [{clave: "x"}]}, "request_payload")


@pytest.mark.parametrize(
    "clave",
    [
        "security",
        "seguridad",
        "secuencia",
        "segundo_titular",
        "clave_fiscal_descripcion",
        "sin_clave",
        "incluir_token_fiscal",
        "cuit",
        "fecha_desde",
    ],
)
def test_assert_no_secretos_conserva_claves_benignas(clave: str) -> None:
    """Ni ``security`` ni compuestos legítimos del dominio fiscal se rechazan."""
    assert_no_secretos({clave: "valor legítimo"}, "request_payload")


def test_sanear_valor_texto_oculta_url_completa() -> None:
    """Una URL prefirmada se oculta entera: lleva host, bucket y firma."""
    prefirmada = (
        "https://minio.interno:9000/mrbot/out.pdf"
        "?X-Amz-Signature=abc123&X-Amz-Credential=clave"
    )
    assert sanear_valor_texto(prefirmada) == MARCA_URL_OCULTA
    assert sanear_valor_texto("mirar www.ejemplo.invalid/path") == MARCA_URL_OCULTA


def test_sanear_valor_texto_enmascara_credencial_embebida() -> None:
    """La clave ``notas`` es inocua, pero el valor no puede llevar un bearer."""
    casos = {
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc": "Authorization",
        "usar api_key=sk-live-123 y reintentar": "api_key",
        "token: eyJhbGciOiJIUzI1NiJ9": "token",
        "clave=1234567890": "clave",
        "Bearer abcdef123456": "Bearer",
    }
    for original, pista in casos.items():
        limpio = sanear_valor_texto(original)
        assert MARCA_SECRETO in limpio, original
        assert pista.split(":")[0].split("=")[0] in limpio, original
        assert "eyJhbGciOiJIUzI1NiJ9" not in limpio, original
        assert "sk-live-123" not in limpio, original
        assert "1234567890" not in limpio, original


def test_sanear_valor_texto_conserva_texto_benigno() -> None:
    """Sin URL ni credencial reconocible el valor viaja intacto."""
    for texto in ("consulta de deuda", "2026-01-01", "CUIT 20111111112", ""):
        assert sanear_valor_texto(texto) == texto


def test_redactar_metadata_aplica_clave_y_valor() -> None:
    """La metadata de auditoría se limpia por clave y también por valor."""
    entrada = {
        "bot": "consulta_cuit",
        "clave_fiscal": "no debe salir",
        "notas": "Bearer abcdef123456",
        "destino": "https://minio.interno/bucket/objeto?X-Amz-Signature=zzz",
        "anidado": [{"password": "x"}, {"detalle": "token=abc"}],
    }
    salida = redactar_metadata(entrada)
    assert salida["bot"] == "consulta_cuit"
    assert salida["clave_fiscal"] == "[REDACTED]"
    assert salida["notas"] == f"Bearer {MARCA_SECRETO}"
    assert salida["destino"] == MARCA_URL_OCULTA
    assert salida["anidado"][0]["password"] == "[REDACTED]"
    assert salida["anidado"][1]["detalle"] == f"token={MARCA_SECRETO}"
    assert "no debe salir" not in str(salida)


def test_submit_job_rechaza_payload_con_secreto_con_422(entorno_limpio) -> None:
    """Material sensible del cliente es defecto de entrada, no caída de servicio."""
    cliente = TestClient(create_app())
    respuesta = cliente.post(
        "/api/v3/bots/consulta_cuit/consultar",
        json={"payload": {"apiKey": "sk-live-123"}, "credentials": {}},
    )
    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert detalle["error_code"] == "validation"
    assert detalle["message"] == "Datos de entrada inválidos."
    assert detalle["correlation_id"]
    assert "sk-live-123" not in respuesta.text
    assert "use el sobre sellado" not in respuesta.text


def test_submit_job_acepta_payload_benigno(entorno_limpio) -> None:
    """Control: el mismo endpoint sigue aceptando un payload legítimo."""
    cliente = TestClient(create_app())
    respuesta = cliente.post(
        "/api/v3/bots/consulta_cuit/consultar",
        json={"payload": {"security": "alta"}, "credentials": {}},
    )
    assert respuesta.status_code == 202
    assert respuesta.json()["status"] == "PENDIENTE"
