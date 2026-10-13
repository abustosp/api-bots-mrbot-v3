"""Paridad del filtro de Mis Comprobantes con V1 (``comprobantes_bot.py``).

V1 renombra la planilla del portal por posición (``mce_xlsx``/``mcr_xlsx``),
rellena con 0 los importes que el portal no informa, recorta el rango y recién
después agrega las columnas auxiliares (``MC``, ``CUIT Cliente``, ``MC AUX`` y
``Factura AUX``) al JSON que devuelve la API. Estas pruebas fijan ese contrato
sobre una planilla de 28 columnas (emitidos) y otra de 30 (recibidos), que es
el formato real del botón ``CSV``.
"""

from __future__ import annotations

import base64
import csv
import io
from pathlib import Path
from types import SimpleNamespace

from bot_worker.bots.comprobantes.plugin import (
    ComprobantesPlugin,
    filtrar_csv_por_rango,
)
from bot_worker.bots.mis_comprobantes.plugin import MisComprobantesPlugin
from bot_worker.bots.planillas import (
    COLUMNAS_MCE,
    COLUMNAS_MCR,
    leer_muestra_mis_comprobantes,
)

CUIT = "20320160856"
# Encabezado real del ZIP de ARCA (``mce_zip``/``mcr_zip`` en V1).
ENCABEZADO_MCE = [
    "Fecha de Emisión",
    "Tipo de Comprobante",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Cód. Autorización",
    "Tipo Doc. Receptor",
    "Nro. Doc. Receptor",
    "Denominación Receptor",
    "Tipo Cambio",
    "Moneda",
    "Imp. Neto Gravado IVA 0%",
    "IVA 2,5%",
    "Imp. Neto Gravado IVA 2,5%",
    "IVA 5%",
    "Imp. Neto Gravado IVA 5%",
    "IVA 10,5%",
    "Imp. Neto Gravado IVA 10,5%",
    "IVA 21%",
    "Imp. Neto Gravado IVA 21%",
    "IVA 27%",
    "Imp. Neto Gravado IVA 27%",
    "Imp. Neto Gravado Total",
    "Imp. Neto No Gravado",
    "Imp. Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
]
ENCABEZADO_MCR = [
    "Fecha de Emisión",
    "Tipo de Comprobante",
    "Punto de Venta",
    "Número Desde",
    "Número Hasta",
    "Cód. Autorización",
    "Tipo Doc. Emisor",
    "Nro. Doc. Emisor",
    "Denominación Emisor",
    "Tipo Doc. Receptor",
    "Nro. Doc. Receptor",
    "Tipo Cambio",
    "Moneda",
    "Imp. Neto Gravado IVA 0%",
    "IVA 2,5%",
    "Imp. Neto Gravado IVA 2,5%",
    "IVA 5%",
    "Imp. Neto Gravado IVA 5%",
    "IVA 10,5%",
    "Imp. Neto Gravado IVA 10,5%",
    "IVA 21%",
    "Imp. Neto Gravado IVA 21%",
    "IVA 27%",
    "Imp. Neto Gravado IVA 27%",
    "Imp. Neto Gravado Total",
    "Imp. Neto No Gravado",
    "Imp. Op. Exentas",
    "Otros Tributos",
    "Total IVA",
    "Imp. Total",
]

_MCE_EXTRA = {
    "Tipo Doc. Receptor": "80",
    "Nro. Doc. Receptor": "20123456789",
    "Denominación Receptor": "CLIENTE SA",
}
_MCR_EXTRA = {
    "Tipo Doc. Emisor": "80",
    "Nro. Doc. Emisor": "30500000001",
    "Denominación Emisor": "PROVEEDOR SA",
    "Tipo Doc. Receptor": "80",
    "Nro. Doc. Receptor": "20320160856",
}


def _planilla(encabezado: list[str], filas: list[dict[str, str]], extra: dict[str, str]) -> str:
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(encabezado)
    for fila in filas:
        valores = dict(extra)
        valores.update(fila)
        escritor.writerow([valores.get(columna, "") for columna in encabezado])
    return buffer.getvalue()


def _fila_mce(fecha: str, total: str) -> dict[str, str]:
    return {
        "Fecha de Emisión": fecha,
        "Tipo de Comprobante": "1",
        "Punto de Venta": "00001",
        "Número Desde": "00000101",
        "Número Hasta": "00000101",
        "Cód. Autorización": "75123456789012",
        "Tipo Cambio": "1",
        "Moneda": "PES",
        "Imp. Neto Gravado IVA 21%": "1000,00",
        "IVA 21%": "210,00",
        "Imp. Total": total,
    }


