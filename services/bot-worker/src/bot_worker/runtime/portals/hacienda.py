"""Portal de Hacienda y Carne - Liquidación, iniciado desde Mis Comprobantes.

Porta el flujo de ``hacienda_bot.py``: selección de denominación en
Comprobantes en Línea, doble apertura del servicio de Hacienda y consultas
de liquidaciones por emisor o receptor.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, TABLAS_JS

_SERVICIO_HACIENDA = re.compile(r"Hacienda\s+y\s+Carne\s*-\s*Liquidaci", re.I)
_CONSULTA_EMISOR = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Emisor", re.I
)
_CONSULTA_RECEPTOR = re.compile(
    r"Consulta\s+y\s+ajuste\s+de\s+Liquidaciones\s*-\s*Por\s+Receptor", re.I
)
_SELECTOR_DESCARGA = (
    "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, a[onclick*='.pdf'], "
    "a[href*='.pdf'], a[title*='Imprimir'], a.btnImprimir, a[title*='Descargar'], "
    "button[onclick*='pd.jsp'], button[onclick*='.pdf'], button[title*='Imprimir'], "
    "button[onclick*='descarg'], [role='button'][onclick*='pd.jsp'], "
    "[role='button'][onclick*='.pdf']"
)

# Namespaces OpenXML del escritor XLSX stdlib (mismo esquema que el escritor de
# ``mis_facilidades``: celdas ``inlineStr``, sin estilos ni formulas).
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_XML = "http://www.w3.org/XML/1998/namespace"
MAX_NOMBRE_HOJA = 31


def letra_columna(numero: int) -> str:
    """Letra de columna estilo Excel para el número 1-based indicado."""
    letras = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def escribir_xlsx(
    destino: Path, hojas: list[tuple[str, list[str], list[list[str]]]]
) -> None:
    """Escribe un XLSX OpenXML mínimo con una o más hojas (stdlib, sin pandas).

    Port del escritor de ``runtime/portals/mis_facilidades.py``: mismo esquema
    de celdas ``inlineStr`` y mismos nombres de partes, para no depender de
    pandas ni de openpyxl en el worker. Los valores se escriben como texto, que
    es lo que devuelven las tablas HTML del portal.
    """
    ET.register_namespace("", _NS_MAIN)
    ET.register_namespace("r", _NS_REL_DOC)
    workbook = ET.Element(f"{{{_NS_MAIN}}}workbook")
    sheets = ET.SubElement(workbook, f"{{{_NS_MAIN}}}sheets")
    for idx, (nombre, _, _) in enumerate(hojas, start=1):
        ET.SubElement(
            sheets,
            f"{{{_NS_MAIN}}}sheet",
            {"name": nombre, "sheetId": str(idx), f"{{{_NS_REL_DOC}}}id": f"rId{idx}"},
        )
    workbook_xml = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)

    rels = ET.Element(f"{{{_NS_REL_PKG}}}Relationships")
    for idx in range(1, len(hojas) + 1):
        ET.SubElement(
            rels,
            f"{{{_NS_REL_PKG}}}Relationship",
            {
                "Id": f"rId{idx}",
                "Type": (
                    "http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/worksheet"
                ),
                "Target": f"worksheets/sheet{idx}.xml",
            },
        )
    rels_xml = ET.tostring(rels, encoding="utf-8", xml_declaration=True)

    tipos = ET.Element(f"{{{_NS_CONTENT}}}Types")
    ET.SubElement(
        tipos,
        f"{{{_NS_CONTENT}}}Default",
        {
            "Extension": "rels",
            "ContentType": "application/vnd.openxmlformats-package.relationships+xml",
        },
    )
    ET.SubElement(
        tipos,
        f"{{{_NS_CONTENT}}}Default",
        {"Extension": "xml", "ContentType": "application/xml"},
    )
    ET.SubElement(
        tipos,
        f"{{{_NS_CONTENT}}}Override",
        {
            "PartName": "/xl/workbook.xml",
            "ContentType": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet.main+xml"
            ),
        },
    )
    for idx in range(1, len(hojas) + 1):
        ET.SubElement(
            tipos,
            f"{{{_NS_CONTENT}}}Override",
            {
                "PartName": f"/xl/worksheets/sheet{idx}.xml",
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.worksheet+xml"
                ),
            },
        )
    content_types_xml = ET.tostring(tipos, encoding="utf-8", xml_declaration=True)

    root_rels = ET.Element(f"{{{_NS_REL_PKG}}}Relationships")
    ET.SubElement(
        root_rels,
        f"{{{_NS_REL_PKG}}}Relationship",
        {
            "Id": "rId1",
            "Type": (
                "http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships/officeDocument"
            ),
            "Target": "xl/workbook.xml",
        },
    )
    root_rels_xml = ET.tostring(root_rels, encoding="utf-8", xml_declaration=True)

    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr("[Content_Types].xml", content_types_xml)
        archivo.writestr("_rels/.rels", root_rels_xml)
        archivo.writestr("xl/workbook.xml", workbook_xml)
        archivo.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        for idx, (_, headers, filas) in enumerate(hojas, start=1):
            worksheet = ET.Element(f"{{{_NS_MAIN}}}worksheet")
            sheet_data = ET.SubElement(worksheet, f"{{{_NS_MAIN}}}sheetData")
            for numero_fila, valores in enumerate([headers, *filas], start=1):
                row_node = ET.SubElement(
                    sheet_data, f"{{{_NS_MAIN}}}row", {"r": str(numero_fila)}
                )
                for numero_columna, valor in enumerate(valores, start=1):
                    celda = ET.SubElement(
                        row_node,
                        f"{{{_NS_MAIN}}}c",
                        {
                            "r": f"{letra_columna(numero_columna)}{numero_fila}",
                            "t": "inlineStr",
                        },
                    )
                    inline = ET.SubElement(celda, f"{{{_NS_MAIN}}}is")
                    texto = ET.SubElement(
                        inline, f"{{{_NS_MAIN}}}t", {f"{{{_NS_XML}}}space": "preserve"}
                    )
                    texto.text = str(valor)
            archivo.writestr(
                f"xl/worksheets/sheet{idx}.xml",
                ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
            )


def normalizar_nombre_hoja(nombre: str, usados: set[str]) -> str:
    """Nombre de hoja válido (31 chars, sin ``[]:*?/\\``) y único."""
    base = re.sub(r"[\\/*?:\[\]]", "_", str(nombre))[:MAX_NOMBRE_HOJA]
    base = base or "Hoja"
    candidato = base
    sufijo = 2
    while candidato.lower() in usados:
        cola = f" {sufijo}"
        candidato = f"{base[: MAX_NOMBRE_HOJA - len(cola)]}{cola}"
        sufijo += 1
    usados.add(candidato.lower())
    return candidato


def extraer_data_id(texto: str) -> str:
    """Id del comprobante para ``getPdf`` (port V1 ``extract_data_id``)."""
    for patron in (
        r'data-id(?:comprobante)?[=:\\"\s]+(\d+)',
        r"getPdf\?id=(\d+)",
        r"[?&](?:id|idComprobante|idLiquidacion)=(\d+)",
    ):
        coincidencia = re.search(patron, texto or "", flags=re.I)
        if coincidencia:
            return coincidencia.group(1)
    return ""


def extraer_clave_fila(texto: str) -> str:
    """Clave estable de la fila para no bajar dos veces el mismo PDF (port V1)."""
    for patron, etiqueta in (
        (r"liqCodigo=(\d+)", "liqCodigo"),
        (r"coe=(\d+)", "coe"),
        (r"codigo=(\d+)", "codigo"),
        (r"\b(\d{6,})\b", "numero"),
    ):
        coincidencia = re.search(patron, texto or "", flags=re.I)
        if coincidencia:
            return f"{etiqueta}={coincidencia.group(1)}"
    return ""


def inferir_identificadores(texto: str) -> tuple[str, str]:
    """CUIT de referencia y código de comprobante del texto de la fila (port V1)."""
    digitos = re.findall(r"\b\d{6,20}\b", texto or "")
    cuit = next((d for d in digitos if len(d) == 11), "SIN_CUIT")
    codigo = next((d for d in digitos if len(d) >= 8 and d != cuit), "")
    return cuit, codigo or "SIN_CODIGO"


def nombre_pdf_hacienda(consulta: str, cuit_representado: str, cuit_ref: str, codigo: str) -> str:
    """Nombre V1 del PDF: ``HACIENDA - <CONSULTA> - <CUIT> - <CUIT_REF> - <CODIGO>.pdf``."""
    crudo = f"HACIENDA - {consulta.upper()} - {cuit_representado} - {cuit_ref} - {codigo}.pdf"
    limpio = re.sub(r"[\\/:*?\"<>|]+", "_", crudo)
    return re.sub(r"\s+", " ", limpio).strip()


_SIGUIENTE = re.compile(r"^\s*(Siguiente|Sig\.?|Next|>|>>|›|»)\s*$", re.I)
_CONSULTAS = {
    "por_emisor": _CONSULTA_EMISOR,
    "por_receptor": _CONSULTA_RECEPTOR,
}


def _normalizar_nombre(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in plano if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", sin_acentos).upper().split())


def _similitud_nombre(esperado: str, encontrado: str) -> float:
    pedido = _normalizar_nombre(esperado)
    candidato = _normalizar_nombre(encontrado)
    if not pedido or not candidato:
        return 0.0
    if pedido == candidato or pedido in candidato:
        return 1.0

    def cobertura(origen: list[str], destino: list[str]) -> float:
        if not origen or not destino:
            return 0.0
        return sum(
            max(SequenceMatcher(None, token, otro).ratio() for otro in destino)
            for token in origen
        ) / len(origen)

    tokens_pedido = pedido.split()
    tokens_candidato = candidato.split()
    dirigida = cobertura(tokens_pedido, tokens_candidato)
    inversa = cobertura(tokens_candidato, tokens_pedido)
    return (
        0.55 * dirigida
        + 0.25 * min(dirigida, inversa)
        + 0.20 * SequenceMatcher(None, pedido, candidato).ratio()
    )


class HaciendaPortal(PortalArca):
    """Acciones de consulta de liquidaciones de hacienda."""

    nombre = "hacienda"

    def _contextos(self) -> list[Any]:
        contextos = [self.page]
        try:
            marco_principal = getattr(self.page, "main_frame", None)
            for marco in self.page.frames:
                if marco is not marco_principal:
                    contextos.append(marco)
        except Exception:
            pass
        return contextos

    async def _texto_candidato(self, candidato: Any) -> str:
        for extractor in (candidato.inner_text, candidato.text_content):
            try:
                texto = " ".join(str(await extractor() or "").split())
            except Exception:
                texto = ""
            if texto:
                return texto
        for atributo in ("aria-label", "title", "value", "name"):
            try:
                texto = str(await candidato.get_attribute(atributo) or "").strip()
            except Exception:
                texto = ""
            if texto:
                return texto
        return ""

    async def _click_candidato(self, candidato: Any) -> bool:
        try:
            await candidato.scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        for opciones in ({"timeout": 8_000}, {"timeout": 8_000, "force": True}):
            try:
                await candidato.click(**opciones)
                try:
                    await self.page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return True
            except Exception:
                continue
        try:
            handle = await candidato.element_handle()
            if handle is not None:
                await handle.evaluate("el => el.click()")
                return True
        except Exception:
            pass
        return False

    async def seleccionar_denominacion(self, denominacion: str) -> None:
        """Selecciona en Mis Comprobantes la denominación solicitada."""
        nombre = " ".join(str(denominacion or "").split())
        if not nombre:
            raise TargetUnavailableError(
                "falta la denominación del representado",
                diagnostic_code="represented_name_missing",
            )
        await self.paso(
            "seleccionar_denominacion", self._seleccionar_denominacion(nombre)
        )

    async def _seleccionar_denominacion(self, nombre: str) -> None:
        patron_exacto = re.compile(rf"^{re.escape(nombre)}$", re.I)
        for contexto in self._contextos():
            for rol, patron, exacto in (
                ("button", nombre, True),
                ("link", nombre, True),
                ("button", patron_exacto, False),
                ("link", patron_exacto, False),
            ):
                try:
                    grupo = contexto.get_by_role(rol, name=patron, exact=exacto)
                    for indice in range(await grupo.count()):
                        if await self._click_candidato(grupo.nth(indice)):
                            return
                except Exception:
                    continue

        candidatos: list[tuple[float, bool, Any]] = []
        for contexto in self._contextos():
            grupos = (
                contexto.get_by_role("button"),
                contexto.get_by_role("link"),
                contexto.locator("[role='button'], button, a, [onclick]"),
            )
            for grupo in grupos:
                try:
                    cantidad = await grupo.count()
                except Exception:
                    continue
                for indice in range(cantidad):
                    elemento = grupo.nth(indice)
                    texto = await self._texto_candidato(elemento)
                    puntaje = _similitud_nombre(nombre, texto)
                    if puntaje < 0.90:
                        continue
                    try:
                        visible = await elemento.is_visible()
                    except Exception:
                        visible = False
                    candidatos.append((puntaje, visible, elemento))

        candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
        for _, _, candidato in candidatos:
            if await self._click_candidato(candidato):
                return
        raise TargetUnavailableError(
            "no se encontró una denominación coincidente en Mis Comprobantes",
            diagnostic_code="represented_name_not_selectable",
        )

    async def _boton_servicio(self) -> Any | None:
        for contexto in self._contextos():
            for rol in ("button", "link"):
                try:
                    grupo = contexto.get_by_role(rol, name=_SERVICIO_HACIENDA)
                    for indice in range(await grupo.count()):
                        candidato = grupo.nth(indice)
                        try:
                            if await candidato.is_visible():
                                return candidato
                        except Exception:
                            continue
                except Exception:
                    continue
        return None

    async def _abrir_servicio(self, boton: Any) -> Any:
        pagina_anterior = self.page
        try:
            async with pagina_anterior.expect_popup(timeout=12_000) as evento:
                await boton.click(timeout=12_000)
            destino = await evento.value
        except Exception as exc:
            if type(exc).__name__ not in {"TimeoutError", "PlaywrightTimeoutError"}:
                raise
            destino = pagina_anterior
        try:
            await destino.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass
        try:
            await destino.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        return destino

    async def abrir_hacienda(
        self, *, servicio: str = "Hacienda y Carne - Liquidacion", denominacion: str
    ) -> "HaciendaPortal":
        """Reproduce la doble apertura V2 para activar la vista de Hacienda."""
        del servicio  # ARCA muestra un único servicio con este nombre.
        return await self.paso(
            "abrir_hacienda", self._abrir_hacienda(denominacion)
        )

    async def _abrir_hacienda(self, denominacion: str) -> "HaciendaPortal":
        primer_boton = await self._boton_servicio()
        if primer_boton is None:
            raise TargetUnavailableError(
                "servicio Hacienda y Carne no visible en Mis Comprobantes",
                diagnostic_code="arca_service_not_visible",
            )
        primera_pagina = await self._abrir_servicio(primer_boton)
        if primera_pagina is not self.page:
            try:
                await primera_pagina.close()
            except Exception:
                pass

        segundo_boton = await self._boton_servicio()
        if segundo_boton is None:
            raise TargetUnavailableError(
                "servicio Hacienda y Carne no visible para la segunda apertura",
                diagnostic_code="arca_service_not_visible",
            )
        segunda_pagina = await self._abrir_servicio(segundo_boton)
        portal = type(self)(
            segunda_pagina,
            captcha=self._captcha,
            context=self._context,
            service_name=self._service_name,
        )
        await portal.seleccionar_denominacion(denominacion)
        await portal._abrir_menu_si_necesario()
        return portal

    async def _abrir_menu_si_necesario(self, *, forzar: bool = False) -> None:
        url = str(getattr(self.page, "url", "") or "")
        if not forzar and not re.search(r"inicioPerfil\.htm|/lsp-web/inicio$", url, re.I):
            return
        try:
            wrapper = self.page.locator("#wrapper")
            clases = str(await wrapper.first.get_attribute("class") or "").lower()
            if "toggled" in clases:
                return
        except Exception:
            pass
        for selector in (
            "#buttonSidebar button.hamburger[data-toggle='offcanvas']",
            "button.hamburger[data-toggle='offcanvas']",
            "button[data-toggle='offcanvas']",
            "button.navbar-toggle",
        ):
            try:
                boton = self.page.locator(selector)
                if await boton.count() and await self._clickear((boton,), total_ms=2_000):
                    await self._esperar(350)
                    return
            except Exception:
                continue

    async def consultar(
        self, *, consulta_key: str, desde: str, hasta: str
    ) -> list[dict[str, Any]]:
        """Consulta liquidaciones por emisor/receptor y devuelve filas tabulares."""
        patron = _CONSULTAS.get(str(consulta_key or "").strip().lower())
        if patron is None:
            raise TargetUnavailableError(
                "tipo de consulta Hacienda no reconocido",
                diagnostic_code="hacienda_query_invalid",
            )
        await self.paso(
            "abrir_seccion_consulta",
            self._abrir_seccion_consulta(consulta_key, patron),
        )
        await self.paso(
            "completar_fechas",
            self._completar_fechas_y_buscar(consulta_key, desde, hasta),
        )
        return await self.paso("leer_resultados", self._leer_resultados(consulta_key))

    async def descargar_pdfs_consulta(
        self,
        *,
        consulta_key: str,
        desde: str,
        hasta: str,
        destino_dir: Path,
        representado_cuit: str,
    ) -> list[dict[str, Any]]:
        """Recorre las páginas de resultados y baja el PDF de cada comprobante.

        Port de ``descargar_todas_las_paginas`` (V1). Los PDF se guardan en
        ``destino_dir`` con el nombre V1 ``HACIENDA - <CONSULTA> - <CUIT> -
        <CUIT_REF> - <CODIGO>.pdf``. Los comprobantes que no se pueden bajar
        no detienen la consulta: quedan en la lista de salida con ``error``.
        """
        patron = _CONSULTAS.get(str(consulta_key or "").strip().lower())
        if patron is None:
            raise TargetUnavailableError(
                "tipo de consulta Hacienda no reconocido",
                diagnostic_code="hacienda_query_invalid",
            )
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        await self.paso(
            "abrir_seccion_consulta",
            self._abrir_seccion_consulta(consulta_key, patron),
        )
        await self.paso(
            "completar_fechas",
            self._completar_fechas_y_buscar(consulta_key, desde, hasta),
        )
        return await self.paso(
            "descargar_pdfs",
            self._recorrer_y_descargar(consulta_key, representado_cuit, destino_dir),
        )

    async def recorrer_consulta(
        self,
        *,
        consulta_key: str,
        desde: str,
        hasta: str,
        destino_dir: Path | None = None,
        representado_cuit: str = "",
        descargar_pdf: bool = True,
    ) -> dict[str, Any]:
        """Recorre una consulta una sola vez juntando filas y PDF (port de V1).

        V1 (``descargar_todas_las_paginas``) navega la consulta una vez y de
        cada página guarda el cuadro (para el Excel consolidado) y baja el PDF
        de cada comprobante. Devuelve ``{"filas": [...], "pdfs": [...] | None}``.
        """
        patron = _CONSULTAS.get(str(consulta_key or "").strip().lower())
        if patron is None:
            raise TargetUnavailableError(
                "tipo de consulta Hacienda no reconocido",
                diagnostic_code="hacienda_query_invalid",
            )
        bajar_pdf = bool(descargar_pdf) and destino_dir is not None
        if bajar_pdf:
            destino = Path(destino_dir)  # type: ignore[arg-type]
            destino.mkdir(parents=True, exist_ok=True)
        await self.paso(
            "abrir_seccion_consulta",
            self._abrir_seccion_consulta(consulta_key, patron),
        )
        await self.paso(
            "completar_fechas",
            self._completar_fechas_y_buscar(consulta_key, desde, hasta),
        )
        filas: list[dict[str, Any]] = []
        pdfs: list[dict[str, Any]] = []
        claves: set[str] = set()
        firmas: set[str] = set()
        for pagina in range(1, 101):
            actuales = self._filas_de_tablas(
                await self._tablas_actuales(), consulta_key, pagina
            )
            firma = repr(
                [
                    {clave: valor for clave, valor in fila.items() if clave != "pagina"}
                    for fila in actuales
                ]
            )
            if firma and firma in firmas:
                break
            if firma:
                firmas.add(firma)
            filas.extend(actuales)
            if bajar_pdf:
                pdfs.extend(
                    await self._pdfs_de_pagina(
                        consulta_key,
                        representado_cuit,
                        Path(destino_dir),  # type: ignore[arg-type]
                        pagina,
                        claves,
                    )
                )
            if not await self._siguiente_pagina():
                break
        return {"filas": filas, "pdfs": pdfs if bajar_pdf else None}

    async def _pdfs_de_pagina(
        self,
        consulta_key: str,
        representado_cuit: str,
        destino_dir: Path,
        pagina: int,
        vistos: set[str],
    ) -> list[dict[str, Any]]:
        """Baja los PDF de los comprobantes de la página actual (port V1)."""
        resultados: list[dict[str, Any]] = []
        for fila in await self._filas_con_descarga():
            clave = fila["clave"]
            if clave in vistos:
                continue
            vistos.add(clave)
            nombre = nombre_pdf_hacienda(
                consulta_key, representado_cuit, fila["cuit_ref"], fila["codigo"]
            )
            entrada: dict[str, Any] = {
                "consulta": consulta_key,
                "pagina": pagina,
                "archivo": nombre,
            }
            try:
                ruta = await self._bajar_pdf_fila(fila, destino_dir / nombre)
                entrada["ruta"] = str(ruta)
            except TargetUnavailableError as exc:
                entrada["error"] = exc.diagnostic_code
            resultados.append(entrada)
        return resultados

    async def _recorrer_y_descargar(
        self, consulta_key: str, representado_cuit: str, destino_dir: Path
    ) -> list[dict[str, Any]]:
        resultados: list[dict[str, Any]] = []
        vistos: set[str] = set()
        firmas_paginas: set[str] = set()
        for pagina in range(1, 101):
            filas = await self._filas_con_descarga()
            # Guarda contra bucles: si la página repite las mismas filas, se corta
            # aunque "Siguiente" siga visible (port del control de firmas de V1).
            firma = "|".join(sorted(f["clave"] for f in filas))
            if firma and firma in firmas_paginas:
                break
            if firma:
                firmas_paginas.add(firma)
            for fila in filas:
                clave = fila["clave"]
                if clave in vistos:
                    continue
                vistos.add(clave)
                nombre = nombre_pdf_hacienda(
                    consulta_key, representado_cuit, fila["cuit_ref"], fila["codigo"]
                )
                entrada: dict[str, Any] = {
                    "consulta": consulta_key,
                    "pagina": pagina,
                    "archivo": nombre,
                }
                try:
                    ruta = await self._bajar_pdf_fila(fila, destino_dir / nombre)
                    entrada["ruta"] = str(ruta)
                except TargetUnavailableError as exc:
                    entrada["error"] = exc.diagnostic_code
                resultados.append(entrada)
            if not await self._siguiente_pagina():
                break
        return resultados

    async def _filas_con_descarga(self) -> list[dict[str, Any]]:
        """Filas de la página actual con un control de descarga (port V1)."""
        filas: list[dict[str, Any]] = []
        for contexto in self._contextos():
            try:
                grupo = contexto.locator("#resultTable tbody tr")
                cantidad = await grupo.count()
            except Exception:
                continue
            for indice in range(cantidad):
                fila = grupo.nth(indice)
                try:
                    control = fila.locator(_SELECTOR_DESCARGA).first
                    if await control.count() == 0:
                        continue
                    texto = await self._texto_candidato(fila)
                except Exception:
                    continue
                onclick = str(await control.get_attribute("onclick") or "")
                href = str(await control.get_attribute("href") or "")
                data_id = extraer_data_id(" ".join((onclick, href, texto)))
                clave = extraer_clave_fila(" ".join((onclick, href, texto))) or f"fila_{indice + 1}"
                cuit_ref, codigo = inferir_identificadores(texto)
                filas.append(
                    {
                        "clave": clave,
                        "control": control,
                        "data_id": data_id,
                        "cuit_ref": cuit_ref,
                        "codigo": codigo,
                        "texto": texto,
                    }
                )
            if filas:
                break
        return filas

    async def _bajar_pdf_fila(self, fila: dict[str, Any], destino: Path) -> Path:
        """Primero por ``getPdf?id=`` del mismo origen; si falla, click y descarga."""
        if fila.get("data_id"):
            url = urljoin(str(getattr(self.page, "url", "") or ""), f"/lsp-web/consultaLiquidacion/getPdf?id={fila['data_id']}")
            try:
                respuesta = await self.page.request.get(url, timeout=60_000)
                if respuesta.status == 200:
                    cuerpo = await respuesta.body()
                    if cuerpo[:5] == b"%PDF-":
                        destino.write_bytes(cuerpo)
                        return destino
            except Exception:
                pass
        control = fila["control"]
        try:
            return await self.capturar_descarga(destino, lambda: control.click(timeout=15_000), espera_ms=30_000)
        except TargetUnavailableError:
            raise TargetUnavailableError(
                "no se pudo descargar el PDF del comprobante",
                diagnostic_code="hacienda_pdf_unavailable",
            )

    async def _buscar_consulta(self, patron: re.Pattern[str]) -> Any | None:
        for contexto in self._contextos():
            try:
                grupo = contexto.get_by_role("link", name=patron)
                if await grupo.count():
                    return grupo.first
            except Exception:
                pass
            try:
                grupo = contexto.locator("a").filter(has_text=patron)
                if await grupo.count():
                    return grupo.first
            except Exception:
                pass
        return None

    async def _abrir_seccion_consulta(
        self, consulta_key: str, patron: re.Pattern[str]
    ) -> None:
        await self._abrir_menu_si_necesario()
        link = await self._buscar_consulta(patron)
        if link is None:
            await self._abrir_menu_si_necesario(forzar=True)
            link = await self._buscar_consulta(patron)
        if link is None:
            raise TargetUnavailableError(
                f"no se encontró la opción de consulta {consulta_key}",
                diagnostic_code="hacienda_query_not_visible",
            )
        try:
            await link.click(timeout=10_000)
        except Exception:
            href = str(await link.get_attribute("href") or "")
            if href and not href.lower().startswith("javascript:"):
                await self.page.goto(urljoin(str(self.page.url or ""), href), timeout=30_000)
            else:
                try:
                    await link.click(timeout=10_000, force=True)
                except Exception:
                    handle = await link.element_handle()
                    if handle is None:
                        raise
                    await handle.evaluate("el => el.click()")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

    async def _primer_localizador(self, constructores: tuple[Any, ...]) -> Any | None:
        for crear in constructores:
            try:
                grupo = crear()
                for indice in range(await grupo.count()):
                    candidato = grupo.nth(indice)
                    try:
                        if await candidato.is_visible():
                            return candidato
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    async def _completar_campo(self, candidatos: tuple[Any, ...], valor: str, campo: str) -> None:
        for contexto in self._contextos():
            localizador = await self._primer_localizador(
                tuple(lambda selector=crear: selector(contexto) for crear in candidatos)
            )
            if localizador is None:
                continue
            try:
                await localizador.click(timeout=2_000)
                await localizador.fill(valor)
                return
            except Exception:
                continue
        raise TargetUnavailableError(
            f"no se encontró el campo {campo} en Hacienda",
            diagnostic_code="hacienda_search_form_missing",
        )

    async def _completar_fechas_y_buscar(
        self, consulta_key: str, desde: str, hasta: str
    ) -> None:
        desde_selectores = (
            lambda c: c.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+desde", re.I)),
            lambda c: c.locator("input[name='fechaStr']"),
            lambda c: c.locator("input[id*='fecha'][id*='desde' i]"),
            lambda c: c.locator("input[placeholder*='desde' i]"),
        )
        hasta_selectores = (
            lambda c: c.get_by_role("textbox", name=re.compile(r"Fecha\s+comprobante\s+hasta", re.I)),
            lambda c: c.locator("input[name='fechaHastaStr']"),
            lambda c: c.locator("input[id*='fecha'][id*='hasta' i]"),
            lambda c: c.locator("input[placeholder*='hasta' i]"),
        )
        await self._completar_campo(desde_selectores, desde, "fecha_desde")
        await self._completar_campo(hasta_selectores, hasta, "fecha_hasta")

        buscar_patron = re.compile(r"Buscar|Consultar", re.I)
        encontrado = False
        for contexto in self._contextos():
            candidatos = (
                lambda c=contexto: c.get_by_role("button", name=buscar_patron),
                lambda c=contexto: c.locator("button:has-text('Buscar')"),
                lambda c=contexto: c.locator("a:has-text('Buscar')"),
            )
            boton = await self._primer_localizador(candidatos)
            if boton is None:
                continue
            try:
                await boton.click(timeout=5_000)
                encontrado = True
                break
            except Exception:
                continue
        if not encontrado:
            raise TargetUnavailableError(
                f"no se encontró el botón de búsqueda para {consulta_key}",
                diagnostic_code="hacienda_search_form_missing",
            )
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        await self._seleccionar_maximo_por_pagina()

    async def _seleccionar_maximo_por_pagina(self) -> None:
        for selector in ("#cantPages", "select[id*='cantPages' i]", "select[name='displayLength']"):
            try:
                combo = self.page.locator(selector)
                if await combo.count() == 0:
                    continue
                combo = combo.first
                opciones = combo.locator("option")
                valores: list[tuple[int, str]] = []
                for indice in range(await opciones.count()):
                    opcion = opciones.nth(indice)
                    valor = str(await opcion.get_attribute("value") or "")
                    texto = str(await opcion.inner_text() or "")
                    digitos = re.sub(r"\D", "", valor or texto)
                    if digitos:
                        valores.append((int(digitos), valor or texto))
                if not valores:
                    return
                maximo, valor_maximo = max(valores)
                actual = re.sub(r"\D", "", str(await combo.input_value() or ""))
                if actual != str(maximo):
                    await combo.select_option(valor_maximo)
                    try:
                        await self.page.wait_for_load_state("networkidle", timeout=12_000)
                    except Exception:
                        pass
                return
            except Exception:
                continue

    async def _tablas_actuales(self) -> list[dict[str, Any]]:
        tablas: list[dict[str, Any]] = []
        try:
            tablas.extend(await self.tablas())
        except Exception:
            pass
        for contexto in self._contextos()[1:]:
            try:
                datos = await contexto.locator("body").evaluate(TABLAS_JS)
                if datos:
                    tablas.extend(tabla for tabla in datos if isinstance(tabla, dict))
            except Exception:
                continue
        return tablas

    @staticmethod
    def _filas_de_tablas(
        tablas: list[dict[str, Any]], consulta_key: str, pagina: int
    ) -> list[dict[str, Any]]:
        resultado: list[dict[str, Any]] = []
        for tabla in tablas:
            headers = [str(x or "").strip() for x in tabla.get("headers", [])]
            for numero, celdas in enumerate(tabla.get("rows", []), start=1):
                valores = [str(x or "").strip() for x in celdas]
                if not any(valores):
                    continue
                fila: dict[str, Any] = {"consulta": consulta_key, "pagina": pagina}
                usados: dict[str, int] = {}
                for indice, valor in enumerate(valores):
                    base = headers[indice] if indice < len(headers) and headers[indice] else f"col_{indice + 1}"
                    usados[base] = usados.get(base, 0) + 1
                    clave = base if usados[base] == 1 else f"{base}_{usados[base]}"
                    fila[clave] = valor
                resultado.append(fila)
        return resultado

    async def _siguiente_pagina(self) -> bool:
        for contexto in self._contextos():
            grupos = (
                contexto.get_by_role("link", name=_SIGUIENTE),
                contexto.get_by_role("button", name=_SIGUIENTE),
                contexto.locator("li.next:not(.disabled) a"),
                contexto.locator("a[aria-label*='Siguiente'], button[aria-label*='Siguiente']"),
                contexto.locator("a:has-text('Siguiente'), button:has-text('Siguiente')"),
            )
            for grupo in grupos:
                try:
                    for indice in range(await grupo.count()):
                        candidato = grupo.nth(indice)
                        try:
                            if not await candidato.is_visible():
                                continue
                            clase = str(await candidato.get_attribute("class") or "").lower()
                            deshabilitado = str(await candidato.get_attribute("disabled") or "")
                            aria = str(await candidato.get_attribute("aria-disabled") or "").lower()
                            parent = candidato.locator("xpath=..")
                            parent_class = str(await parent.get_attribute("class") or "").lower()
                            if deshabilitado or aria == "true" or "disabled" in clase or "disabled" in parent_class:
                                continue
                            await candidato.click(timeout=5_000)
                            try:
                                await self.page.wait_for_load_state("networkidle", timeout=10_000)
                            except Exception:
                                pass
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
        return False

    async def _leer_resultados(self, consulta_key: str) -> list[dict[str, Any]]:
        filas: list[dict[str, Any]] = []
        firmas_vistas: set[str] = set()
        for pagina in range(1, 101):
            tablas = await self._tablas_actuales()
            actuales = self._filas_de_tablas(tablas, consulta_key, pagina)
            firma = repr(
                [
                    {clave: valor for clave, valor in fila.items() if clave != "pagina"}
                    for fila in actuales
                ]
            )
            if firma and firma in firmas_vistas:
                break
            if firma:
                firmas_vistas.add(firma)
            filas.extend(actuales)
            if not await self._siguiente_pagina():
                break
        return filas
