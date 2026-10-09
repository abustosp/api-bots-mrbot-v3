"""Carga Portal IVA: borrado previo, rutas dentro de work_dir, reintentos y resultado parcial.

Se usa un portal falso que registra cada operacion en orden. Las pruebas fijan
los cuatro puntos que pidio el dueño: el borrado va antes de importar (para no
duplicar), los archivos llegan como rutas absolutas de ``work_dir``, el reintento
no vuelve a borrar, y una seccion fallida deja el job PARCIAL sin tumbarlo.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.carga_portal_iva.plugin import CargaPortalIvaPlugin
from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.context import ArtifactStore, CancellationToken

CUIT = "20320160856"


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


CF_CSV = "Fecha;Credito Fiscal Computable\n01/01/2026;100,50\n02/01/2026;50,25\n"
TXT = "linea1|dato\nlinea2|dato\n"


class _PortalCarga:
    """Portal falso que registra el orden de las operaciones."""

    def __init__(self, *, fallar_importar: set[str] | None = None, fallar_csv: set[str] | None = None,
                 sin_boton_borrar: bool = False, fallar_intentos: int = 0) -> None:
        self.log: list[tuple[str, ...]] = []
        self._fallar_importar = fallar_importar or set()
        self._fallar_csv = fallar_csv or set()
        self._sin_boton_borrar = sin_boton_borrar
        self._fallos_restantes = fallar_intentos

    async def seleccionar_representado(self, cuit: str) -> None:
        self.log.append(("representado", cuit))

    async def preparar_periodo(self, periodo: str, **opciones: bool) -> list[dict[str, str]]:
        self.log.append(("preparar_periodo", periodo, str(opciones)))
        return [{"value": periodo, "label": f"{periodo[4:]}/{periodo[:4]}"}]

    async def abrir_libro(self, libro: str) -> None:
        self.log.append(("abrir_libro", libro))

    async def eliminar_todo(self) -> bool:
        self.log.append(("eliminar_todo",))
        return not self._sin_boton_borrar

    async def importar_txt(self, libro: str, comprobantes: Path, alicuotas: Path) -> None:
        self.log.append(("importar_txt", libro, str(comprobantes), str(alicuotas)))
        if self._fallos_restantes > 0:
            self._fallos_restantes -= 1
            raise TargetUnavailableError("no se detecto Procesada", diagnostic_code="carga_not_processed")
        if libro in self._fallar_importar:
            raise TargetUnavailableError("fallo de importacion", diagnostic_code="carga_not_processed")

    async def volver_al_inicio(self) -> None:
        self.log.append(("volver_al_inicio",))

    async def cargar_csv(self, seccion: str, csv_path: Path) -> None:
        self.log.append(("cargar_csv", seccion, str(csv_path)))
        if seccion in self._fallar_csv:
            raise TargetUnavailableError("CSV rechazado", diagnostic_code="carga_csv_link_missing")

    async def cargar_credito_fiscal_compras(self, total: str) -> bool:
        self.log.append(("credito_fiscal_compras", total))
        return True


class _Sesion:
    def __init__(self, portal: _PortalCarga) -> None:
        self.portal = portal
        self.portales: list[str | None] = []

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **kwargs: Any) -> _PortalCarga:
        self.portales.append(kwargs.get("portal"))
        return self.portal


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


def _entrada(plugin: CargaPortalIvaPlugin, **extra: Any) -> Any:
    cuerpo: dict[str, Any] = {
        "operacion": "cargar",
        "representado_cuit": CUIT,
        "periodo": "202601",
        **extra,
    }
    return asyncio.run(plugin.validate(cuerpo))


def _ejecutar(tmp_path: Path, entrada: Any, portal: _PortalCarga) -> Any:
    sesion = _Sesion(portal)
    runtime = SimpleNamespace(
        job_id="job-carga",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia"),
        proxy=None,
        artifact_store=ArtifactStore(tmp_path, slots={}, presign=None),
        event_sink=_Sink(),
        browser_factory=_Fabrica(sesion),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(CargaPortalIvaPlugin().execute(entrada, runtime))
    return resultado, sesion


def test_borra_los_registros_previos_antes_de_importar(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(CargaPortalIvaPlugin(), liv_cbte_b64=_b64(TXT), liv_alicuota_b64=_b64(TXT))

    resultado, sesion = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "OK"
    assert sesion.portales == ["carga_portal_iva"]
    nombres = [op[0] for op in portal.log]
    assert nombres.index("eliminar_todo") < nombres.index("importar_txt")
    assert nombres.index("abrir_libro") < nombres.index("eliminar_todo")


def test_sin_boton_de_borrado_la_seccion_falla_y_no_importa(tmp_path: Path) -> None:
    portal = _PortalCarga(sin_boton_borrar=True)
    entrada = _entrada(CargaPortalIvaPlugin(), liv_cbte_b64=_b64(TXT), liv_alicuota_b64=_b64(TXT))

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "ERROR"
    assert resultado.data["secciones"]["ventas_txt"] == "error: TargetUnavailableError"
    assert all(op[0] != "importar_txt" for op in portal.log)


def test_las_rutas_llegan_como_absolutas_dentro_de_work_dir(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(
        CargaPortalIvaPlugin(),
        liv_cbte_b64=_b64(TXT),
        liv_alicuota_b64=_b64(TXT),
        csv_cf_b64=_b64(CF_CSV),
    )

    _ejecutar(tmp_path, entrada, portal)

    rutas = [op for op in portal.log if op[0] in ("importar_txt", "cargar_csv")]
    assert rutas, "debe haber operaciones con archivos"
    for op in rutas:
        for ruta in op[2:] if op[0] == "importar_txt" else op[2:3]:
            assert Path(ruta).is_absolute(), f"ruta relativa: {ruta}"
            assert Path(ruta).resolve().parent == tmp_path.resolve(), f"fuera de work_dir: {ruta}"
            assert Path(ruta).is_file(), f"no existe: {ruta}"


def test_reintento_del_txt_no_vuelve_a_borrar(tmp_path: Path) -> None:
    """Primer intento falla al verificar; el reintento importa sin volver a borrar."""
    portal = _PortalCarga(fallar_intentos=1)
    entrada = _entrada(CargaPortalIvaPlugin(), liv_cbte_b64=_b64(TXT), liv_alicuota_b64=_b64(TXT))

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "OK"
    assert resultado.data["secciones"]["ventas_txt"] == "ventas: importado (intento 2)"
    assert [op[0] for op in portal.log].count("eliminar_todo") == 1
    assert [op[0] for op in portal.log].count("importar_txt") == 2


def test_una_seccion_fallida_deja_el_job_parcial_y_sigue_con_las_demas(tmp_path: Path) -> None:
    portal = _PortalCarga(fallar_importar={"ventas"}, fallar_csv={"DF"})
    entrada = _entrada(
        CargaPortalIvaPlugin(),
        liv_cbte_b64=_b64(TXT),
        liv_alicuota_b64=_b64(TXT),
        lic_cbte_b64=_b64(TXT),
        lic_alicuota_b64=_b64(TXT),
        csv_cf_b64=_b64(CF_CSV),
        csv_df_b64=_b64(CF_CSV),
    )

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "PARCIAL"
    secciones = resultado.data["secciones"]
    assert secciones["ventas_txt"].startswith("error:")
    assert secciones["compras_txt"].endswith("importado (intento 1)")
    assert secciones["CF"] == "CF: cargado"
    assert secciones["DF"].startswith("error:")
    assert any("ventas_txt" in w or "DF" in w for w in resultado.warnings)


def test_si_todas_las_secciones_fallan_el_job_es_error(tmp_path: Path) -> None:
    portal = _PortalCarga(fallar_importar={"ventas"})
    entrada = _entrada(CargaPortalIvaPlugin(), liv_cbte_b64=_b64(TXT), liv_alicuota_b64=_b64(TXT))

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "ERROR"
    assert resultado.errors[0].internal_diagnostic == "carga_todas_las_secciones_fallaron"


def test_credito_fiscal_compras_se_completa_despues_del_cf_con_prorrateo(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(CargaPortalIvaPlugin(), csv_cf_b64=_b64(CF_CSV), prorrateo_global=True)

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    assert resultado.result == "OK"
    nombres = [op[0] for op in portal.log]
    assert nombres.index("cargar_csv") < nombres.index("credito_fiscal_compras")
    assert ("credito_fiscal_compras", "150.75") in portal.log
    assert resultado.data["secciones"]["credito_fiscal_compras"] == "ok"


def test_sin_prorrateo_no_se_toca_el_credito_fiscal(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(CargaPortalIvaPlugin(), csv_cf_b64=_b64(CF_CSV), prorrateo_global=False)

    _ejecutar(tmp_path, entrada, portal)

    assert all(op[0] != "credito_fiscal_compras" for op in portal.log)


def test_compras_abre_su_libro_y_no_ventas(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(CargaPortalIvaPlugin(), lic_cbte_b64=_b64(TXT), lic_alicuota_b64=_b64(TXT))

    _ejecutar(tmp_path, entrada, portal)

    libros = [op[1] for op in portal.log if op[0] == "abrir_libro"]
    assert libros == ["compras"]


def test_prepara_periodo_con_todos_los_flags_v1(tmp_path: Path) -> None:
    portal = _PortalCarga()
    entrada = _entrada(
        CargaPortalIvaPlugin(),
        csv_cf_b64=_b64(CF_CSV),
        operaciones_ng_o_e=True,
        prorrateo_ambos=True,
        importacion_definitiva_bienes=True,
        importacion_servicios=True,
        regimen_turiva=True,
        bienes_usados=True,
        ninguna_anteriores=False,
    )

    resultado, _ = _ejecutar(tmp_path, entrada, portal)

    preparado = next(op for op in portal.log if op[0] == "preparar_periodo")
    assert preparado[1] == "202601"
    assert "'operaciones_ng_o_e': True" in preparado[2]
    assert "'prorrateo_ambos': True" in preparado[2]
    assert resultado.data["periodos_seleccionables"] == [
        {"value": "202601", "label": "01/2026"}
    ]
