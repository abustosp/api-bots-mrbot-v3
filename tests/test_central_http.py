"""Pruebas HTTP reales de central-api con TestClient (re-chequeo ROJO).

Cubre lo que antes fallaba por falta de dependencias (fastapi/pydantic
ausentes): la app central levanta, ``GET /health`` responde y
``GET /ready`` informa sin base de datos. Usa solo stdlib + fastapi.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from central_api.main import create_app


def _cliente() -> TestClient:
    """Crea un cliente de prueba sin arrancar el servidor real."""
    return TestClient(create_app(), raise_server_exceptions=True)


def test_health_ok():
    """La vivacidad responde sin dependencias externas."""
    cliente = _cliente()
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "ok"
    assert isinstance(cuerpo["version"], str)


def test_ready_sin_bd_informa_motivo():
    """Sin ``DATABASE_URL`` la disponibilidad es falsa con motivo público."""
    cliente = _cliente()
    respuesta = cliente.get("/ready")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["ready"] is False
    assert "reason" in cuerpo


def test_lista_bots_en_modo_desarrollo():
    """El catálogo público es visible sin clave en modo desarrollo."""
    cliente = _cliente()
    respuesta = cliente.get("/api/v3/bots")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert isinstance(cuerpo, dict)
