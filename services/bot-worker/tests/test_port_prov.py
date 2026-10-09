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
from bot_worker.bots.retper_iibb_agip.session import (
    AgipSession,
    _nombre_archivo as _nombre_archivo_agip,
)
from bot_worker.bots.retper_iibb_misiones.plugin import RetperIibbMisionesPlugin
from bot_worker.bots.retper_iibb_misiones.plugin import (
    _build_filename_base,
    _looks_like_pdf,
    _period_to_site_format,
)
from bot_worker.bots.sct.plugin import SctPlugin, nombre_reporte_sct
from bot_worker.bots.sifere.plugin import SiferePlugin
from bot_worker.runtime.portals.sifere import nombre_sifere, representado_para_nombre
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
        self.consultas = 0

    def set_default_timeout(self, *args: Any) -> None:
        pass

    async def goto(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_by_role(self, *args: Any, **kwargs: Any) -> _Rol:
        pagina = self
        nombre = kwargs.get("name") or (args[1] if len(args) > 1 else "")
        es_consultar = "Consultar" in str(getattr(nombre, "pattern", nombre))

        class _RolContador(_Rol):
            async def click(self, *a: Any, **k: Any) -> None:
                if es_consultar:
                    pagina.consultas += 1

        return _RolContador()

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
    async def consultar_sircar(
        self,
        *,
        metodo: str,
        jurisdiccion: int,
        periodo: str,
        destino_dir: Path,
        representado_cuit: str,
        nombre_representado: str | None = None,
    ) -> dict[str, Any]:
        etiqueta = "Retenciones" if metodo == "retencion" else "Percepciones"
        nombre = representado_para_nombre(nombre_representado, representado_cuit)
        archivos = []
        for tipo, ext in (("xlsx", ".xls"), ("pdf", ".pdf")):
            ruta = Path(destino_dir) / nombre_sifere(
                representado_cuit,
                periodo,
                nombre,
                ("SIRCAR", etiqueta, str(jurisdiccion)),
                ext=ext,
            )
            ruta.write_bytes(b"PK\x03\x04sifere")
            archivos.append({"nombre": ruta.name, "tipo": tipo, "filas": 1})
        return {
            "metodo": metodo,
            "etiqueta": etiqueta,
            "jurisdiccion": jurisdiccion,
            "periodo": periodo,
            "archivos": archivos,
            "errores": [],
        }

    async def consultar_consolidados(self, **_: Any) -> dict[str, Any]:
        # Como el portal real sin tablas consolidadas: no genera archivos.
        return {"archivos": [], "errores": [], "filas": {}}


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

    async def recorrer_consulta(
        self,
        consulta_key: str,
        desde: str,
        hasta: str,
        destino_dir: Any = None,
        representado_cuit: str = "",
        descargar_pdf: bool = True,
    ) -> dict[str, Any]:
        return {
            "filas": [{"consulta": consulta_key, "desde": desde, "hasta": hasta}],
            "pdfs": [],
        }


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
    assert nombre_archivo_descarga(CUIT, "202401", "Mi Empresa") == (
        f"{CUIT[-1]} - {CUIT} - RETPER ARBA - 202401 - Mi Empresa.zip"
    )
    assert nombre_archivo_descarga(CUIT, "202401", "  Mi/Empresa *") == (
        f"{CUIT[-1]} - {CUIT} - RETPER ARBA - 202401 - MiEmpresa.zip"
    )
    assert nombre_archivo_descarga(CUIT, "202401", "   ").endswith("SIN_DENOMINACION.zip")
    assert _nombre_archivo_agip(CUIT, "RET", "01/2024", "02/2024", "xls", "Mi Empresa") == (
        f"{CUIT[-1]} - {CUIT} - RET - 202401 - 202402 - Mi Empresa.xls"
    )
    assert _yyyymm_to_mmyyyy("202401") == "01/2024"
    nombre = nombre_descarga_agip(CUIT, "RETPER", "202401", "202402", "Denom", "csv")
    assert nombre.endswith(".csv") and CUIT in nombre
    assert _period_to_site_format("202401") == "2024/01"
    base = _build_filename_base(CUIT, "202401", "202402", "Denom")
    assert CUIT in base and "MISIONES" in base
    assert _looks_like_pdf(b"%PDF-1.4\n" + b"x" * 200) is True
    assert _looks_like_pdf(b"no-es-pdf") is False
    assert nombre_sifere(CUIT, "202401", "Empresa", ("SIRCAR", "Retenciones", "901"), ext=".xls") == (
        f"{CUIT[-1]} - {CUIT} - SIFERE - SIRCAR - Retenciones - 901 - 202401 - Empresa.xls"
    )
    assert nombre_sifere(CUIT, "202401", representado_para_nombre(None, CUIT), ("SIRCREB", "Tablas"), ext=".xlsx") == (
        f"{CUIT[-1]} - {CUIT} - SIFERE - SIRCREB - Tablas - 202401 - {CUIT}.xlsx"
    )
    assert es_respuesta_sin_datos("No tiene afiliación vigente") is not None
    assert es_respuesta_sin_datos("alicuota 1,5 vigente") is None
    assert nombre_reporte_sct(CUIT, "deudas", "csv").endswith(".csv")
    assert "POR_EMISOR" in nombre_consolidado(CUIT, "por_emisor", "01/01/2024", "31/01/2024").upper()


class _TabFalso:
    def __init__(self) -> None:
        self.clics = 0

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_TabFalso":
        return self

    async def is_visible(self) -> bool:
        return True

    async def click(self) -> None:
        self.clics += 1


class _DivConsultado:
    def get_by_text(self, *_: Any, **__: Any) -> _TabFalso:
        return _TabFalso()


class _BotonBuscar:
    async def click(self, *_: Any, **__: Any) -> None:
        return None


class _PaginaAgipTabs:
    """Pagina AGIP con las cinco solapas visibles y botones que descargan."""

    def locator(self, _selector: str) -> _DivConsultado:
        return _DivConsultado()

    def get_by_role(self, *_: Any, **__: Any) -> _BotonBuscar:
        return _BotonBuscar()

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_agip_empaqueta_las_salidas_con_los_nombres_de_v1(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import zipfile

    async def _ir() -> None:
        sesion = AgipSession(object(), page=_PaginaAgipTabs())
        sesion._logged_in = True  # type: ignore[attr-defined]
        sesion._representado = CUIT  # type: ignore[attr-defined]
        descargados: list[str] = []

        async def set_periodos(desde: str, hasta: str) -> None:
            assert (desde, hasta) == ("01/2024", "02/2024")

        async def esperar() -> str:
            return "con_datos"

        async def descargar(_selector: str, destino: Path) -> bool:
            Path(destino).write_bytes(b"salida")
            descargados.append(Path(destino).name)
            return True

        monkeypatch.setattr(sesion, "_set_periodos", set_periodos)
        monkeypatch.setattr(sesion, "_esperar_resultados", esperar)
        monkeypatch.setattr(sesion, "_descargar", descargar)

        destino = tmp_path / "reporte.zip"
        archivos = await sesion.consultar_retper(
            "01/2024", "02/2024", destino, denominacion="Mi Empresa"
        )

        assert [a["tipo"] for a in archivos] == [
            "RET", "RET", "RET SIFERE",
            "PER", "PER", "PER SIFERE",
            "RET bancarias", "RET bancarias", "RET bancarias SIFERE",
            "PER ADUANERAS", "PER ADUANERAS", "PER ADUANERAS SIFERE",
            "RET SIRTAC", "RET SIRTAC", "RET SIRTAC SIFERE",
        ]
        assert all(a["url_minio"] is None for a in archivos)
        assert archivos[0]["archivo"] == (
            f"{CUIT[-1]} - {CUIT} - RET - 202401 - 202402 - Mi Empresa.xls"
        )
        with zipfile.ZipFile(destino) as paquete:
            assert paquete.namelist() == descargados

    asyncio.run(_ir())


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


def test_validate_arba_acepta_los_nombres_de_v1() -> None:
    async def _ir() -> None:
        _, entrada = await ArbaPlugin().validate({
            "operacion": "descargar",
            "cuit": "20-12345678-9",
            "clave": "secreta",
            "denominacion": "Mi Empresa",
            "periodo": "202401",
            "carga_minio": False,
            "proxy_request": True,
        })
        assert entrada.representado_cuit == CUIT
        assert entrada.representado_nombre == "Mi Empresa"
        assert entrada.subir is False

    asyncio.run(_ir())


def test_validate_agip_acepta_los_nombres_de_v1() -> None:
    async def _ir() -> None:
        _, entrada = await RetperIibbAgipPlugin().validate({
            "operacion": "consultar",
            "cuit_representado": "20-12345678-9",
            "usuario": "usuario@ejemplo.test",
            "clave": "secreta",
            "denominacion": "Mi Empresa",
            "desde": "202401",
            "hasta": "202402",
            "carga_minio": False,
            "proxy_request": True,
        })
        assert entrada.representado_cuit == CUIT
        assert (entrada.periodo_desde, entrada.periodo_hasta) == ("202401", "202402")
        assert entrada.subir_archivo is False

    asyncio.run(_ir())


def test_validate_misiones_acepta_los_nombres_de_v1() -> None:
    async def _ir() -> None:
        _, entrada = await RetperIibbMisionesPlugin().validate({
            "operacion": "consultar",
            "cuit_representante": "20-12345678-9",
            "clave_representante": "secreta",
            "denominacion": "Mi Empresa",
            "desde": "202401",
            "hasta": "202402",
            "carga_minio": False,
            "proxy_request": True,
        })
        assert entrada.representado_cuit == CUIT
        assert (entrada.periodo_desde, entrada.periodo_hasta) == ("202401", "202402")
        assert entrada.subir_archivo is False

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
            # SIFERE: jurisdicciones fuera del padrón ya no es error (V1: lista
            # vacía = no consultar SIRCAR); el periodo inválido sí.
            (SiferePlugin(), {"operacion": "consultar", "representado_cuit": CUIT,
                              "periodo": "202413"}),
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
        # V1: ``archivos`` con tipo/archivo/url del comprobante subido.
        assert resultado.data["tipo"] == "RETPER ARBA"
        assert resultado.data["archivos"] == [
            {
                "tipo": "RETPER ARBA",
                "archivo": resultado.data["archivo"],
                "url_minio": "obj/retper_arba.zip",
            }
        ]
        assert resultado.data["archivo"] == nombre_archivo_descarga(CUIT, "202401", "Mi Empresa")

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
        # V1 repite la consulta completa y recien despues evalua el aviso.
        assert pagina.consultas == 2

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
            "periodo": "202401", "jurisdicciones": [901],
        })
        rts = _runtime(tmp_path, _Fabrica(_Sesion(_ServicioSifere())), {
            "sifere_sircar_xlsx_retencion_901": _slot("sifere_sircar_xlsx_retencion_901"),
            "sifere_sircar_pdf_retencion_901": _slot("sifere_sircar_pdf_retencion_901"),
            "sifere_sircar_xlsx_percepcion_901": _slot("sifere_sircar_xlsx_percepcion_901"),
            "sifere_sircar_pdf_percepcion_901": _slot("sifere_sircar_pdf_percepcion_901"),
        })
        ress = await sif.execute(pays, rts)
        assert ress.result == "OK" and len(ress.artifacts) == 4
        assert [a["artifact_id"] for a in ress.artifacts] == [
            "sifere_sircar_xlsx_retencion_901",
            "sifere_sircar_pdf_retencion_901",
            "sifere_sircar_xlsx_percepcion_901",
            "sifere_sircar_pdf_percepcion_901",
        ]

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
        # V1: un unico artefacto consolidado (.xlsx) con una hoja por consulta.
        assert len(resultado.artifacts) == 1
        assert resultado.artifacts[0]["artifact_id"] == "hacienda_consolidado"
        consolidado = resultado.data["consolidado"]
        assert consolidado["archivo"].endswith(".xlsx")
        assert consolidado["hojas"] == ["consolidado", "por_emisor"]
        assert consolidado["filas"] == 1

    asyncio.run(_ir())
