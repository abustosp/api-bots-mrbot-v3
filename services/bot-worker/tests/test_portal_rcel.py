"""Pruebas unitarias del port RCEL sin navegador ni credenciales."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.registry import get_plugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.rcel import RcelPortal, _similitud_nombre


class _Elemento:
    def __init__(self, texto: str = "") -> None:
        self.texto = texto
        self.clicks = 0
        self.valor: str | None = None

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Elemento":
        return self

    def nth(self, indice: int) -> "_Elemento":
        raise IndexError(indice)

    async def is_visible(self, **_: Any) -> bool:
        return True

    async def inner_text(self, **_: Any) -> str:
        return self.texto

    async def text_content(self, **_: Any) -> str:
        return self.texto

    async def get_attribute(self, _: str) -> str:
        return ""

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        self.clicks += 1

    async def fill(self, valor: str) -> None:
        self.valor = valor


class _Grupo:
    def __init__(self, elementos: list[_Elemento] | None = None) -> None:
        self.elementos = elementos or []

    async def count(self) -> int:
        return len(self.elementos)

    @property
    def first(self) -> _Elemento:
        return self.elementos[0]

    def nth(self, indice: int) -> _Elemento:
        return self.elementos[indice]


class _Pagina:
    def __init__(self, botones: list[_Elemento] | None = None) -> None:
        self.botones = botones or []
        self.frames: list[Any] = []
        self.main_frame = None
        self.url = "https://fe.afip.gob.ar/rcel/"

    def get_by_role(self, _: str, name: Any = None, **__: Any) -> _Grupo:
        if isinstance(name, re.Pattern):
            return _Grupo([e for e in self.botones if name.search(e.texto)])
        return _Grupo(self.botones)

    def locator(self, _: str) -> _Grupo:
        return _Grupo()

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_portal_rcel_se_registra_por_descubrimiento() -> None:
    assert portal_para("rcel") is RcelPortal
    assert "rcel" in PORTALES
    familia = get_plugin("rcel").manifest.artefactos_produce[0]
    assert familia.nombre == "rcel"
    assert familia.content_types == ("application/pdf",)


def test_match_de_denominacion_normaliza_acentos_y_texto_extra() -> None:
    assert _similitud_nombre("Compañía del Sur S.A.", "COMPANIA DEL SUR SA") == 1.0
    assert _similitud_nombre("GERMAN BOLZAN", "GERMAN BOLZAN 20-12345678-9") == 1.0
    assert _similitud_nombre("GERMAN BOLZAN", "OTRA EMPRESA") < 0.9


def test_selecciona_empresa_por_nombre_y_rechaza_cuit_distinto() -> None:
    correcta = _Elemento("Compañía del Sur S.A.")
    pagina = _Pagina([_Elemento("Otra Empresa"), correcta])
    portal = RcelPortal(pagina)

    assert asyncio.run(portal._seleccionar_por_nombre("COMPANIA DEL SUR SA"))
    assert correcta.clicks == 1

    async def encabezado() -> tuple[str, str]:
        return "20987654321", "Compañía del Sur S.A."

    portal._leer_empresa_encabezado = encabezado  # type: ignore[method-assign]
    assert not asyncio.run(portal._verificar_empresa("20123456789", "Compañía del Sur S.A."))


def test_nombre_representado_es_requerido() -> None:
    portal = RcelPortal(_Pagina())
    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20123456789"))
    assert exc.value.diagnostic_code == "rcel_company_name_missing"


def test_descargar_facturas_sin_resultados_devuelve_lista_vacia(tmp_path: Path) -> None:
    consultas = _Elemento("Consultas")
    buscar = _Elemento("Buscar")
    desde = _Elemento()
    hasta = _Elemento()

    class PaginaFormulario(_Pagina):
        def get_by_role(self, role: str, name: Any = None, **kwargs: Any) -> _Grupo:
            if role == "button" and isinstance(name, re.Pattern):
                candidatos = [consultas, buscar]
                return _Grupo([e for e in candidatos if name.search(e.texto)])
            return _Grupo()

        def get_by_label(self, nombre: str, **_: Any) -> _Elemento:
            return desde if nombre == "Desde" else hasta

    portal = RcelPortal(PaginaFormulario())
    resultado = asyncio.run(
        portal.descargar_facturas("01/01/2026", "31/01/2026", tmp_path)
    )

    assert resultado == []
    assert consultas.clicks == 1 and buscar.clicks == 1
    assert desde.valor == "01/01/2026" and hasta.valor == "31/01/2026"


# --- Extraccion de campos del PDF (port de V1 extraer_y_procesar_pdf) -------------


def _pdf_con_texto(lineas: list[str]) -> bytes:
    """PDF minimo de una pagina con ``lineas`` como texto, generado sin dependencias."""
    from io import BytesIO

    def esc(valor: str) -> str:
        return valor.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    flujo = "BT /F1 9 Tf 10 780 Td 12 TL\n" + "\n".join(
        f"({esc(linea)}) Tj T*" for linea in lineas
    ) + "\nET"
    objetos = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(flujo.encode('latin-1'))} >>\nstream\n{flujo}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = BytesIO()
    salida.write(b"%PDF-1.4\n")
    posiciones = []
    for numero, cuerpo in enumerate(objetos, start=1):
        posiciones.append(salida.tell())
        salida.write(f"{numero} 0 obj\n{cuerpo}\nendobj\n".encode("latin-1"))
    inicio_xref = salida.tell()
    salida.write(f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode())
    for pos in posiciones:
        salida.write(f"{pos:010d} 00000 n \n".encode())
    salida.write(
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n".encode()
    )
    return salida.getvalue()


FACTURA_LINEAS = [
    "Punto de Venta: 00012 Comp. Nro: 00000345",
    "Fecha de Emision: 01/09/2026",
    "CUIT: 20123456789 COD. 011",
    "Importe Neto Gravado: $ 1,000",
    "IVA 21%: $ 210",
    "Importe Total: $ 1,210",
    "CAE N\u00b0: 74123456789012",
    "Fecha de Vto. de CAE: 11/09/2026",
]


def test_extraer_campos_factura_lee_aux_y_totales() -> None:
    from bot_worker.runtime.portals.rcel import extraer_campos_factura

    campos = extraer_campos_factura(_pdf_con_texto(FACTURA_LINEAS))

    assert campos["CUIT del Emisor"] == "20123456789"
    assert campos["COD"] == "011"
    assert campos["Punto de Venta"] == "00012"
    assert campos["N\u00famero de Factura"] == "00000345"
    assert campos["AUX"] == "20123456789-011-00012-00000345"
    assert campos["IVA 21%"] == "210"
    assert campos["Importe Total"] == "1,210"
    assert campos["Vencimiento CAE"] == "11/09/2026"
    assert "B64" not in campos


def test_extraer_campos_factura_incluye_b64_si_se_pide() -> None:
    import base64

    from bot_worker.runtime.portals.rcel import extraer_campos_factura

    pdf = _pdf_con_texto(FACTURA_LINEAS)
    campos = extraer_campos_factura(pdf, incluir_b64=True)

    assert base64.b64decode(campos["B64"]) == pdf


def test_extraer_campos_factura_sin_datos_deja_campos_vacios() -> None:
    from bot_worker.runtime.portals.rcel import CAMPOS_FACTURA_VACIOS, extraer_campos_factura

    campos = extraer_campos_factura(_pdf_con_texto(["Documento sin datos fiscales"]))

    assert set(campos) == set(CAMPOS_FACTURA_VACIOS)
    assert campos["CAE"] == ""


def test_aux_sin_datos_fiscales_es_guion_triple_como_v1() -> None:
    # V1 arma AUX con f-string aunque falten partes: sin datos queda "---".
    from bot_worker.runtime.portals.rcel import extraer_campos_factura

    campos = extraer_campos_factura(_pdf_con_texto(["Documento sin datos fiscales"]))

    assert campos["AUX"] == "---"


def test_aux_con_partes_faltantes_se_arma_igual_que_v1() -> None:
    from bot_worker.runtime.portals.rcel import extraer_campos_factura

    campos = extraer_campos_factura(_pdf_con_texto(["CUIT: 20123456789 COD. 011"]))

    assert campos["AUX"] == "20123456789-011--"


def test_extraer_campos_factura_rechaza_bytes_que_no_son_pdf() -> None:
    from bot_worker.runtime.portals.rcel import extraer_campos_factura

    with pytest.raises(ValueError):
        extraer_campos_factura(b"no es un pdf")


def test_portal_extraer_campos_lee_el_pdf_descargado(tmp_path: Path) -> None:
    portal = RcelPortal(_Pagina())
    ruta = tmp_path / "comprobante_001.pdf"
    ruta.write_bytes(_pdf_con_texto(FACTURA_LINEAS))

    campos = asyncio.run(portal.extraer_campos(ruta))

    assert campos["AUX"] == "20123456789-011-00012-00000345"


def test_portal_extraer_campos_con_pdf_invalido_falla_con_diagnostico(tmp_path: Path) -> None:
    portal = RcelPortal(_Pagina())
    ruta = tmp_path / "roto.pdf"
    ruta.write_bytes(b"%PDF-roto")

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.extraer_campos(ruta))
    assert error.value.diagnostic_code == "rcel_pdf_fields_failed"


def test_plugin_rcel_extraer_campos_es_default_como_v1() -> None:
    from bot_worker.bots.rcel.schema import RcelDescargarInput

    base = {
        "representado_cuit": "20123456789",
        "representado_nombre": "EMPRESA",
        "fecha_desde": "01/09/2026",
        "fecha_hasta": "30/09/2026",
    }
    # V1 extraia campos de cada PDF siempre: el default ahora es True.
    assert RcelDescargarInput(**base).extraer_campos is True
    assert RcelDescargarInput(**base, extraer_campos=False).extraer_campos is False


# --- Paridad V1: default de extraccion y aislamiento por PDF ----------------


def test_plugin_rcel_default_extrae_campos_y_pdf_roto_no_tumba_la_corrida(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from bot_worker.bots.errors import TargetUnavailableError
    from bot_worker.bots.rcel.plugin import RcelPlugin
    from bot_worker.runtime.context import ArtifactStore

    buenos = tmp_path / "buenos"
    buenos.mkdir()
    (tmp_path / "x").mkdir()

    class _Servicio:
        async def extraer_campos(self, ruta: Path) -> dict[str, str]:
            if ruta.name == "roto.pdf":
                raise TargetUnavailableError(
                    "no se pudieron extraer los campos del PDF de RCEL",
                    diagnostic_code="rcel_pdf_fields_failed",
                )
            return {"AUX": "20123456789-011-00012-00000345"}

    class _Store:
        def __init__(self, base: Path) -> None:
            self._base = base

        def resolve(self, rel: str) -> Path:
            return self._base / rel

        async def upload(self, artifact_id: str, rel: str) -> dict[str, Any]:
            return {"artifact_id": artifact_id, "sha256": "x", "size_bytes": 1}

    plugin = RcelPlugin()
    entrada = asyncio.run(plugin.validate({
        "operacion": "descargar",
        "representado_cuit": "20123456789",
        "representado_nombre": "EMPRESA",
        "desde": "01/09/2026",
        "hasta": "30/09/2026",
        "subir_pdf": False,
    }))[1]
    # Sin pedir nada: el default de V1 es extraer campos.
    assert entrada.extraer_campos is True

    base = tmp_path / "x"
    carpeta = base / "run.descargas"
    carpeta.mkdir(parents=True)
    (carpeta / "ok.pdf").write_bytes(b"%PDF-1.4 ok")
    (carpeta / "roto.pdf").write_bytes(b"no pdf")

    class _ServicioDescarga(_Servicio):
        async def descargar_facturas(self, **_: Any) -> list[str]:
            return ["ok.pdf", "roto.pdf"]

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    runtime = SimpleNamespace(
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        artifact_store=_Store(base),
    )
    plugin_base = plugin
    entrada_run = entrada
    # Fuerza el mismo nombre base que usa el plugin para la carpeta.
    from bot_worker.bots.rcel.plugin import nombre_base_rcel

    nombre = nombre_base_rcel("20123456789", "01092026", "30092026", "EMPRESA")
    carpeta_real = base / f"{nombre}.descargas"
    carpeta_real.mkdir(parents=True, exist_ok=True)
    (carpeta_real / "ok.pdf").write_bytes(b"%PDF-1.4 ok")
    (carpeta_real / "roto.pdf").write_bytes(b"no pdf")

    datos, artefactos, errores = asyncio.run(
        plugin_base._descargar(_ServicioDescarga(), entrada_run, runtime)
    )

    # Paridad V1: el PDF roto no entra en ``facturas`` ni en ``cantidad``
    # (V1 solo agrega a ``facturas`` lo que devuelve la extraccion).
    assert datos["cantidad"] == 1
    assert [f["archivo"] for f in datos["facturas"]] == ["ok.pdf"]
    assert datos["facturas"][0]["campos"] == {"AUX": "20123456789-011-00012-00000345"}
    assert len(errores) == 1
    assert "rcel_pdf_fields_failed" in errores[0].internal_diagnostic
    assert "roto.pdf" in errores[0].internal_diagnostic



# --- Paridad V1: nombre sugerido, errores por comprobante y orden extraer-subir ---


class _Descarga:
    def __init__(self, nombre: str, contenido: bytes) -> None:
        self.suggested_filename = nombre
        self._contenido = contenido

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(self._contenido)


class _EsperaDescarga:
    """Imita ``page.expect_download``: el valor se resuelve tras el click."""

    def __init__(self, pagina: _PaginaDescargas) -> None:
        self._pagina = pagina

    async def __aenter__(self) -> _EsperaDescarga:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self) -> Any:
        async def obtener() -> _Descarga:
            ultimo = self._pagina.clicks[-1]
            if ultimo._error is not None:
                raise ultimo._error
            assert ultimo._descarga is not None
            return ultimo._descarga

        return obtener()


class _EnlacePdf:
    def __init__(self, pagina: "_PaginaDescargas", descarga: _Descarga | None, error: Exception | None = None) -> None:
        self._pagina = pagina
        self._descarga = descarga
        self._error = error

    async def click(self, **_: Any) -> None:
        self._pagina.clicks.append(self)


class _PaginaDescargas(_Pagina):
    def __init__(self, enlaces: list[tuple[_Descarga | None, Exception | None]]) -> None:
        super().__init__()
        self.clicks: list[_EnlacePdf] = []
        self._enlaces = [_EnlacePdf(self, d, e) for d, e in enlaces]

    def locator(self, _: str) -> Any:
        enlaces = self._enlaces

        class _Lista:
            async def count(self) -> int:
                return len(enlaces)

            def nth(self, indice: int) -> _EnlacePdf:
                return enlaces[indice]

        return _Lista()

    def expect_download(self, **_: Any) -> _EsperaDescarga:
        return _EsperaDescarga(self)


def test_descarga_usa_el_nombre_sugerido_por_el_portal_como_v1(tmp_path: Path) -> None:
    pagina = _PaginaDescargas(
        [(_Descarga("FAC-0001-00000345.pdf", b"%PDF-1.4 a"), None)]
    )
    portal = RcelPortal(pagina)

    nombres = asyncio.run(portal._descargar_pdfs(tmp_path))

    assert nombres == ["FAC-0001-00000345.pdf"]
    assert (tmp_path / "FAC-0001-00000345.pdf").read_bytes() == b"%PDF-1.4 a"
    assert portal.errores_descarga == []


def test_un_comprobante_que_no_se_descarga_no_corta_la_corrida(tmp_path: Path) -> None:
    pagina = _PaginaDescargas(
        [
            (None, TimeoutError("sin descarga")),
            (_Descarga("B.pdf", b"%PDF-1.4 b"), None),
        ]
    )
    portal = RcelPortal(pagina)

    nombres = asyncio.run(portal._descargar_pdfs(tmp_path))

    assert nombres == ["B.pdf"]
    assert len(portal.errores_descarga) == 1
    assert portal.errores_descarga[0].startswith("Error al descargar factura:")


def test_plugin_emite_los_errores_de_descarga_del_portal(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from bot_worker.bots.rcel.plugin import RcelPlugin
    from bot_worker.runtime.context import ArtifactStore

    class _Servicio:
        errores_descarga = ["Error al descargar factura: TimeoutError"]

        async def descargar_facturas(self, **_: Any) -> list[str]:
            return []

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    plugin = RcelPlugin()
    entrada = asyncio.run(plugin.validate({
        "operacion": "descargar",
        "representado_cuit": "20123456789",
        "representado_nombre": "EMPRESA",
        "desde": "01/09/2026",
        "hasta": "30/09/2026",
        "subir_pdf": False,
    }))[1]
    runtime = SimpleNamespace(
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        artifact_store=ArtifactStore(tmp_path, slots={}),
    )

    datos, _, errores = asyncio.run(plugin._descargar(_Servicio(), entrada, runtime))

    assert datos["cantidad"] == 0
    assert [e.internal_diagnostic for e in errores] == [
        "Error al descargar factura: TimeoutError"
    ]


def test_plugin_extrae_campos_antes_de_subir_y_no_sube_pdf_sin_campos(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from bot_worker.bots.errors import TargetUnavailableError
    from bot_worker.bots.rcel.plugin import RcelPlugin, nombre_base_rcel

    orden: list[str] = []

    class _Servicio:
        errores_descarga: list[str] = []

        async def descargar_facturas(self, **_: Any) -> list[str]:
            return ["ok.pdf", "roto.pdf"]

        async def extraer_campos(self, ruta: Path) -> dict[str, str]:
            orden.append(f"extraer:{ruta.name}")
            if ruta.name == "roto.pdf":
                raise TargetUnavailableError(
                    "no se pudieron extraer los campos del PDF de RCEL",
                    diagnostic_code="rcel_pdf_fields_failed",
                )
            return {"AUX": "20123456789-011-00012-00000345"}

    class _Store(ArtifactStoreFalso):
        async def upload(self, artifact_id: str, rel: str) -> dict[str, Any]:
            orden.append(f"subir:{rel.rsplit('/', 1)[-1]}")
            return {"artifact_id": artifact_id, "sha256": "x", "size_bytes": 1}

    base = tmp_path / nombre_base_rcel("20123456789", "01092026", "30092026", "EMPRESA")
    carpeta = Path(str(base) + ".descargas")
    carpeta.mkdir(parents=True)
    (carpeta / "ok.pdf").write_bytes(b"%PDF-1.4 ok")
    (carpeta / "roto.pdf").write_bytes(b"no pdf")

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    plugin = RcelPlugin()
    entrada = asyncio.run(plugin.validate({
        "operacion": "descargar",
        "representado_cuit": "20123456789",
        "representado_nombre": "EMPRESA",
        "desde": "01/09/2026",
        "hasta": "30/09/2026",
        "subir_pdf": True,
    }))[1]
    runtime = SimpleNamespace(
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        artifact_store=_Store(tmp_path),
    )

    datos, artefactos, errores = asyncio.run(
        plugin._descargar(_Servicio(), entrada, runtime)
    )

    # Como V1: extrae antes de subir, y el PDF que falla no se sube ni se lista.
    assert orden == ["extraer:ok.pdf", "subir:ok.pdf", "extraer:roto.pdf"]
    assert [f["archivo"] for f in datos["facturas"]] == ["ok.pdf"]
    assert len(artefactos) == 1
    assert len(errores) == 1


class ArtifactStoreFalso:
    """Store minimo: resuelve rutas bajo ``base`` sin presign real."""

    def __init__(self, base: Path) -> None:
        self._base = base

    def resolve(self, rel: str) -> Path:
        return self._base / rel
