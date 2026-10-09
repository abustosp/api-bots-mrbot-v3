"""Portal SIPER (padrón PUC / conducta fiscal): capturas de detalle y categorías.

Port de ``api-bots-mrbot/app/bot/siper_bot.py`` (V2). El plugin V3 pide
``capturar_vista(vista, destino)`` por cada vista habilitada.
"""
from __future__ import annotations

import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

BASE_SETI = "https://seti.afip.gob.ar/padron-puc-consulta-internet"
URL_DETALLE = f"{BASE_SETI}/ResponsiveIndexInternetAction.do"
URL_CATEGORIAS = (
    f"{BASE_SETI}/gov.afip.padron.puc.conductafiscal.gwt.ConductaFiscal/"
    "ConductaFiscalAction.do"
)
RUTA_CONSULTA = "uv-index.html"

# Etiqueta de la fila (``td.conductaFiscalName``) -> clave del resumen (port de V1).
CAMPOS_DETALLE: dict[str, str] = {
    "Contribuyente": "contribuyente",
    "Categoría SIPER": "categoria_siper",
    "Motivo": "motivo",
    "Fecha última Categoría SIPER": "fecha_ultima_categoria_siper",
    "Estado trámite de disconformidad": "estado_tramite_disconformidad",
}


def _normalizar_texto(texto: str) -> str:
    return " ".join(texto.split())


def _clave_etiqueta(texto: str) -> str:
    """Compara etiquetas sin acentos ni mayúsculas: el portal cambia su forma."""
    plano = unicodedata.normalize("NFKD", texto)
    sin_acentos = "".join(c for c in plano if not unicodedata.combining(c))
    return _normalizar_texto(sin_acentos).lower()