def _fila_mcr(fecha: str, total: str) -> dict[str, str]:
    """Fila de recibidos: mismo layout MCR que arma el portal."""
    return _fila_mce(fecha, total)


def test_filtrar_emitidos_renombra_por_posicion_y_rellena_importes(tmp_path: Path) -> None:
    origen = tmp_path / "MCE.csv"
    destino = tmp_path / "filtrado.csv"
    origen.write_text(
        _planilla(
            ENCABEZADO_MCE,
            [_fila_mce("2025-12-31", "1210,00"), _fila_mce("2026-01-10", "1210,00")],
            _MCE_EXTRA,
        ),
        encoding="utf-8",
    )

    filas = filtrar_csv_por_rango(origen, destino, "01/01/2026", "31/01/2026", "emitidos")

    assert filas == 1
    with open(destino, encoding="utf-8", newline="") as fh:
        leidas = list(csv.reader(fh, delimiter=";"))
    assert leidas[0] == list(COLUMNAS_MCE)
    fila = dict(zip(leidas[0], leidas[1]))
    # Fecha ISO, números sin ceros a la izquierda e importes con punto decimal.
    assert fila["Fecha"] == "2026-01-10"
    assert fila["Punto de Venta"] == "1"
    assert fila["Número Desde"] == "101"
    assert fila["Neto Grav. IVA 21%"] == "1000.0"
    assert fila["IVA 21%"] == "210.0"
    assert fila["Imp. Total"] == "1210.0"
    # Los importes que el portal no informa quedan en 0, como ``fillna(0)``.
    assert fila["Neto Grav. IVA 0%"] == "0.0"
    assert fila["Neto No Gravado"] == "0.0"
    # Las columnas auxiliares nunca se escriben en el CSV (V1 las agrega al JSON).
    assert "MC AUX" not in leidas[0]


def test_filtrar_recibidos_usa_las_columnas_mcr(tmp_path: Path) -> None:
    origen = tmp_path / "MCR.csv"
    destino = tmp_path / "filtrado.csv"
    origen.write_text(
        _planilla(ENCABEZADO_MCR, [_fila_mcr("2026-01-10", "100,00")], _MCR_EXTRA),
        encoding="utf-8",
    )

    filas = filtrar_csv_por_rango(origen, destino, "01/01/2026", "31/01/2026", "recibidos")

    assert filas == 1
    with open(destino, encoding="utf-8", newline="") as fh:
        leidas = list(csv.reader(fh, delimiter=";"))
    assert leidas[0] == list(COLUMNAS_MCR)
    assert "Nro. Doc. Emisor" in leidas[0]


def test_la_muestra_agrega_las_columnas_auxiliares_de_v1(tmp_path: Path) -> None:
    origen = tmp_path / "MCE.csv"
    destino = tmp_path / "filtrado.csv"
    origen.write_text(
        _planilla(ENCABEZADO_MCE, [_fila_mce("2026-01-10", "1210,00")], _MCE_EXTRA),
        encoding="utf-8",
    )
    filtrar_csv_por_rango(origen, destino, "01/01/2026", "31/01/2026", "emitidos")

    muestra = leer_muestra_mis_comprobantes(destino, "emitidos", CUIT, 5)
    fila = muestra[0]

    assert fila["MC"] == "MCE"
    assert fila["CUIT Cliente"] == CUIT
    assert fila["MC AUX"] == f"{CUIT}-1-1-101-20123456789-{CUIT}"
    assert fila["Factura AUX"] == f"{CUIT}-1-1-101"

    origen_r = tmp_path / "MCR.csv"
    destino_r = tmp_path / "filtrado_r.csv"
    origen_r.write_text(
        _planilla(ENCABEZADO_MCR, [_fila_mcr("2026-01-10", "100,00")], _MCR_EXTRA),
        encoding="utf-8",
    )
    filtrar_csv_por_rango(origen_r, destino_r, "01/01/2026", "31/01/2026", "recibidos")

    fila_r = leer_muestra_mis_comprobantes(destino_r, "recibidos", CUIT, 5)[0]
    assert fila_r["MC"] == "MCR"
    assert fila_r["MC AUX"] == f"{CUIT}-1-1-101-20320160856-30500000001"
    # V1 deja ``NaN`` en Factura AUX para recibidos.
    assert fila_r["Factura AUX"] == "nan"


