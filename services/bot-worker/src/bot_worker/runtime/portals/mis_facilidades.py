"""Portal de Mis Facilidades: consulta de planes y exportación de sus tablas.

Porta las interacciones de lectura de ``mis_facilidades_bot.py`` (V1/V2).
Este módulo sólo selecciona un CUIT, lista planes y abre vistas de consulta
(Pagos, Plan de Pago y obligaciones). No crea, adhiere ni modifica planes ni
inicia la generación de VEP.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

_PLANES_JS = r"""
() => Array.from(document.querySelectorAll('table.searchTable tbody tr'))
  .map(row => {
    const cells = Array.from(row.querySelectorAll(':scope > td'));
    const detalle = row.querySelector("input[id^='ContentPlaceHolder1_rpt_detallePlan_']");
    const texto = el => (el?.innerText || '').replace(/\s+/g, ' ').trim();
    return {
      numero: texto(cells[1]),
      denominacion: texto(cells[3]),
      situacion: texto(cells[6]),
      detalle_id: detalle?.id || ''
    };
  })
  .filter(plan => plan.numero || plan.situacion || plan.detalle_id)
"""

_TABLA_JS = r"""
(table) => {
  const visible = el => {
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden';
  };
  const texto = el => (el.innerText || '').replace(/\s+/g, ' ').trim();
  const headRows = Array.from(table.querySelectorAll('thead tr')).filter(visible);
  let headers = headRows.flatMap(row => Array.from(row.querySelectorAll('th, td'))
    .filter(visible).map(texto));
  const bodyRows = Array.from(table.querySelectorAll('tbody tr, tr')).filter(visible);
  const rows = [];
  bodyRows.forEach((row, index) => {
    const cells = Array.from(row.querySelectorAll(':scope > th, :scope > td'))
      .filter(visible).map(texto);
    if (!headers.length && index === 0 && row.querySelector('th')) {
      headers = cells;
      return;
    }
    if (cells.some(value => value !== '')) rows.push(cells);
  });
  if (!headers.length && rows.length) {
    headers = Array.from({length: Math.max(...rows.map(row => row.length))},
      (_, index) => `col_${index + 1}`);
  }
  return {headers, rows};
}
"""

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_NS_CONTENT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_XML = "http://www.w3.org/XML/1998/namespace"


class MisFacilidadesPortal(PortalArca):
    """Consulta del listado y detalle de planes de Mis Facilidades."""

    nombre = "mis_facilidades"

    def __init__(self, page: Any, *, captcha: Any = None, context: Any = None,
                 service_name: str = "") -> None:
        super().__init__(page, captcha=captcha, context=context, service_name=service_name)
        self._detalle_por_numero: dict[str, str] = {}
        self._plan_actual: str | None = None
        self._secciones_por_plan: dict[str, list[dict[str, Any]]] = {}

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona el combo específico de Mis Facilidades si está presente."""
        digitos = solo_digitos(cuit)
        if len(digitos) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        combo = self.page.locator("#ContentPlaceHolder1_ddlCUIT")
        if await combo.count():
            opciones = combo.locator("option")
            seleccion = None
            for indice in range(await opciones.count()):
                opcion = opciones.nth(indice)
                valor = (await opcion.get_attribute("value")) or ""
                etiqueta = (await opcion.text_content()) or ""
                if digitos in solo_digitos(valor) or digitos in solo_digitos(etiqueta):
                    seleccion = (valor, etiqueta)
                    break
            if seleccion is None:
                raise TargetUnavailableError(
                    "el CUIT representado no está disponible en Mis Facilidades",
                    diagnostic_code="represented_cuit_not_selectable",
                )
            valor, etiqueta = seleccion
            if valor:
                await combo.select_option(value=valor)
            else:
                await combo.select_option(label=etiqueta)
            aceptar = self.page.get_by_role("button", name=re.compile(r"^Aceptar$", re.I))
            if await aceptar.count():
                await aceptar.first.click(timeout=10_000)
                await self._esperar_carga()
            return

        # Hay instalaciones en las que ARCA ya abre el servicio para el único
        # representado. Se conserva la selección genérica, validando antes de
        # aceptar una página que ya expone el CUIT buscado.
        try:
            await super().seleccionar_representado(cuit)
            return
        except TargetUnavailableError:
            texto = ""
            try:
                texto = await self.page.locator("body").inner_text(timeout=3_000)
            except Exception:
                pass
            if digitos in solo_digitos(texto):
                return
            raise

    async def listar_planes(self) -> list[dict[str, str]]:
        """Lee los planes del listado, conservando el identificador de detalle."""
        return await self.paso("listar_planes", self._leer_planes())

    async def exportar_plan_pdf(self, *, numero: str, destino: Path) -> Path:
        """Escribe un PDF de consulta con las tablas visibles del plan."""
        numero = str(numero or "").strip()
        secciones = await self._obtener_secciones(numero)
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        pagina = None
        try:
            if self._context is not None:
                pagina = await self._context.new_page()
                await pagina.set_content(self._html_reporte(numero, secciones), wait_until="load")
                await pagina.emulate_media(media="screen")
                await pagina.pdf(path=str(destino), format="A4", print_background=True)
            else:
                raise RuntimeError("contexto de navegador no disponible para exportar PDF")
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo exportar el PDF del plan",
                diagnostic_code="mis_facilidades_pdf_failed",
            ) from exc
        finally:
            if pagina is not None:
                try:
                    await pagina.close()
                except Exception:
                    pass
        self._validar_archivo(destino, b"%PDF", "mis_facilidades_pdf_empty")
        return destino

    async def exportar_plan_xlsx(self, *, numero: str, destino: Path) -> Path:
        """Exporta Pagos, Cuotas y obligaciones a un XLSX sin dependencias extra."""
        numero = str(numero or "").strip()
        secciones = await self._obtener_secciones(numero)
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._escribir_xlsx(destino, secciones, numero)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo exportar el XLSX del plan",
                diagnostic_code="mis_facilidades_xlsx_failed",
            ) from exc
        self._validar_archivo(destino, b"PK\x03\x04", "mis_facilidades_xlsx_empty")
        return destino

    async def _leer_planes(self) -> list[dict[str, str]]:
        tabla = self.page.locator("table.searchTable")
        try:
            await tabla.first.wait_for(state="visible", timeout=25_000)
        except Exception:
            pass
        try:
            tabla_visible = await tabla.first.is_visible(timeout=500)
        except Exception:
            tabla_visible = False
        if await tabla.count() == 0 or not tabla_visible:
            texto = ""
            try:
                texto = (await self.page.locator("body").inner_text(timeout=3_000)).lower()
            except Exception:
                pass
            if any(frase in texto for frase in ("no se encontraron planes", "no hay planes", "sin planes")):
                self._detalle_por_numero.clear()
                return []
            raise TargetUnavailableError(
                "no se encontró el listado de planes de Mis Facilidades",
                diagnostic_code="mis_facilidades_list_missing",
            )
        filas = await self.page.evaluate(_PLANES_JS)
        planes: list[dict[str, str]] = []
        self._detalle_por_numero.clear()
        for fila in filas or []:
            if not isinstance(fila, dict):
                continue
            numero = " ".join(str(fila.get("numero") or "").split())
            situacion = " ".join(str(fila.get("situacion") or "").split())
            denominacion = " ".join(str(fila.get("denominacion") or "").split())
            detalle_id = str(fila.get("detalle_id") or "").strip()
            if not numero and not situacion:
                continue
            # En V1/V2 esas filas se reconocían en el listado pero se omitían
            # al no tener el control que abre un detalle exportable.
            if not numero or not detalle_id:
                continue
            plan = {"numero": numero, "situacion": situacion}
            if denominacion:
                plan["denominacion"] = denominacion
            planes.append(plan)
            if numero and detalle_id:
                self._detalle_por_numero[numero] = detalle_id
        return planes

    async def _obtener_secciones(self, numero: str) -> list[dict[str, Any]]:
        if not numero:
            raise TargetUnavailableError(
                "número de plan vacío",
                diagnostic_code="mis_facilidades_plan_number_missing",
            )
        if numero in self._secciones_por_plan:
            return self._secciones_por_plan[numero]
        await self._abrir_plan(numero)
        secciones: list[dict[str, Any]] = []
        try:
            pagos = await self._capturar_vista(
                "Ver Pagos", "#tableDetallePagos", "Pagos", obligatorio=True
            )
            if pagos:
                secciones.append(pagos)
            cuotas = await self._capturar_vista(
                "Plan de Pago", "#tableDetallePlanPago", "Cuotas", obligatorio=True
            )
            if cuotas:
                secciones.append(cuotas)
            for nombre, rotulo in (
                ("Obligaciones Impositivas", "Obligaciones Impositivas"),
                ("Obligaciones Previsionales", "Obligaciones Previsionales"),
            ):
                obligaciones = await self._capturar_vista(
                    nombre, "table[id^='tableDetalleDeuda']", rotulo, obligatorio=False,
                    multiples=True,
                )
                secciones.extend(obligaciones if isinstance(obligaciones, list) else ([obligaciones] if obligaciones else []))
            self._secciones_por_plan[numero] = secciones
            return secciones
        finally:
            await self._volver_al_listado(suprimir_error=True)

    async def _capturar_vista(
        self, nombre: str, selector: str, titulo: str, *, obligatorio: bool,
        multiples: bool = False,
    ) -> Any:
        boton = self.page.get_by_role("button", name=re.compile(rf"^{re.escape(nombre)}$", re.I))
        if not await boton.count():
            if obligatorio:
                raise TargetUnavailableError(
                    f"no se encontró la vista {titulo} del plan",
                    diagnostic_code="mis_facilidades_view_missing",
                )
            return [] if multiples else None
        await boton.first.click(timeout=15_000)
        await self._esperar_carga()
        localizador = self.page.locator(selector)
        try:
            await localizador.first.wait_for(state="visible", timeout=15_000)
        except Exception:
            pass
        cantidad = await localizador.count()
        if cantidad == 0 and obligatorio:
            await self._volver_a_detalle()
            raise TargetUnavailableError(
                f"no se encontró la tabla de {titulo} del plan",
                diagnostic_code="mis_facilidades_table_missing",
            )
        datos = []
        for indice in range(cantidad):
            tabla = localizador.nth(indice)
            try:
                contenido = await tabla.evaluate(_TABLA_JS)
            except Exception:
                continue
            if not isinstance(contenido, dict):
                continue
            headers = [str(celda) for celda in contenido.get("headers", [])]
            rows = [[str(celda) for celda in fila] for fila in contenido.get("rows", [])]
            if headers or rows:
                datos.append({"titulo": titulo if indice == 0 else f"{titulo} {indice + 1}", "headers": headers, "rows": rows})
        await self._volver_a_detalle()
        if multiples:
            return datos
        if datos:
            return datos[0]
        if obligatorio:
            raise TargetUnavailableError(
                f"la tabla de {titulo} del plan está vacía",
                diagnostic_code="mis_facilidades_table_empty",
            )
        return None

    async def _abrir_plan(self, numero: str) -> None:
        if self._plan_actual == numero and await self._es_detalle():
            return
        if not await self._es_listado():
            await self._volver_al_listado()
        if not self._detalle_por_numero:
            await self.listar_planes()
        detalle_id = self._detalle_por_numero.get(numero)
        if not detalle_id:
            raise TargetUnavailableError(
                "no se encontró el detalle del plan solicitado",
                diagnostic_code="mis_facilidades_plan_not_found",
            )

        ultimo_error: Exception | None = None
        for intento in range(3):
            try:
                localizador = self.page.locator(f"#{detalle_id}")
                if not await localizador.count():
                    # Una vuelta de postback puede regenerar el listado y sus
                    # IDs. Actualizar el mapa antes de declarar el plan perdido.
                    await self.listar_planes()
                    detalle_id = self._detalle_por_numero.get(numero, "")
                    localizador = self.page.locator(f"#{detalle_id}") if detalle_id else None
                if localizador is not None and await localizador.count():
                    try:
                        await localizador.first.scroll_into_view_if_needed(timeout=5_000)
                    except Exception:
                        pass
                    try:
                        await localizador.first.click(timeout=8_000)
                    except Exception:
                        try:
                            await localizador.first.click(force=True, timeout=8_000)
                        except Exception:
                            await self.page.evaluate(
                                "id => { const el = document.getElementById(id); if (!el) return false; el.click(); return true; }",
                                detalle_id,
                            )
                    await self._esperar_carga()
                    if await self._es_detalle():
                        self._plan_actual = numero
                        return
                ultimo_error = RuntimeError("el control del plan no abrió el detalle")
            except Exception as exc:
                ultimo_error = exc
            if intento < 2:
                if not await self._es_listado():
                    await self._volver_al_listado(suprimir_error=True)
                await self._esperar(700)
        raise TargetUnavailableError(
            "no se pudo abrir el detalle del plan",
            diagnostic_code="mis_facilidades_plan_open_failed",
        ) from ultimo_error

    async def _volver_a_detalle(self) -> None:
        enlace = self.page.get_by_role("link", name=re.compile(r"Volver", re.I))
        if await enlace.count():
            await enlace.first.click(timeout=12_000)
            await self._esperar_carga()
        if not await self._es_detalle():
            self._plan_actual = None
            raise TargetUnavailableError(
                "no se pudo volver al detalle del plan",
                diagnostic_code="mis_facilidades_return_detail_failed",
            )

    async def _volver_al_listado(self, *, suprimir_error: bool = False) -> None:
        if await self._es_listado():
            self._plan_actual = None
            return
        try:
            boton = self.page.locator("#ContentPlaceHolder1_btnVolver")
            if await boton.count() and await boton.first.is_visible(timeout=2_000):
                await boton.first.click(timeout=12_000)
            else:
                enlace = self.page.get_by_role("link", name=re.compile(r"Volver", re.I))
                if not await enlace.count():
                    raise RuntimeError("link de vuelta ausente")
                await enlace.first.click(timeout=12_000)
            await self._esperar_carga()
            if not await self._es_listado():
                raise RuntimeError("no se volvió al listado")
            self._plan_actual = None
        except Exception as exc:
            if not suprimir_error:
                raise TargetUnavailableError(
                    "no se pudo volver al listado de planes",
                    diagnostic_code="mis_facilidades_return_list_failed",
                ) from exc

    async def _es_listado(self) -> bool:
        try:
            tabla = self.page.locator("table.searchTable")
            if await tabla.count() and await tabla.first.is_visible(timeout=500):
                return True
            detalle = self.page.locator("[id^='ContentPlaceHolder1_rpt_detallePlan_']")
            return bool(await detalle.count() and await detalle.first.is_visible(timeout=500))
        except Exception:
            return False

    async def _es_detalle(self) -> bool:
        for selector in (
            "#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_nroPlan",
            "#ContentPlaceHolder1_btnVolver",
        ):
            try:
                localizador = self.page.locator(selector)
                if await localizador.count() and await localizador.first.is_visible(timeout=500):
                    return True
            except Exception:
                pass
        for nombre in ("Ver Pagos", "Plan de Pago"):
            try:
                boton = self.page.get_by_role("button", name=nombre)
                if await boton.count() and await boton.first.is_visible(timeout=500):
                    return True
            except Exception:
                pass
        return False

    async def _esperar_carga(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        await self._esperar(500)

    @staticmethod
    def _html_reporte(numero: str, secciones: list[dict[str, Any]]) -> str:
        from html import escape

        partes = [
            "<!doctype html><html><head><meta charset='utf-8'>",
            "<style>body{font:12px Arial,sans-serif;color:#222}h1{font-size:20px}"
            "h2{font-size:15px;margin-top:24px}table{border-collapse:collapse;width:100%;"
            "margin-bottom:20px}th,td{border:1px solid #999;padding:5px;text-align:left}"
            "th{background:#eee}</style></head><body>",
            f"<h1>Mis Facilidades · Plan {escape(numero)}</h1>",
        ]
        if not secciones:
            partes.append("<p>No se encontraron tablas disponibles para este plan.</p>")
        for seccion in secciones:
            partes.append(f"<h2>{escape(str(seccion.get('titulo') or 'Detalle'))}</h2><table>")
            headers = seccion.get("headers") or []
            rows = seccion.get("rows") or []
            if headers:
                partes.append("<thead><tr>" + "".join(f"<th>{escape(str(valor))}</th>" for valor in headers) + "</tr></thead>")
            partes.append("<tbody>")
            for fila in rows:
                partes.append("<tr>" + "".join(f"<td>{escape(str(valor))}</td>" for valor in fila) + "</tr>")
            partes.append("</tbody></table>")
        partes.append("</body></html>")
        return "".join(partes)

    @classmethod
    def _escribir_xlsx(cls, destino: Path, secciones: list[dict[str, Any]], numero: str) -> None:
        """Escribe un XLSX OpenXML mínimo con tablas y hojas de consulta."""
        hojas: list[tuple[str, list[str], list[list[str]]]] = []
        usados: set[str] = set()
        for indice, seccion in enumerate(secciones, start=1):
            base = re.sub(r"[\\/*?:\[\]]", "_", str(seccion.get("titulo") or f"Tabla {indice}"))[:31] or f"Tabla {indice}"
            nombre = base
            sufijo = 2
            while nombre.lower() in usados:
                cola = f" {sufijo}"
                nombre = f"{base[:31 - len(cola)]}{cola}"
                sufijo += 1
            usados.add(nombre.lower())
            headers = [str(valor) for valor in (seccion.get("headers") or [])]
            filas = [[str(valor) for valor in fila] for fila in (seccion.get("rows") or [])]
            if not headers and filas:
                ancho = max(len(fila) for fila in filas)
                headers = [f"col_{n}" for n in range(1, ancho + 1)]
            hojas.append((nombre, headers, filas))
        if not hojas:
            hojas.append(("Plan", ["Número de plan"], [[str(numero)]]))

        ET.register_namespace("", _NS_MAIN)
        ET.register_namespace("r", _NS_REL_DOC)
        workbook = ET.Element(f"{{{_NS_MAIN}}}workbook")
        sheets = ET.SubElement(workbook, f"{{{_NS_MAIN}}}sheets")
        for idx, (nombre, _, _) in enumerate(hojas, start=1):
            ET.SubElement(sheets, f"{{{_NS_MAIN}}}sheet", {
                "name": nombre, "sheetId": str(idx), f"{{{_NS_REL_DOC}}}id": f"rId{idx}"
            })
        workbook_xml = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)

        rels = ET.Element(f"{{{_NS_REL_PKG}}}Relationships")
        for idx in range(1, len(hojas) + 1):
            ET.SubElement(rels, f"{{{_NS_REL_PKG}}}Relationship", {
                "Id": f"rId{idx}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{idx}.xml",
            })
        rels_xml = ET.tostring(rels, encoding="utf-8", xml_declaration=True)

        tipos = ET.Element(f"{{{_NS_CONTENT}}}Types")
        ET.SubElement(tipos, f"{{{_NS_CONTENT}}}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
        ET.SubElement(tipos, f"{{{_NS_CONTENT}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
        ET.SubElement(tipos, f"{{{_NS_CONTENT}}}Override", {
            "PartName": "/xl/workbook.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        })
        for idx in range(1, len(hojas) + 1):
            ET.SubElement(tipos, f"{{{_NS_CONTENT}}}Override", {
                "PartName": f"/xl/worksheets/sheet{idx}.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            })
        content_types_xml = ET.tostring(tipos, encoding="utf-8", xml_declaration=True)

        root_rels = ET.Element(f"{{{_NS_REL_PKG}}}Relationships")
        ET.SubElement(root_rels, f"{{{_NS_REL_PKG}}}Relationship", {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml",
        })
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
                    row_node = ET.SubElement(sheet_data, f"{{{_NS_MAIN}}}row", {"r": str(numero_fila)})
                    for numero_columna, valor in enumerate(valores, start=1):
                        celda = ET.SubElement(row_node, f"{{{_NS_MAIN}}}c", {
                            "r": f"{cls._letra_columna(numero_columna)}{numero_fila}",
                            "t": "inlineStr",
                        })
                        inline = ET.SubElement(celda, f"{{{_NS_MAIN}}}is")
                        texto = ET.SubElement(inline, f"{{{_NS_MAIN}}}t", {f"{{{_NS_XML}}}space": "preserve"})
                        texto.text = str(valor)
                archivo.writestr(
                    f"xl/worksheets/sheet{idx}.xml",
                    ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
                )

    @staticmethod
    def _letra_columna(numero: int) -> str:
        letras = ""
        while numero:
            numero, resto = divmod(numero - 1, 26)
            letras = chr(65 + resto) + letras
        return letras

    @staticmethod
    def _validar_archivo(destino: Path, prefijo: bytes, diagnostico: str) -> None:
        try:
            if not destino.is_file() or destino.stat().st_size == 0:
                raise ValueError("archivo vacío")
            with destino.open("rb") as archivo:
                inicio = archivo.read(len(prefijo))
            if not inicio.startswith(prefijo):
                raise ValueError("firma de archivo inválida")
            if destino.suffix.lower() == ".xlsx":
                with zipfile.ZipFile(destino) as libro:
                    if libro.testzip() is not None or "xl/workbook.xml" not in libro.namelist():
                        raise ValueError("libro OpenXML inválido")
        except Exception as exc:
            raise TargetUnavailableError(
                "la exportación de Mis Facilidades quedó vacía o inválida",
                diagnostic_code=diagnostico,
            ) from exc
