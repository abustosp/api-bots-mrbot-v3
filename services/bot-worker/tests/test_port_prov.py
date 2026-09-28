"""Port provincial/no-ARCA: validate + execute de los 7 bots provinciales.

Cubre ``arba``, ``retper_iibb_agip``, ``retper_iibb_misiones``,
``sifere``, ``srt``, ``hacienda`` y ``sct``: contratos ``validate()``,
helpers puros portados de V2 y ``execute()`` con navegador falso
(sin red ni secretos).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from bot_worker.bots.arba.plugin import ArbaPlugin
from bot_worker.bots.arba.schema import nombre_archivo_descarga
from bot_worker.bots.errors import CredentialsRejectedError, InvalidInputError
from bot_worker.bots.hacienda.plugin import HaciendaPlugin, escribir_consolidado
from bot_worker.bots.hacienda.plugin import nombre_consolidado
from bot_worker.bots.retper_iibb_agip.plugin import RetperIibbAgipPlugin
from bot_worker.bots.retper_iibb_agip.plugin import (
    _yyyymm_to_mmyyyy,
    nombre_descarga_agip,
)
from bot_worker.bots.retper_iibb_misiones.plugin import RetperIibbMisionesPlugin
from bot_worker.bots.retper_iibb_misiones.plugin import (
    _build_filename_base,
    _looks_like_pdf,
    _period_to_site_format,
)
from bot_worker.bots.sct.plugin import SctPlugin, nombre_reporte_sct
from bot_worker.bots.sifere.plugin import SiferePlugin, nombre_excel_sifere
from bot_worker.bots.srt.plugin import SrtPlugin, es_respuesta_sin_datos
from bot_worker.runtime.context import (
    ArtifactSlot,
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
)

CUIT = "20123456789"


# ---------------------------------------------------------------------------
# Falsos de runtime (sin red, sin Playwright, sin secretos en disco)


class _Sink:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, int, str]] = []

    async def progress(self, phase: str, percent: int, message: str) -> None:
        self.eventos.append((phase, percent, message))


class _AsyncCM:
    def __init__(self, valor: Any) -> None:
        self._valor = valor

    async def __aenter__(self) -> Any:
        return self._valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _Rol:
    async def fill(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def click(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def wait_for(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def count(self) -> int:
        return 0

    async def select_option(self, *args: Any, **kwargs: Any) -> None:
        pass


class _Descarga:
    def __init__(self, contenido: bytes) -> None:
        self._contenido = contenido

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(self._contenido)


class _InfoDescarga:
    def __init__(self, contenido: bytes) -> None:
        self._contenido = contenido

    @property
    def value(self):  # type: ignore[no-untyped-def]
        async def _obtener() -> _Descarga:
            return _Descarga(self._contenido)

        return _obtener()


class _PaginaArba:
    """Pagina falsa: login ok, rol ausente, consulta con o sin archivos."""

    def __init__(self, contenido: str = "") -> None:
        self._contenido = contenido
        self.url = "https://dfe.arba.gov.ar/DomicilioElectronico/consulta"

    def set_default_timeout(self, *args: Any) -> None:
        pass

    async def goto(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_by_role(self, *args: Any, **kwargs: Any) -> _Rol:
        return _Rol()

    def locator(self, *args: Any, **kwargs: Any) -> _Rol:
        return _Rol()

    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def content(self) -> str:
        return self._contenido

    def expect_download(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM(_InfoDescarga(b"PK\x03\x04falso-zip"))


class _ContextoArba:
    def __init__(self, pagina: _PaginaArba) -> None:
        self._pagina = pagina

    async def new_page(self) -> _PaginaArba:
        return self._pagina


class _ServicioBase:
    async def seleccionar_representado(self, *args: Any, **kwargs: Any) -> None:
        pass


class _ServicioSifere(_ServicioBase):
    async def consultar_jurisdiccion(
        self, codigo: int, periodo: str, destino: Path
    ) -> dict[str, Any]:
        Path(destino).write_text("cuit;importe\n30;100\n", encoding="utf-8")
        return {"filas": 1, "total": "100"}


class _ServicioSrt:
    async def consultar_cuit(self, cuit: str) -> dict[str, Any]:
        return {"alicuota": "1,5", "tablas": []}


class _ServicioSct(_ServicioBase):
    async def descargar_reporte(
        self, seccion: str, formato: str, destino: Path
    ) -> None:
        Path(destino).write_bytes(b"reporte-falso")


class _Comprobantes:
    def __init__(self) -> None:
        self.denominaciones: list[str] = []

    async def seleccionar_denominacion(self, denominacion: str) -> None:
        self.denominaciones.append(denominacion)

    async def abrir_hacienda(self, servicio: str, denominacion: str) -> Any:
        return _Hacienda()


class _Hacienda:
    async def consultar(
        self, consulta_key: str, desde: str, hasta: str
    ) -> list[dict[str, Any]]:
        return [{"consulta": consulta_key, "desde": desde, "hasta": hasta}]


class _Sesion:
    """Sesion falsa: servicio ARCA o metodos directos no-ARCA."""

    def __init__(self, servicio: Any = None) -> None:
        self._servicio = servicio or _ServicioBase()
        self.ingresos: list[Any] = []

    async def login(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def open_service(self, *args: Any, **kwargs: Any) -> Any:
        return self._servicio

    async def ingresar(self, *args: Any, **kwargs: Any) -> None:
        self.ingresos.append((args, kwargs))

    async def consultar_retper(self, **kwargs: Any) -> None:
        destino = kwargs.get("destino") or kwargs.get("destino_xlsx")
        Path(destino).write_bytes(b"reporte-falso")

    async def generar_pdf(self, destino_pdf: Path) -> None:
        Path(destino_pdf).write_bytes(b"%PDF-1.4\n" + b"x" * 200)

    async def close(self) -> None:
        pass


class _Fabrica:
    def __init__(self, sesion: _Sesion, pagina: _PaginaArba | None = None) -> None:
        self._sesion = sesion
        self._pagina = pagina or _PaginaArba()

    def arca_session(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM(self._sesion)

    def new_context(self, *args: Any, **kwargs: Any) -> _AsyncCM:
        return _AsyncCM((None, _ContextoArba(self._pagina)))


def _slot(artifact_id: str) -> ArtifactSlot:
    return ArtifactSlot(
        artifact_id=artifact_id, put_url="", object_key=f"obj/{artifact_id}"
    )


def _runtime(
    tmp_path: Path, fabrica: _Fabrica, slots: dict[str, ArtifactSlot],
    *, sin_credenciales: bool = False,
) -> BotRuntime:
    return BotRuntime(
        job_id="job-prov",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=time.time() + 300),
        credentials=None
        if sin_credenciales
        else FiscalCredentials(cuit_representante=CUIT, clave="secreta"),
        proxy=None,
        artifact_store=ArtifactStore(work_dir=tmp_path, slots=slots),
        event_sink=_Sink(),
        browser_factory=fabrica,  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )


# ---------------------------------------------------------------------------
# Helpers puros portados de V2


def test_helpers_puros_provinciales() -> None:
    assert nombre_archivo_descarga(CUIT, "202401", "Mi Empresa").endswith(".zip")
    assert CUIT in nombre_archivo_descarga(CUIT, "202401", "Mi Empresa")
    assert _yyyymm_to_mmyyyy("202401") == "01/2024"
    nombre = nombre_descarga_agip(CUIT, "RETPER", "202401", "202402", "Denom", "csv")
    assert nombre.endswith(".csv") and CUIT in nombre
    assert _period_to_site_format("202401") == "2024/01"
    base = _build_filename_base(CUIT, "202401", "202402", "Denom")
    assert CUIT in base and "MISIONES" in base
    assert _looks_like_pdf(b"%PDF-1.4\n" + b"x" * 200) is True
    assert _looks_like_pdf(b"no-es-pdf") is False
    assert nombre_excel_sifere(CUIT, "202401", 901).endswith(".xlsx")
    assert es_respuesta_sin_datos("No tiene afiliación vigente") is not None
    assert es_respuesta_sin_datos("alicuota 1,5 vigente") is None
    assert nombre_reporte_sct(CUIT, "deudas", "csv").endswith(".csv")
    assert "POR_EMISOR" in nombre_consolidado(CUIT, "por_emisor", "01/01/2024", "31/01/2024").upper()


def test_hacienda_escribir_consolidado(tmp_path: Path) -> None:
    destino = tmp_path / "consolidado.csv"
    filas = escribir_consolidado(destino, [{"a": "1", "b": "2"}, {"b": "3", "c": "4"}])
    assert filas == 2
    texto = destino.read_text(encoding="utf-8")
    assert "a" in texto.splitlines()[0] and "c" in texto.splitlines()[0]


# ---------------------------------------------------------------------------
# validate(): contratos de entrada


def test_validate_provincial_ok() -> None:
    async def _ir() -> None:
        assert (await ArbaPlugin().validate({
            "operacion": "descargar", "representado_cuit": CUIT,
            "periodo": "202401", "representado_nombre": "Mi Empresa",
        }))[0] == "descargar"
        assert (await RetperIibbAgipPlugin().validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "periodo_desde": "202401", "periodo_hasta": "202402",
        }))[0] == "consultar"
        assert (await RetperIibbMisionesPlugin().validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "periodo_desde": "202401", "periodo_hasta": "202402",
        }))[0] == "consultar"
        assert (await SiferePlugin().validate({
            "operacion": "consultar", "representado_cuit": CUIT, "periodo": "202401",
        }))[0] == "consultar"
        assert (await SrtPlugin().validate({
            "operacion": "consultar_alicuotas", "cuits_consulta": [CUIT],
        }))[0] == "consultar_alicuotas"
        assert (await HaciendaPlugin().validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "fecha_desde": "01/01/2024", "fecha_hasta": "31/01/2024",
        }))[0] == "consultar"
        assert (await SctPlugin().validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "secciones": ["deudas"], "formatos": ["csv"],
        }))[0] == "consultar"

    asyncio.run(_ir())


def test_validate_provincial_rechaza_entrada_invalida() -> None:
    async def _ir() -> None:
        casos = [
            (ArbaPlugin(), {"operacion": "descargar", "representado_cuit": "123",
                            "periodo": "202401", "representado_nombre": "X"}),
            (ArbaPlugin(), {"operacion": "inexistente", "representado_cuit": CUIT,
                            "periodo": "202401", "representado_nombre": "X"}),
            (RetperIibbAgipPlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                                      "denominacion": "D", "periodo_desde": "202403",
                                      "periodo_hasta": "202401"}),
            (RetperIibbMisionesPlugin(), {"operacion": "consultar",
                                          "representado_cuit": CUIT, "denominacion": "D",
                                          "periodo_desde": "202413", "periodo_hasta": "202413"}),
            (SiferePlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                              "periodo": "202401", "jurisdicciones": [100]}),
            (SrtPlugin(), {"operacion": "consultar_alicuotas", "cuits_consulta": []}),
            (HaciendaPlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                                "denominacion": "D", "fecha_desde": "31/01/2024",
                                "fecha_hasta": "01/01/2024"}),
            (SctPlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                           "secciones": ["otra"], "formatos": ["csv"]}),
        ]
        for plugin, payload in casos:
            try:
                await plugin.validate(payload)  # type: ignore[union-attr]
                raise AssertionError(f"{type(plugin).__name__} acepto {payload!r}")
            except InvalidInputError:
                pass

    asyncio.run(_ir())


def test_execute_provincial_sin_credenciales(tmp_path: Path) -> None:
    async def _ir() -> None:
        fabrica = _Fabrica(_Sesion())
        for plugin, payload in [
            (ArbaPlugin(), {"operacion": "descargar", "representado_cuit": CUIT,
                            "periodo": "202401", "representado_nombre": "X"}),
            (SrtPlugin(), {"operacion": "consultar_alicuotas", "cuits_consulta": [CUIT]}),
            (SctPlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                           "secciones": ["deudas"], "formatos": ["csv"]}),
        ]:
            datos = await plugin.validate(payload)  # type: ignore[union-attr]
            try:
                await plugin.execute(datos, _runtime(tmp_path, fabrica, {},  # type: ignore[union-attr]
                                                     sin_credenciales=True))
                raise AssertionError("deberia exigir credenciales")
            except CredentialsRejectedError:
                pass

    asyncio.run(_ir())


# ---------------------------------------------------------------------------
# execute(): caminos felices con navegador falso


def test_execute_arba_descarga_zip(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = ArbaPlugin()
        payload = await plugin.validate({
            "operacion": "descargar", "representado_cuit": CUIT,
            "periodo": "202401", "representado_nombre": "Mi Empresa",
        })
        rt = _runtime(tmp_path, _Fabrica(_Sesion()), {"retper_arba.zip": _slot("retper_arba.zip")})
        resultado = await plugin.execute(payload, rt)
        assert resultado.result == "OK"
        assert resultado.data["periodo"] == "202401"
        assert len(resultado.artifacts) == 1
        assert resultado.artifacts[0]["sha256"]

    asyncio.run(_ir())


def test_execute_arba_sin_archivos(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = ArbaPlugin()
        payload = await plugin.validate({
            "operacion": "descargar", "representado_cuit": CUIT,
            "periodo": "202401", "representado_nombre": "Mi Empresa",
        })
        pagina = _PaginaArba("No Existen archivos para el periodo solicitado")
        rt = _runtime(tmp_path, _Fabrica(_Sesion(), pagina), {})
        resultado = await plugin.execute(payload, rt)
        assert resultado.result == "OK"
        assert resultado.data["archivos"] == []

    asyncio.run(_ir())


def test_execute_agip_y_misiones(tmp_path: Path, monkeypatch: Any) -> None:
    # AGIP y Misiones no son ARCA: cada plugin construye su propia sesión sobre
    # un contexto de navegador. Se reemplazan esas clases por la sesión falsa.
    import bot_worker.bots.retper_iibb_agip.plugin as mod_agip
    import bot_worker.bots.retper_iibb_misiones.plugin as mod_mis

    monkeypatch.setattr(mod_agip, "AgipSession", lambda *a, **k: _Sesion())
    monkeypatch.setattr(mod_mis, "MisionesSession", lambda *a, **k: _Sesion())

    async def _ir() -> None:
        agip = RetperIibbAgipPlugin()
        paga = await agip.validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "periodo_desde": "202401", "periodo_hasta": "202402",
        })
        rta = _runtime(tmp_path, _Fabrica(_Sesion()),
                       {"retper_agip_reporte": _slot("retper_agip_reporte")})
        resa = await agip.execute(paga, rta)
        assert resa.result == "OK" and len(resa.artifacts) == 1

        mis = RetperIibbMisionesPlugin()
        pagm = await mis.validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "periodo_desde": "202401", "periodo_hasta": "202402",
        })
        rtm = _runtime(tmp_path, _Fabrica(_Sesion()), {
            "retper_misiones_xlsx": _slot("retper_misiones_xlsx"),
            "retper_misiones_pdf": _slot("retper_misiones_pdf"),
        })
        resm = await mis.execute(pagm, rtm)
        assert resm.result == "OK" and len(resm.artifacts) == 2
        assert resm.data["archivos"][1].endswith(".pdf")

    asyncio.run(_ir())


def test_execute_sifere_srt_sct(tmp_path: Path) -> None:
    async def _ir() -> None:
        sif = SiferePlugin()
        pays = await sif.validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "periodo": "202401", "jurisdicciones": [901, 902],
        })
        rts = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioSifere())), {
            "sifere_jurisdiccion_xlsx_901": _slot("sifere_jurisdiccion_xlsx_901"),
            "sifere_jurisdiccion_xlsx_902": _slot("sifere_jurisdiccion_xlsx_902"),
        })
        ress = await sif.execute(pays, rts)
        assert ress.result == "OK" and len(ress.artifacts) == 2

        srt = SrtPlugin()
        payr = await srt.validate({
            "operacion": "consultar_alicuotas", "cuits_consulta": [CUIT],
        })
        rtr = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioSrt())),
                       {"srt_alicuotas_json": _slot("srt_alicuotas_json")})
        resr = await srt.execute(payr, rtr)
        assert resr.result == "OK" and resr.data["cantidad"] == 1
        assert resr.data["consultas"][0]["alicuota"] == "1,5"

        sct = SctPlugin()
        payc = await sct.validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "secciones": ["deudas"], "formatos": ["csv"],
        })
        rtc = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioSct())),
                       {"sct_reporte_deudas_csv": _slot("sct_reporte_deudas_csv")})
        resc = await sct.execute(payc, rtc)
        assert resc.result == "OK" and len(resc.artifacts) == 1

    asyncio.run(_ir())


def test_execute_hacienda_consulta_y_consolida(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = HaciendaPlugin()
        payload = await plugin.validate({
            "operacion": "consultar", "representado_cuit": CUIT,
            "denominacion": "D", "fecha_desde": "01/01/2024",
            "fecha_hasta": "31/01/2024", "por_receptor": False,
        })
        rt = _runtime(tmp_path, _Fabrica(_Sesion(_Comprobantes())),
                      {"hacienda_consolidado": _slot("hacienda_consolidado")})
        resultado = await plugin.execute(payload, rt)
        assert resultado.result == "OK"
        assert resultado.data["por_emisor"]["filas"] == 1
        assert "por_receptor" not in resultado.data
        assert len(resultado.artifacts) == 1

    asyncio.run(_ir())