def test_el_esquema_acepta_los_campos_de_v1_del_filtro() -> None:
    """Puntos de venta, ``timeout_mc``, ``carga_b64`` y los aliases de V1."""
    for plugin_cls in (ComprobantesPlugin, MisComprobantesPlugin):
        plugin = plugin_cls()
        import asyncio

        operacion, entrada = asyncio.run(
            plugin.validate(
                {
                    "operacion": "consultar",
                    "cuit": "20-32016085-6",
                    "representado_nombre": "Empresa",
                    "desde": "01/01/2026",
                    "hasta": "31/01/2026",
                    "descarga_emitidos": True,
                    "descarga_recibidos": True,
                    "puntos_venta_emitidos": ["1", "002"],
                    "puntos_venta_recibidos": [3],
                    "timeout_mc": 45,
                    "carga_b64": True,
                    "carga_json": True,
                    "carga_minio": True,
                    "proxy_request": False,
                    "eliminar_descargas": False,
                }
            )
        )

        assert operacion == "consultar"
        assert entrada.emitidos and entrada.recibidos
        # Los puntos de venta se conservan como texto (ceros a la izquierda).
        assert entrada.puntos_venta("emitidos") == ["1", "002"]
        assert entrada.puntos_venta("recibidos") == ["3"]
        assert entrada.timeout_mc == 45
        assert entrada.carga_b64 is True
        assert entrada.incluir_json is True
        assert entrada.subir_csv is True


def test_el_esquema_permite_solo_base64_como_salida() -> None:
    import asyncio

    entrada = asyncio.run(
        ComprobantesPlugin().validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT,
                "representado_nombre": "Empresa",
                "fecha_desde": "01/01/2026",
                "fecha_hasta": "31/01/2026",
                "emitidos": True,
                "incluir_json": False,
                "subir_csv": False,
                "carga_b64": True,
            }
        )
    )

    assert entrada[1].carga_b64 is True


def test_carga_b64_se_acepta_pero_el_resultado_nunca_trae_base64() -> None:
    """``carga_b64`` es un flag historico: se ignora y el archivo sale por ``files[]``."""
    import asyncio
    import zipfile

    from bot_worker.runtime.context import ArtifactStore, BotRuntime, CancellationToken

    class _Servicio:
        async def seleccionar_representado(self, cuit: str) -> None:
            return None

        async def descargar_csv(
            self, *, tipo: str, destino: Path, desde: str, hasta: str, **kwargs: Any
        ) -> None:
            with zipfile.ZipFile(destino, "w") as paquete:
                paquete.writestr("consulta.csv", "Fecha;Tipo\n2026-01-10;1\n")

    class _Sesion:
        async def login(self) -> None:
            return None

        async def open_service(self, servicio: str, **kwargs: Any) -> Any:
            return _Servicio()

    class _Fabrica:
        def arca_session(self, **kwargs: Any) -> Any:
            sesion = _Sesion()

            class _Contexto:
                async def __aenter__(self) -> _Sesion:
                    return sesion

                async def __aexit__(self, *args: Any) -> bool:
                    return False

            return _Contexto()

    class _Sink:
        async def progress(self, **kwargs: Any) -> None:
            return None

    async def _presign(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"upload_url": "", "object_key": "jobs/x/emitidos_csv"}

    async def _ejecutar(tmp: Path) -> Any:
        plugin = ComprobantesPlugin(muestra_json=1)
        entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT,
                "representado_nombre": "Empresa",
                "fecha_desde": "01/01/2026",
                "fecha_hasta": "31/01/2026",
                "emitidos": True,
                "incluir_json": True,
                "subir_csv": False,
                "carga_b64": True,
            }
        )
        runtime = BotRuntime(
            job_id="job-b64",
            work_dir=tmp,
            deadline=SimpleNamespace(remaining_seconds=lambda: 60),
            credentials=SimpleNamespace(clave="clave-ficticia"),
            proxy=None,
            artifact_store=ArtifactStore(tmp, slots={}, presign=_presign),
            event_sink=_Sink(),
            browser_factory=_Fabrica(),
            cancellation=CancellationToken(),
        )
        return await plugin.execute(entrada, runtime)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        resultado = asyncio.run(_ejecutar(Path(tmp)))

    assert resultado.result == "OK"
    resumen = resultado.data["emitidos"]
    assert "archivo_b64" not in resumen
    assert resumen["filas"] == 1
    assert resumen["muestra"][0]["MC"] == "MCE"
    assert resumen["muestra"][0]["CUIT Cliente"] == CUIT
    assert "MC AUX" in resumen["muestra"][0]
