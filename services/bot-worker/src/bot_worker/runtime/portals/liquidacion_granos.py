"""Portal de Liquidación Primaria de Granos de ARCA.

Porta la consulta de V1/V2 a la interfaz ``PortalArca``. Las planillas se
construyen a partir de las tablas consultadas y los comprobantes se obtienen
por el endpoint de reportes del propio portal, con click como respaldo.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlparse

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

_SELECTOR_DESCARGA = (
    "a[onclick*='pd.jsp'], a[href*='pd.jsp'], a.imprimir, "
    "a[onclick*='.pdf'], a[href*='.pdf'], "
    "a[onclick*='descarg'], a[href*='descarg'], a[title*='Descargar']"
)
_SELECTOR_CERTIFICADOS = "a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]"
_MENU_LPG = re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I)
_MENU_LSG = re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I)
_MENU_CERTIFICADOS = re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I)
_MENU_CONSULTA_CERTIFICADOS = re.compile(r"Consulta\s+de\s+Certificados\s+de", re.I)
_SECCIONES = {
    "lpg_emitidas": (
        _MENU_LPG,
        re.compile(r"Consulta\s+de\s+Liquidaciones(?!\s+Recibidas)", re.I),
    ),
    "lpg_recibidas": (_MENU_LPG, re.compile(r"Consulta\s+Liquidaciones\s+Recibidas", re.I)),
    "lsg_emitidas": (_MENU_LSG, re.compile(r"Consulta\s+de\s+Liquidaciones\s+Emitidas", re.I)),
    "lsg_recibidas": (_MENU_LSG, re.compile(r"Consulta\s+de\s+Liquidaciones\s+Recibidas", re.I)),
}
_TIPOS = {
    "lpg_emitidas": ("LPG", "emitidas"),
    "lpg_recibidas": ("LPG", "recibidas"),
    "lsg_emitidas": ("LSG", "emitidas"),
    "lsg_recibidas": ("LSG", "recibidas"),
    "certificados_deposito": ("CD", "deposito"),
}
_MAX_PDF_BYTES = 10 * 1024 * 1024


def _limpiar_xml(valor: Any) -> str:
    texto = str(valor or "")
    return "".join(
        caracter
        for caracter in texto
        if caracter in "\t\n\r" or ord(caracter) >= 32
    )


def _celda_excel(columna: int, fila: int, valor: Any) -> str:
    numero = columna
    letras = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    contenido = html.escape(_limpiar_xml(valor), quote=False)
    return (
        f'<c r="{letras}{fila}" t="inlineStr"><is>'
        f'<t xml:space="preserve">{contenido}</t></is></c>'
    )


def _guardar_xlsx(destino: Path, encabezados: list[str], filas: list[list[Any]]) -> Path:
    """Escribe un XLSX mínimo usando solo la biblioteca estándar."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not encabezados:
        encabezados = ["Resultado"]
    if not filas and encabezados == ["Resultado"]:
        filas = [["Sin registros"]]
    max_columnas = max([len(encabezados), *(len(fila) for fila in filas)], default=1)
    if len(encabezados) < max_columnas:
        encabezados.extend(f"Columna {i}" for i in range(len(encabezados) + 1, max_columnas + 1))
    filas_xml = []
    for num_fila, valores in enumerate([encabezados, *filas], start=1):
        valores = list(valores) + [""] * (max_columnas - len(valores))
        celdas = "".join(_celda_excel(idx, num_fila, valor) for idx, valor in enumerate(valores, 1))
        filas_xml.append(f'<row r="{num_fila}">{celdas}</row>')
    hoja = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(filas_xml)}</sheetData></worksheet>'
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    raiz_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Liquidaciones" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(destino, "w", compression=zipfile.ZIP_DEFLATED) as archivo:
        archivo.writestr("[Content_Types].xml", tipos)
        archivo.writestr("_rels/.rels", raiz_rels)
        archivo.writestr("xl/workbook.xml", workbook)
        archivo.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archivo.writestr("xl/worksheets/sheet1.xml", hoja)
    return destino


