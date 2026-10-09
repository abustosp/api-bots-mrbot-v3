"""Esquema de declaracion_en_linea: campos de V1, periodos y rango invertido.

V1 (``bot_declaracion_en_linea``) acepta ``cuit_representado``,
``representado_nombre``, ``periodo_desde``/``periodo_hasta`` en varios formatos
(``AAAAMM``, ``MM/AAAA``, ``AAAA-MM``) y ``carga_minio``; si el rango viene
invertido lo ordena en lugar de rechazarlo.
"""

from __future__ import annotations

import asyncio

import pytest

from bot_worker.bots.declaracion_en_linea.plugin import DeclaracionEnLineaPlugin
from bot_worker.bots.declaracion_en_linea.schema import (
    DeclaracionEnLineaConsultarInput,
    esquema_entrada,
    generar_rango_periodos,
)
from bot_worker.bots.errors import InvalidInputError

CUIT = "20123456789"


def _validar(**extra: object) -> DeclaracionEnLineaConsultarInput:
    base = {
        "operacion": "consultar",
        "periodo_desde": "202503",
        "periodo_hasta": "202504",
    }
    base.update(extra)
    _, entrada = asyncio.run(DeclaracionEnLineaPlugin().validate(base))
    return entrada


def test_acepta_los_nombres_de_v1() -> None:
    entrada = _validar(
        cuit_representado="20-12345678-9",
        representado_nombre="EMPRESA FICTICIA SA",
        carga_minio=False,
        proxy_request=True,
        cuit_representante="27123456789",
        clave_representante="secreta",
    )

    assert entrada.representado_cuit == CUIT
    assert entrada.representado_nombre == "EMPRESA FICTICIA SA"
    assert entrada.subir_archivos is False


def test_sin_representado_queda_none_para_usar_el_representante() -> None:
    entrada = _validar()

    assert entrada.representado_cuit is None


def test_normaliza_los_formatos_de_periodo_de_v1() -> None:
    assert _validar(periodo_desde="03/2025", periodo_hasta="04/2025").periodo_desde == "202503"
    assert _validar(periodo_desde="2025-03", periodo_hasta="2025-04").periodo_hasta == "202504"
    assert _validar(periodo_desde="2025/03", periodo_hasta="2025/04").periodo_desde == "202503"


def test_ordena_el_rango_invertido_como_v1() -> None:
    entrada = _validar(periodo_desde="202506", periodo_hasta="202503")

    assert (entrada.periodo_desde, entrada.periodo_hasta) == ("202503", "202506")
    assert generar_rango_periodos(entrada.periodo_desde, entrada.periodo_hasta) == [
        "202503",
        "202504",
        "202505",
        "202506",
    ]


def test_rechaza_un_periodo_invalido() -> None:
    with pytest.raises(InvalidInputError):
        _validar(periodo_desde="marzo", periodo_hasta="202504")
    with pytest.raises(InvalidInputError):
        _validar(periodo_desde="202513", periodo_hasta="202601")


def test_exige_al_menos_una_salida() -> None:
    with pytest.raises(InvalidInputError):
        _validar(incluir_json=False, subir_archivos=False)


def test_el_json_schema_documenta_los_alias_de_v1() -> None:
    propiedades = esquema_entrada()["properties"]

    assert {"representado_cuit", "cuit", "cuit_representado"} <= set(propiedades)
    assert {"periodo_desde", "periodo_hasta", "carga_minio"} <= set(propiedades)


def test_nombres_de_archivo_por_periodo_como_v1() -> None:
    from bot_worker.bots.declaracion_en_linea.plugin import nombre_archivo_periodo

    assert nombre_archivo_periodo(CUIT, "DDJJ", "202503", "EMPRESA SA", ".pdf") == (
        f"{CUIT[-1]} - {CUIT} - Declaracion en linea - DDJJ - 202503 - EMPRESA SA.pdf"
    )
    assert nombre_archivo_periodo(CUIT, "DDJJ Cuiles", "202503", "EMPRESA SA", ".xls") == (
        f"{CUIT[-1]} - {CUIT} - Declaracion en linea - DDJJ Cuiles - 202503 - EMPRESA SA.xls"
    )
    # Sin nombre del representado, V1 omite el tramo final.
    assert nombre_archivo_periodo(CUIT, "VEP", "202503", "", ".pdf") == (
        f"{CUIT[-1]} - {CUIT} - Declaracion en linea - VEP - 202503.pdf"
    )
    # Los separadores de ruta se sanean como en V1.
    assert nombre_archivo_periodo(CUIT, "DDJJ", "202503", "A/B", ".pdf").endswith(
        "202503 - A_B.pdf"
    )
