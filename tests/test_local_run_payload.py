"""Regresiones del runner local de bots (``tools/local_run/run_bot.py``).

El runner es herramienta de desarrollo, no de produccion: sus pruebas viven en
la suite de la raiz y no en la del worker (que no debe importar ``tools``).
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from tools.local_run.run_bot import _payload_desde_fila  # noqa: E402


def test_payload_desde_fila_mapea_campos_legacy_al_esquema_v3() -> None:
    """``nombre_representado`` y ``AAAAMMDD`` de la tabla V1 al payload V3."""
    propiedades = {"representado_nombre", "fecha_desde", "fecha_hasta"}

    payload = _payload_desde_fila(
        {
            "nombre_representado": "ENTIDAD DE PRUEBA",
            "desde": "20260901",
            "hasta": "20260930",
        },
        propiedades,
    )

    assert payload["representado_nombre"] == "ENTIDAD DE PRUEBA"
    assert payload["fecha_desde"] == "01/09/2026"
    assert payload["fecha_hasta"] == "30/09/2026"


def test_payload_desde_fila_mapea_periodo_desde_de_mis_retenciones() -> None:
    """La tabla V1 de Mis Retenciones llama periodo_* a fechas DD/MM/AAAA."""
    propiedades = {"representado_cuit", "representado_nombre", "fecha_desde", "fecha_hasta"}

    payload = _payload_desde_fila(
        {
            "denominacion": "ENTIDAD DE PRUEBA",
            "periodo_desde": "01/09/2026",
            "periodo_hasta": "30/09/2026",
        },
        propiedades,
        representado="20123456786",
    )

    assert payload["representado_cuit"] == "20123456786"
    assert payload["representado_nombre"] == "ENTIDAD DE PRUEBA"
    assert payload["fecha_desde"] == "01/09/2026"
    assert payload["fecha_hasta"] == "30/09/2026"


def test_payload_desde_fila_deriva_cuits_consulta_y_formatos() -> None:
    propiedades = {"cuits_consulta", "excel", "pdf"}

    payload = _payload_desde_fila(
        {
            "cuits_consultados": "20111111112, 20222222223",
            "request_excel": 1,
            "request_pdf": 0,
        },
        propiedades,
    )

    assert payload["cuits_consulta"] == ["20111111112", "20222222223"]
    assert payload["excel"] is True
    assert "pdf" not in payload


def test_payload_hacienda_deriva_denominacion_sin_pisar_el_canonico() -> None:
    fila = {"nombre_representado": "ENTIDAD LEGACY"}
    assert _payload_desde_fila(fila, {"denominacion"}) == {
        "denominacion": "ENTIDAD LEGACY"
    }
    fila["denominacion"] = "ENTIDAD CANONICA"
    assert _payload_desde_fila(fila, {"denominacion"}) == {
        "denominacion": "ENTIDAD CANONICA"
    }
