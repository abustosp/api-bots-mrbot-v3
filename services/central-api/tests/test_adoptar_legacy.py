from __future__ import annotations

from central_api.tools.adoptar_legacy import (
    clasificar_bot,
    clave_idempotencia,
    con_sufijo,
    extraer_cuit,
    operacion,
)


def test_extraer_cuit():
    assert extraer_cuit("ccma_20123456789_20240101.pdf") == "20123456789"
    assert extraer_cuit("F8010.27207526199.HSHS.pdf") == "27207526199"
    assert extraer_cuit("srt_alicuotas.json") == "s-cuit"


def test_clasificar_bot():
    assert clasificar_bot("ccma", "ccma_x.pdf", "g") == "ccma"
    assert clasificar_bot("ccma", "ccma_compensacion_x.pdf", "g") == "ccma_compensacion"
    assert clasificar_bot("vep", "pagos_vep_1.csv", "g") == "consulta_pagos_vep"
    assert clasificar_bot("vep", "x - VEP CCMA.pdf", "g") == "vep_ccma"
    assert clasificar_bot("misretenciones", "a IVA-SIMPLE b.csv", "g") == \
        "mis_retenciones_iva_simple"
    assert clasificar_bot("misretenciones", "retenciones.csv", "g") == "mis_retenciones"
    assert clasificar_bot("ccma", "jobs/u/ccma_x.pdf", "g") is None
    assert clasificar_bot("ccma", "_migraciones/x.json", "g") is None
    assert clasificar_bot("g", "sct_compensaciones_1.csv", "g") == "compensaciones"
    assert clasificar_bot("g", "CPG - x.xlsx", "g") == "certificacion_primaria_granos"
    assert clasificar_bot("g", "cosa_rara.bin", "g") is None
    assert clasificar_bot("miscomprobantes", "0 - MCE - x.zip", "g") == "mis_comprobantes"
    assert clasificar_bot("portaliva", "0 - x - compras.zip", "g") == "portal_iva"
    assert clasificar_bot("miscomprobantes", "0 - MCE - x.zip", "miscomprobantes") == \
        "mis_comprobantes"
    assert clasificar_bot("miscomprobantes", "sct_compensaciones_x.csv",
                          "miscomprobantes") == "compensaciones"


def test_operacion_y_clave():
    ops = {"srt": ["consultar_alicuotas"], "sct": ["consultar", "descargar"]}
    assert operacion("srt", ops) == "consultar_alicuotas"
    assert operacion("sct", ops) == "consultar"
    assert operacion("inexistente", ops) is None
    assert clave_idempotencia("srt", "s-cuit") == "legacy-adopt-srt-s-cuit"
    assert con_sufijo("a.csv", 2) == "a-2.csv"