class _DetalleHTML(HTMLParser):
    """Junta la etiqueta (``td.conductaFiscalName``) y el valor que la sigue.

    El valor (``td.conductaFiscalValue``) puede tener varios ``div`` o ``span``;
    se concatena su texto normalizado.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._en_nombre = False
        self._en_valor = False
        self._etiqueta: list[str] = []
        self._valor: list[str] = []
        self._pendiente = ""
        self.valores: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        clases = (dict(attrs).get("class") or "").split()
        if tag == "td" and "conductaFiscalName" in clases:
            self._en_nombre = True
            self._etiqueta = []
        elif tag == "td" and "conductaFiscalValue" in clases:
            self._en_valor = True
            self._valor = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "td":
            return
        if self._en_nombre:
            self._en_nombre = False
            self._pendiente = _normalizar_texto("".join(self._etiqueta))
        elif self._en_valor:
            self._en_valor = False
            if self._pendiente:
                self.valores.setdefault(self._pendiente, _normalizar_texto("".join(self._valor)))
            self._pendiente = ""

    def handle_data(self, data: str) -> None:
        if self._en_nombre:
            self._etiqueta.append(data)
        elif self._en_valor:
            self._valor.append(data)


def extraer_detalle_siper(html: str) -> dict[str, str]:
    """Campos de la conducta fiscal a partir del HTML del detalle SIPER (port de V1).

    Devuelve solo los campos con valor no vacío.
    """
    parser = _DetalleHTML()
    parser.feed(html or "")
    parser.close()
    por_clave = {_clave_etiqueta(k): v for k, v in parser.valores.items()}
    resumen: dict[str, str] = {}
    for etiqueta, clave in CAMPOS_DETALLE.items():
        valor = por_clave.get(_clave_etiqueta(etiqueta), "")
        if valor:
            resumen[clave] = valor
    return resumen


class SiperPortal(PortalArca):
    """Capturas de conducta fiscal (SIPER) del CUIT representado."""

    nombre = "siper"

    async def seleccionar_representado(self, cuit: str) -> None:
        """En SIPER el representado viaja en la URL de la consulta.

        Si el portal muestra el selector se usa; si no, no es un error.
        """
        try:
            await super().seleccionar_representado(cuit)
        except TargetUnavailableError:
            return

    async def extraer_campos_detalle(self, representado_cuit: str) -> dict[str, str]:
        """Abre el detalle SIPER y devuelve sus campos (contribuyente, categoría, motivo...).

        Usa el mismo HTML que la captura PNG, sin navegar de nuevo si ya estaba abierto.
        """
        cuit = solo_digitos(representado_cuit)
        if len(cuit) != 11:
            raise TargetUnavailableError(
                "CUIT inválido para el detalle SIPER",
                diagnostic_code="siper_cuit_invalid",
            )
        await self.paso("abrir_detalle", self._abrir(f"{URL_DETALLE}?idPersona={cuit}"))
        html = await self.paso("leer_detalle", self._html_seccion())
        campos = extraer_detalle_siper(html)
        if not campos:
            raise TargetUnavailableError(
                "el detalle SIPER no trajo campos de conducta fiscal",
                diagnostic_code="siper_detalle_vacio",
            )
        return campos

    async def _html_seccion(self) -> str:
        try:
            return str(await self.page.locator("#sectionContainer").first.inner_html() or "")
        except Exception:
            return ""

    async def capturar_vista(
        self, vista: str, destino: Path, representado_cuit: str = ""
    ) -> dict[str, Any]:
        """Navega a la vista pedida y guarda la captura en ``destino``."""
        vista = str(vista or "").strip().lower()
        if vista not in ("detalle", "categorias"):
            raise TargetUnavailableError(
                f"vista SIPER desconocida: {vista}",
                diagnostic_code="siper_vista_desconocida",
            )
        cuit = solo_digitos(representado_cuit)
        if vista == "detalle":
            if len(cuit) != 11:
                raise TargetUnavailableError(
                    "CUIT inválido para el detalle SIPER",
                    diagnostic_code="siper_cuit_invalid",
                )
            await self.paso("abrir_detalle", self._abrir(f"{URL_DETALLE}?idPersona={cuit}"))
        else:
            await self.paso("abrir_categorias", self._abrir(URL_CATEGORIAS))
        return await self.paso("capturar", self._capturar(destino, vista))

    # ------------------------------------------------------------------

    async def _abrir(self, url: str) -> None:
        await self.abrir_url(url, timeout_ms=45_000)
        # El padrón es una SPA: se espera el contenedor de la sección.
        for _ in range(30):
            try:
                if await self.page.locator("#sectionContainer").count():
                    break
            except Exception:
                break
            await self._esperar(500)
        await self._esperar(1_200)

    async def _capturar(self, destino: Path, vista: str) -> dict[str, Any]:
        destino = Path(destino)
        contenedor = self.page.locator("#sectionContainer")
        try:
            await contenedor.first.wait_for(state="visible", timeout=15_000)
            objetivo = contenedor.first
        except Exception:
            if await contenedor.count() == 0:
                objetivo = self.page.locator("body")
            else:
                raise TargetUnavailableError(
                    "la vista SIPER no quedó visible para capturar",
                    diagnostic_code="siper_vista_no_visible",
                ) from None
        await objetivo.screenshot(path=str(destino))
        if not destino.exists() or destino.stat().st_size == 0:
            raise TargetUnavailableError(
                "la captura SIPER salió vacía",
                diagnostic_code="siper_captura_vacia",
            )
        ancho, alto = _dimensiones_png(destino)
        return {"vista": vista, "ancho": ancho, "alto": alto, "archivo": destino.name}


def _dimensiones_png(ruta: Path) -> tuple[int, int]:
    """Ancho y alto de un PNG desde su cabecera IHDR (sin dependencias)."""
    try:
        with open(ruta, "rb") as fh:
            cabecera = fh.read(24)
        if cabecera[:8] != b"\x89PNG\r\n\x1a\n" or cabecera[12:16] != b"IHDR":
            return 0, 0
        return (
            int.from_bytes(cabecera[16:20], "big"),
            int.from_bytes(cabecera[20:24], "big"),
        )
    except Exception:
        return 0, 0
