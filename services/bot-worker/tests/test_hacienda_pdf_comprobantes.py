"""Descarga de PDF por comprobante de hacienda (port V1 sin navegador real)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import ArtifactUploadError
from bot_worker.bots.hacienda.plugin import (
    ID_ARTEFACTO_PDF,
    HaciendaPlugin,
)
from bot_worker.runtime.context import (
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
)
from bot_worker.runtime.portals.hacienda import (
    extraer_clave_fila,
    extraer_data_id,
    inferir_identificadores,
    nombre_pdf_hacienda,
)

CUIT = "20123456789"
PDF = b"%PDF-1.7 comprobante de prueba"


# ---------------------------------------------------------------------------
# Helpers puros (port de V1)


def test_extrae_data_id_de_atributos_y_de_getpdf() -> None:
    assert extraer_data_id('data-id="123456" x') == "123456"
    assert extraer_data_id("data-idcomprobante: 98765") == "98765"
    assert extraer_data_id("/lsp-web/consultaLiquidacion/getPdf?id=555") == "555"
    assert extraer_data_id("sin id") == ""


def test_clave_de_fila_es_estable_y_prioriza_liq_codigo() -> None:
    assert extraer_clave_fila("liqCodigo=4411 coe=1") == "liqCodigo=4411"
    assert extraer_clave_fila("nada util") == ""


def test_identificadores_toman_cuit_de_referencia_y_codigo() -> None:
    cuit, codigo = inferir_identificadores("20111111111 Vendedor 1234567890")
    assert cuit == "20111111111"
    assert codigo == "1234567890"
    assert inferir_identificadores("sin numeros") == ("SIN_CUIT", "SIN_CODIGO")


def test_nombre_pdf_sigue_el_patron_v1_y_sanitiza() -> None:
    nombre = nombre_pdf_hacienda("por_emisor", CUIT, "20111111111", "1234567890")
    assert nombre == "HACIENDA - POR_EMISOR - 20123456789 - 20111111111 - 1234567890.pdf"
    assert "/" not in nombre_pdf_hacienda("por_emisor", CUIT, "x/y", "a:b")
    assert ":" not in nombre_pdf_hacienda("por_emisor", CUIT, "x/y", "a:b")


# ---------------------------------------------------------------------------
# Plugin con portal falso


class _Sink:
    async def progress(self, phase: str, percent: int, message: str) -> None:
        return None


class _HaciendaFalsa:
    """Portal de hacienda con recorrido y descarga por consulta ya resueltos."""

    def __init__(
        self, pdfs: list[dict[str, Any]], filas: list[dict[str, Any]] | None = None
    ) -> None:
        self.pdfs = pdfs
        self.filas = filas if filas is not None else [{"consulta": "por_emisor"}]
        self.llamadas: list[dict[str, Any]] = []
        self.recorridos: list[dict[str, Any]] = []
        self.consultas_tabulares = 0

    async def consultar(self, *, consulta_key: str, desde: str, hasta: str) -> list[dict[str, Any]]:
        self.consultas_tabulares += 1
        return [dict(fila) for fila in self.filas]

    async def recorrer_consulta(self, **kwargs: Any) -> dict[str, Any]:
        """Port del recorrido unico de V1: filas del cuadro y PDF por comprobante."""
        self.llamadas.append(kwargs)
        self.recorridos.append(kwargs)
        destino = Path(kwargs["destino_dir"])
        destino.mkdir(parents=True, exist_ok=True)
        for pdf in self.pdfs:
            if "ruta" in pdf:
                (destino / pdf["archivo"]).write_bytes(PDF)
        filas = [
            dict(fila, consulta=kwargs["consulta_key"])
            if "consulta" not in fila
            else dict(fila)
            for fila in self.filas
        ]
        return {"filas": filas, "pdfs": self.pdfs if kwargs.get("descargar_pdf") else None}

    async def descargar_pdfs_consulta(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.llamadas.append(kwargs)
        return self.pdfs


class _Comprobantes:
    def __init__(self, hacienda: _HaciendaFalsa) -> None:
        self._hacienda = hacienda

    async def seleccionar_denominacion(self, denominacion: str) -> None:
        return None

    async def abrir_hacienda(self, servicio: str, denominacion: str) -> _HaciendaFalsa:
        return self._hacienda


class _AsyncCM:
    def __init__(self, valor: Any) -> None:
        self._valor = valor

    async def __aenter__(self) -> Any:
        return self._valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _Sesion:
    def __init__(self, comprobantes: _Comprobantes) -> None:
        self._comprobantes = comprobantes

    async def login(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def open_service(self, *args: Any, **kwargs: Any) -> _Comprobantes:
        return self._comprobantes


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self._sesion = sesion

    def arca_session(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM(self._sesion)


class _Subidas:
    """Presign falso: acepta cualquier slot del manifiesto."""

    def __init__(self) -> None:
        self.rutas: list[tuple[str, str]] = []


def _runtime(tmp_path: Path, fabrica: _Fabrica, subidas: _Subidas) -> BotRuntime:
    async def presign(artifact_id: str, content_type: str, size_bytes: int) -> dict[str, Any]:
        return {"upload_url": "", "object_key": f"obj/{artifact_id}"}

    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=presign,
        declarados={
            spec.nombre: ((spec.content_types), spec.max_bytes)
            for spec in HaciendaPlugin.manifest.artefactos_produce
        },
    )
    original = store.upload

    async def upload_registrado(artifact_id: str, rel_path: str, client: Any = None) -> dict[str, Any]:
        subidas.rutas.append((artifact_id, rel_path))
        return await original(artifact_id, rel_path, client)

    store.upload = upload_registrado  # type: ignore[method-assign]
    return BotRuntime(
        job_id="job-hac-pdf",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=time.time() + 300),
        credentials=FiscalCredentials(cuit_representante=CUIT, clave="secreta"),
        proxy=None,
        artifact_store=store,
        event_sink=_Sink(),
        browser_factory=fabrica,  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )


def _entrada_con_pdf(**extra: Any) -> dict[str, Any]:
    base = {
        "operacion": "consultar",
        "representado_cuit": CUIT,
        "denominacion": "D",
        "fecha_desde": "01/01/2024",
        "fecha_hasta": "31/01/2024",
        "por_receptor": False,
        "descargar_pdf": True,
        "subir_excel": False,
    }
    base.update(extra)
    return base


def test_descargar_pdf_sube_los_comprobantes_bajados(tmp_path: Path) -> None:
    async def _ir() -> None:
        pdfs = [
            {"consulta": "por_emisor", "pagina": 1, "archivo": "HACIENDA - POR_EMISOR - A.pdf", "ruta": "x"},
            {"consulta": "por_emisor", "pagina": 1, "archivo": "HACIENDA - POR_EMISOR - B.pdf", "ruta": "y"},
        ]
        hacienda = _HaciendaFalsa(pdfs)
        subidas = _Subidas()
        plugin = HaciendaPlugin()
        payload = await plugin.validate(_entrada_con_pdf(subir_excel=False))
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_Comprobantes(hacienda))), subidas)

        resultado = await plugin.execute(payload, rt)

        assert resultado.result == "OK"
        # Una sola navegacion por consulta: filas del cuadro y PDF juntos (V1).
        assert hacienda.consultas_tabulares == 0
        assert len(hacienda.llamadas) == 1
        assert hacienda.llamadas[0]["representado_cuit"] == CUIT
        assert hacienda.llamadas[0]["descargar_pdf"] is True
        assert resultado.data["por_emisor"]["filas"] == 1
        assert resultado.data["por_emisor"]["pdf"] == {"total": 2, "subidos": 2, "errores": []}
        subidos_ids = [artefacto for artefacto, _ in subidas.rutas]
        assert subidos_ids == [ID_ARTEFACTO_PDF, ID_ARTEFACTO_PDF]
        assert all(ruta.startswith("pdf_por_emisor/") for _, ruta in subidas.rutas)
        assert len(resultado.artifacts) == 2

    asyncio.run(_ir())


def test_error_por_comprobante_no_detiene_la_consulta(tmp_path: Path) -> None:
    async def _ir() -> None:
        pdfs = [
            {"consulta": "por_emisor", "pagina": 1, "archivo": "A.pdf", "ruta": "x"},
            {
                "consulta": "por_emisor",
                "pagina": 1,
                "archivo": "B.pdf",
                "error": "hacienda_pdf_unavailable",
            },
        ]
        hacienda = _HaciendaFalsa(pdfs)
        subidas = _Subidas()
        plugin = HaciendaPlugin()
        payload = await plugin.validate(_entrada_con_pdf(subir_excel=False))
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_Comprobantes(hacienda))), subidas)

        resultado = await plugin.execute(payload, rt)

        resumen = resultado.data["por_emisor"]["pdf"]
        assert resumen["total"] == 2
        assert resumen["subidos"] == 1
        assert resumen["errores"] == [
            {"archivo": "B.pdf", "error": "hacienda_pdf_unavailable"}
        ]
        assert len(subidas.rutas) == 1

    asyncio.run(_ir())


def test_sin_descargar_pdf_se_conserva_el_contrato_tabular(tmp_path: Path) -> None:
    async def _ir() -> None:
        hacienda = _HaciendaFalsa([])
        plugin = HaciendaPlugin()
        payload = await plugin.validate(_entrada_con_pdf(descargar_pdf=False))
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_Comprobantes(hacienda))), _Subidas())

        resultado = await plugin.execute(payload, rt)

        assert hacienda.consultas_tabulares == 1
        assert hacienda.llamadas == []
        assert "pdf" not in resultado.data["por_emisor"]

    asyncio.run(_ir())


def test_subida_pdf_fallida_sigue_siendo_artifact_upload_error(tmp_path: Path) -> None:
    """Contrato V3: una subida que falla es reintentable, no se silencia."""

    async def _ir() -> None:
        pdfs = [{"consulta": "por_emisor", "pagina": 1, "archivo": "A.pdf", "ruta": "x"}]
        hacienda = _HaciendaFalsa(pdfs)
        plugin = HaciendaPlugin()
        payload = await plugin.validate(_entrada_con_pdf(subir_excel=False))
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_Comprobantes(hacienda))), _Subidas())
        # Sin slot ni declaracion para hacienda_pdf: el store rechaza la subida.
        rt.artifact_store._declarados = {}  # type: ignore[attr-defined]
        with pytest.raises(ArtifactUploadError):
            await plugin.execute(payload, rt)

    asyncio.run(_ir())


def test_manifiesto_declara_el_artefacto_pdf() -> None:
    nombres = [a.nombre for a in HaciendaPlugin.manifest.artefactos_produce]
    assert "hacienda_consolidado" in nombres
    assert "hacienda_pdf" in nombres
    assert ID_ARTEFACTO_PDF == "hacienda_pdf"


def test_entrada_descarga_pdf_por_defecto_como_v1() -> None:
    from bot_worker.bots.hacienda.schema import HaciendaConsultarInput

    entrada = HaciendaConsultarInput.model_validate(
        {
            "representado_cuit": CUIT,
            "denominacion": "D",
            "fecha_desde": "01/01/2024",
            "fecha_hasta": "31/01/2024",
        }
    )
    assert entrada.descargar_pdf is True
    assert entrada.subir_excel is True


def test_el_esquema_acepta_los_campos_de_v1() -> None:
    """CUIT del representante, minio, descargas y headless de ``hacienda_bot``."""
    from bot_worker.bots.hacienda.schema import HaciendaConsultarInput

    entrada = HaciendaConsultarInput.model_validate(
        {
            "cuit_representante": "20-12345678-9",
            "cuit_representado": CUIT,
            "denominacion": "D",
            "desde": "01/01/2024",
            "hasta": "31/01/2024",
            "eliminar_descargas": False,
            "subir_a_minio": False,
            "proxy_request": True,
            "headless": True,
        }
    )
    assert entrada.cuit_representante == "20123456789"
    assert entrada.subir_excel is False
    assert entrada.proxy_request is True


def test_crea_el_excel_consolidado_de_v1_con_hojas_por_consulta(tmp_path: Path) -> None:
    """V1 escribe un unico XLSX: ``consolidado`` + una hoja por consulta."""
    import zipfile

    from bot_worker.bots.hacienda.plugin import hojas_consolidado, nombre_consolidado_xlsx
    from bot_worker.runtime.portals.hacienda import escribir_xlsx

    destino = tmp_path / "consolidado.xlsx"
    hojas = hojas_consolidado(
        {
            "por_emisor": [{"consulta": "por_emisor", "Fecha": "01/01/2024"}],
            "por_receptor": [{"consulta": "por_receptor", "Fecha": "02/01/2024"}],
        }
    )
    escribir_xlsx(destino, hojas)

    assert [hoja[0] for hoja in hojas] == ["consolidado", "por_emisor", "por_receptor"]
    with zipfile.ZipFile(destino) as libro:
        nombres = set(libro.namelist())
        assert "[Content_Types].xml" in nombres
        assert "xl/workbook.xml" in nombres
        hoja = libro.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "01/01/2024" in hoja and "02/01/2024" in hoja

    nombre = nombre_consolidado_xlsx(
        CUIT, "Empresa SA", "01/01/2024", "31/01/2024", "20260101_120000"
    )
    assert nombre.startswith("HACIENDA - TABLA CONSOLIDADA - ")
    assert nombre.endswith(".xlsx")


def test_sin_registros_el_consolidado_avisa_sin_datos() -> None:
    from bot_worker.bots.hacienda.plugin import hojas_consolidado

    hojas = hojas_consolidado({"por_emisor": [], "por_receptor": []})

    assert hojas[0][0] == "consolidado"
    assert hojas[0][2] == [["sin_datos", "Sin registros de comprobantes"]]