def _limpiar_nombre(valor: str) -> str:
    limpio = re.sub(r"[\\/:*?\"<>|]+", "_", str(valor or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio or "archivo"


def _ruta_unica(destino: Path) -> Path:
    if not destino.exists():
        return destino
    contador = 2
    while True:
        candidata = destino.with_name(f"{destino.stem}_{contador}{destino.suffix}")
        if not candidata.exists():
            return candidata
        contador += 1


class LiquidacionGranosPortal(PortalArca):
    """Navegación de planillas y comprobantes de Liquidación de Granos."""

    nombre = "liquidacion_granos"

    def __init__(self, page: Any, *, captcha: Any = None, context: Any = None, service_name: str = "") -> None:
        super().__init__(page, captcha=captcha, context=context, service_name=service_name)
        self._representado_cuit = ""

    async def seleccionar_representado(self, cuit: str) -> None:
        self._representado_cuit = solo_digitos(cuit)
        try:
            # V1/V2 llega directamente al servicio para el representado activo.
            menu = self.page.get_by_role("button", name=_MENU_LPG)
            if await menu.count() > 0:
                return
        except Exception:
            pass
        await super().seleccionar_representado(cuit)

    async def descargar_planilla(
        self, *, seccion: str, destino: Path, desde: str, hasta: str
    ) -> Path:
        """Consulta una sección y guarda su tabla como XLSX válido."""
        if seccion not in _TIPOS:
            raise TargetUnavailableError(
                "sección desconocida de Liquidación de Granos",
                diagnostic_code="granos_section_unknown",
            )
        tablas: list[dict[str, Any]] = []
        if seccion == "certificados_deposito":
            await self._abrir_certificados()
            tipos = await self._tipos_certificado()
            encabezados_certificados: list[str] = []
            filas_certificados: list[list[Any]] = []
            for valor, _ in tipos:
                await self._consultar_certificados(desde, hasta, valor)
                encabezados, filas = self._filas_de_tablas(await self.tablas())
                if not encabezados_certificados:
                    encabezados_certificados = encabezados
                filas_certificados.extend([[valor, *fila] for fila in filas])
            encabezados, filas = (
                ["Tipo de consulta", *encabezados_certificados],
                filas_certificados,
            )
        else:
            await self._abrir_seccion(seccion)
            await self._consultar_fechas(desde, hasta)
            tablas = await self.tablas()
            encabezados, filas = self._filas_de_tablas(tablas)
        return await self.paso(
            "descargar_planilla",
            asyncio.to_thread(_guardar_xlsx, Path(destino), encabezados, filas),
        )

    async def descargar_comprobantes(
        self, *, seccion: str, destino_dir: Path, desde: str, hasta: str
    ) -> list[Path]:
        """Descarga los PDF de una consulta, sin exponer cookies al plugin."""
        if seccion not in _TIPOS:
            raise TargetUnavailableError(
                "sección desconocida de Liquidación de Granos",
                diagnostic_code="granos_section_unknown",
            )
        destino_dir = Path(destino_dir)
        destino_dir.mkdir(parents=True, exist_ok=True)
        tipo, consulta = _TIPOS[seccion]
        if seccion == "certificados_deposito":
            await self._abrir_certificados()
            descargados: list[Path] = []
            for valor, _ in await self._tipos_certificado():
                await self._consultar_certificados(desde, hasta, valor)
                descargados.extend(await self._descargar_certificados(destino_dir, valor))
            return descargados
        await self._abrir_seccion(seccion)
        await self._consultar_fechas(desde, hasta)
        return await self._descargar_comprobantes_de_pagina(
            destino_dir, tipo, consulta
        )

    @staticmethod
    def _filas_de_tablas(tablas: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
        candidatas = [tabla for tabla in tablas if isinstance(tabla, dict)]
        if not candidatas:
            return ["Resultado"], [["Sin registros"]]
        candidata = max(candidatas, key=lambda tabla: len(tabla.get("rows") or []))
        encabezados = [str(valor or "") for valor in candidata.get("headers") or []]
        filas = [list(fila) for fila in candidata.get("rows") or [] if isinstance(fila, (list, tuple))]
        ancho = max([len(encabezados), *(len(fila) for fila in filas)], default=0)
        if ancho == 0:
            return ["Resultado"], [["Sin registros"]]
        if not encabezados:
            encabezados = [f"Columna {indice}" for indice in range(1, ancho + 1)]
        if len(encabezados) < ancho:
            encabezados.extend(f"Columna {i}" for i in range(len(encabezados) + 1, ancho + 1))
        if not filas:
            filas = [["Sin registros"] + [""] * (ancho - 1)]
        return encabezados, filas

    async def _click_patron(self, patron: re.Pattern[str], nombre_paso: str) -> None:
        candidatos = (
            self.page.get_by_role("button", name=patron),
            self.page.get_by_role("link", name=patron),
            self.page.locator("button, a, [role='button'], .usarManito").filter(has_text=patron),
        )
        elemento = await self._primero_visible(candidatos, total_ms=8_000)
        if elemento is None:
            raise TargetUnavailableError(
                f"no se encontró la opción de menú {nombre_paso}",
                diagnostic_code=f"granos_menu_{nombre_paso}",
            )
        try:
            await elemento.click(timeout=10_000)
        except Exception as exc:
            raise TargetUnavailableError(
                f"no se pudo abrir la opción de menú {nombre_paso}",
                diagnostic_code=f"granos_menu_{nombre_paso}",
            ) from exc
        await self._esperar_red()

    async def _volver_menu(self) -> None:
        try:
            menu = self.page.get_by_role("button", name=re.compile(r"Men[úu]\s+principal", re.I))
            if await menu.count() > 0 and await menu.first.is_visible(timeout=500):
                await menu.first.click(timeout=8_000)
                await self._esperar_red()
        except Exception:
            # Al entrar por primera vez ya puede estar visible el menú principal.
            return

    async def _abrir_seccion(self, seccion: str) -> None:
        modulo, consulta = _SECCIONES[seccion]
        await self._volver_menu()
        await self._click_patron(modulo, f"modulo_{seccion}")
        await self._click_patron(consulta, f"consulta_{seccion}")

    async def _abrir_certificados(self) -> None:
        await self._volver_menu()
        await self._click_patron(_MENU_CERTIFICADOS, "certificacion_electronica")
        await self._click_patron(_MENU_CONSULTA_CERTIFICADOS, "consulta_certificados")

    async def _esperar_red(self) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=12_000)

    async def _consultar_fechas(self, desde: str, hasta: str) -> None:
        try:
            await self.page.locator("input[name='fechaStr']").fill(desde)
            await self.page.locator("input[name='fechaHastaStr']").fill(hasta)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se encontraron los campos de fecha de Liquidación de Granos",
                diagnostic_code="granos_date_fields_missing",
            ) from exc
        await self._click_patron(
            re.compile(r"Consultar\s+Por\s+Criterio", re.I), "consultar"
        )

    async def _tipos_certificado(self) -> list[tuple[str, str]]:
        selector = self.page.locator("#tipoConsulta")
        try:
            opciones = await selector.first.evaluate(
                "el => Array.from(el.options).map(o => ({value:(o.value||'').trim(), text:(o.textContent||'').trim()}))"
            )
        except Exception as exc:
            raise TargetUnavailableError(
                "no se encontró el tipo de consulta de certificados",
                diagnostic_code="granos_certificate_type_missing",
            ) from exc
        resultado: list[tuple[str, str]] = []
        vistos: set[str] = set()
        for opcion in opciones or []:
            valor = str(opcion.get("value") or "").strip()
            texto = str(opcion.get("text") or "").strip()
            if not valor or valor == "-1" or valor in vistos:
                continue
            vistos.add(valor)
            resultado.append((valor, texto or valor))
        if not resultado:
            raise TargetUnavailableError(
                "no hay tipos de consulta de certificados disponibles",
                diagnostic_code="granos_certificate_type_empty",
            )
        return resultado

    async def _consultar_certificados(self, desde: str, hasta: str, tipo: str) -> None:
        try:
            await self.page.locator("#tipoConsulta").first.select_option(value=tipo)
            await self.page.locator("#fechaDesde").first.fill(desde)
            await self.page.locator("#fechaHasta").first.fill(hasta)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudieron completar las fechas de certificados",
                diagnostic_code="granos_certificate_date_fields_missing",
            ) from exc
        await self._click_patron(re.compile(r"Consultar", re.I), "consultar_certificados")

    async def _descargar_comprobantes_de_pagina(
        self, destino_dir: Path, tipo: str, consulta: str
    ) -> list[Path]:
        filas = self.page.locator("tr").filter(has=self.page.locator(_SELECTOR_DESCARGA))
        try:
            cantidad = await filas.count()
        except Exception:
            return []
        resultado: list[Path] = []
        procesadas: set[str] = set()
        for indice in range(cantidad):
            fila = filas.nth(indice)
            try:
                if await fila.locator("table").count() > 0:
                    continue
                enlace = fila.locator(_SELECTOR_DESCARGA).first
                onclick = str(await enlace.get_attribute("onclick") or "")
                href = str(await enlace.get_attribute("href") or "")
                raw = onclick or href
                llave = self._llave_descarga(raw)
                if llave and llave in procesadas:
                    continue
                if llave:
                    procesadas.add(llave)
                texto = str(await fila.inner_text() or "")
            except Exception:
                continue
            digitos = re.findall(r"\b\d{11,14}\b", texto)
            cuit = next((token for token in digitos if len(token) == 11), "SIN_CUIT_VENDEDOR")
            coe = next((token for token in digitos if len(token) >= 12), "SIN_COE")
            etiqueta = "Emitida" if "emit" in consulta else "Recibida"
            nombre = _limpiar_nombre(
                f"{tipo} - {etiqueta} - {self._representado_cuit or 'representado'} - {cuit} - {coe}.pdf"
            )
            destino = _ruta_unica(destino_dir / nombre)
            guardado = await self._pdf_desde_enlace(raw, destino)
            if guardado is None:
                guardado = await self._pdf_por_click(enlace, destino)
            if guardado is not None:
                resultado.append(guardado)
        return resultado

    @staticmethod
    def _llave_descarga(raw: str) -> str:
        for patron, etiqueta in (
            (r"liqCodigo=(\d+)", "liqCodigo"),
            (r"coe=(\d+)", "coe"),
            (r"certificado(?:Id)?=(\d+)", "certificadoId"),
            (r"codigo=(\d+)", "codigo"),
            (r"id=(\d+)", "id"),
            (r"\b(\d{6,})\b", "numero"),
        ):
            match = re.search(patron, raw or "", flags=re.I)
            if match:
                return f"{etiqueta}={match.group(1)}"
        return ""

    async def _descargar_certificados(self, destino_dir: Path, tipo: str) -> list[Path]:
        enlaces = self.page.locator(_SELECTOR_CERTIFICADOS)
        try:
            cantidad = await enlaces.count()
        except Exception:
            return []
        resultado: list[Path] = []
        vistos: set[tuple[str, str, str]] = set()
        for indice in range(cantidad):
            enlace = enlaces.nth(indice)
            try:
                clase = str(await enlace.get_attribute("data-tipocertificado") or "").strip().upper()
                codigo = solo_digitos(await enlace.get_attribute("data-liqcodigo"))
                coe = solo_digitos(await enlace.get_attribute("data-coe"))
            except Exception:
                continue
            llave = (clase, codigo, coe)
            if not all(llave) or llave in vistos:
                continue
            vistos.add(llave)
            nombre = _limpiar_nombre(
                f"CD - {tipo} - deposito - {self._representado_cuit or 'representado'} - SIN_CUIT_VENDEDOR - {coe}.pdf"
            )
            destino = _ruta_unica(destino_dir / nombre)
            url = urljoin(
                str(self.page.url or ""),
                "generarReporteCEG.do?" + urlencode(
                    {"tipo": clase, "ceeCodigo": codigo, "coe": coe}
                ),
            )
            guardado = await self._descargar_pdf_url(url, destino, str(self.page.url or ""))
            if guardado is None:
                guardado = await self._pdf_por_click(enlace, destino)
            if guardado is not None:
                resultado.append(guardado)
        return resultado

    async def _pdf_desde_enlace(self, raw: str, destino: Path) -> Path | None:
        valor = html.unescape(raw or "")
        pdf_match = re.search(r"pd\.jsp\?[^'\";\s]+", valor, flags=re.I)
        reporte_match = re.search(
            r"generarReporte(?:DebitoCredito)?\.do\?[^'\";\s]+", valor, flags=re.I
        )
        pd_path = pdf_match.group(0).strip() if pdf_match else ""
        reporte = reporte_match.group(0).strip() if reporte_match else ""
        if pd_path:
            query = urlparse("https://dummy/" + pd_path.lstrip("/")).query
            acciones = parse_qs(query).get("action", [])
            if acciones:
                reporte = unquote(acciones[0]).strip()
        if not reporte:
            return None
        pagina_url = str(self.page.url or "")
        pd_url = urljoin(pagina_url, pd_path) if pd_path else ""
        reporte_url = urljoin(pagina_url, reporte)
        http_saved_id = ""
        if pd_url:
            if not self._misma_origen(pagina_url, pd_url):
                return None
            try:
                previa = await self.page.request.get(
                    pd_url, headers={"Referer": pagina_url}, timeout=30_000
                )
                if previa.status >= 400:
                    return None
                cuerpo = (await previa.body()).decode("latin-1", errors="ignore")
                encontrado = re.search(
                    r"name\s*=\s*['\"]HTTP_SAVED_ID['\"][^>]*value\s*=\s*['\"]([^'\"]+)['\"]",
                    cuerpo,
                    flags=re.I,
                )
                if encontrado:
                    http_saved_id = encontrado.group(1).strip()
            except Exception:
                return None
        if not self._misma_origen(pagina_url, reporte_url):
            return None
        if not http_saved_id:
            try:
                for selector in (
                    "form[name='frmGo'] input[name='HTTP_SAVED_ID']",
                    "form[name='form'] input[name='HTTP_SAVED_ID']",
                    "input[name='HTTP_SAVED_ID']",
                ):
                    campo = self.page.locator(selector)
                    if await campo.count() > 0:
                        http_saved_id = str(await campo.first.get_attribute("value") or "").strip()
                        if http_saved_id:
                            break
            except Exception:
                pass
        return await self._descargar_pdf_url(reporte_url, destino, pd_url or pagina_url, http_saved_id)

    async def _descargar_pdf_url(
        self, url: str, destino: Path, referer: str, http_saved_id: str = ""
    ) -> Path | None:
        pagina_url = str(self.page.url or "")
        if not self._misma_origen(pagina_url, url):
            return None
        parsed = urlparse(pagina_url)
        headers = {"Referer": referer or pagina_url}
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        try:
            if http_saved_id:
                respuesta = await self.page.request.post(
                    url,
                    headers=headers,
                    form={"HTTP_SAVED_ID": http_saved_id},
                    timeout=30_000,
                )
            else:
                respuesta = await self.page.request.post(
                    url, headers=headers, timeout=30_000
                )
            if respuesta.status >= 400:
                return None
            cuerpo = await respuesta.body()
            tipo = str((respuesta.headers or {}).get("content-type", "")).lower()
            if len(cuerpo) > _MAX_PDF_BYTES or not ("pdf" in tipo or cuerpo.startswith(b"%PDF")):
                return None
            destino = _ruta_unica(destino)
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(cuerpo)
            return destino
        except Exception:
            return None

    async def _pdf_por_click(self, enlace: Any, destino: Path) -> Path | None:
        try:
            async with self.page.expect_download(timeout=30_000) as info:
                await enlace.click(timeout=8_000)
            descarga = await info.value
            destino = _ruta_unica(destino)
            await descarga.save_as(str(destino))
            if not destino.is_file() or destino.stat().st_size == 0:
                return None
            if destino.stat().st_size > _MAX_PDF_BYTES:
                destino.unlink(missing_ok=True)
                return None
            with destino.open("rb") as archivo:
                if not archivo.read(5).startswith(b"%PDF"):
                    destino.unlink(missing_ok=True)
                    return None
            return destino
        except Exception:
            return None

    @staticmethod
    def _misma_origen(origen: str, destino: str) -> bool:
        base, solicitada = urlparse(origen), urlparse(destino)
        return bool(
            base.scheme == "https"
            and solicitada.scheme == "https"
            and base.hostname
            and base.hostname.lower() == (solicitada.hostname or "").lower()
            and base.port == solicitada.port
        )
