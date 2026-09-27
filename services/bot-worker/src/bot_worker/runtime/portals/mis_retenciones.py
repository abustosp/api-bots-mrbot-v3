"""Portal Mis Retenciones de ARCA, port de V1/V2."""
from __future__ import annotations

import asyncio
import csv
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

INICIO_URL = "https://mirequa-web.arca.gob.ar/"
_IMPUESTOS = {
    "216": "216 - SIRE - IVA",
    "217": "217 - SICORE-IMPTO.A LAS GANANCIAS",
    "219": "219 - SICORE-IMPTO.S/ BS PERSONALES",
    "353": "353 - RETENCIONES CONTRIB.SEG.SOCIAL",
    "767": "767 - SICORE - RETENCIONES Y PERCEPC",
    "787": "787 - RET ART 79 LEY GCIAS INC A,ByC",
}


def _formatear_cuit(cuit: str) -> str:
    digits = solo_digitos(cuit)
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"
    return ""


def _variantes_impuesto(label: str, code: str) -> list[str]:
    normalizado = re.sub(r"\s+", " ", (label or "").strip())
    solo_texto = re.sub(rf"^{re.escape(code)}\s*-\s*", "", normalizado, flags=re.I)
    candidatas = [normalizado, solo_texto, f"- {solo_texto}"]
    candidatas.extend((re.sub(r"[./]", " ", item) for item in candidatas if item))
    unicas: list[str] = []
    for candidata in candidatas:
        candidata = re.sub(r"\s+", " ", candidata).strip()
        if candidata and candidata not in unicas:
            unicas.append(candidata)
    return unicas


def _patrones_impuesto(label: str, code: str) -> list[re.Pattern[str]]:
    texto = re.sub(rf"^{re.escape(code)}\s*-\s*", "", (label or "").strip(), flags=re.I)
    tokens = [re.escape(token) for token in re.split(r"[\s\-./,]+", texto) if token]
    if not tokens:
        return []
    flexible = r"[\s\-./,]+".join(tokens)
    patrones = [re.compile(flexible, re.I)]
    if code:
        patrones.insert(0, re.compile(rf"\b{re.escape(code)}\b[\s\-./,]*{flexible}", re.I))
    return patrones


class MisRetencionesPortal(PortalArca):
    """Consulta retenciones/percepciones y descarga el CSV exportado."""

    nombre = "mis_retenciones"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._representado_cuit = ""

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona la tarjeta de relación propia de Mis Retenciones."""
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        await self.paso("seleccionar_representado", self._seleccionar_representado(digits))
        self._representado_cuit = digits

    async def _seleccionar_representado(self, digits: str) -> None:
        if await self._representado_activo(digits):
            return
        await self._abrir_selector_representado()
        formateado = _formatear_cuit(digits)
        patron = re.compile(rf"{digits[:2]}\D*{digits[2:10]}\D*{digits[10]}")
        candidatos = (
            self.page.locator(".e-relation__card", has=self.page.get_by_text(formateado)),
            self.page.locator(".e-relation__card", has=self.page.get_by_text(patron)),
            self.page.locator("div[id^='selectorRelaciones_relation']", has_text=formateado),
            self.page.get_by_text(formateado),
            self.page.get_by_text(patron),
        )
        for candidato in candidatos:
            try:
                if await candidato.count():
                    await candidato.first.click(timeout=4_000)
                    await self._esperar(700)
                    if await self._representado_activo(digits):
                        return
            except Exception:
                continue
        raise TargetUnavailableError(
            "no se pudo seleccionar el CUIT representado en Mis Retenciones",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def _representado_activo(self, digits: str) -> bool:
        nav = self.page.locator("#navBarMisRetenciones")
        for selector in (
            "#navBarMisRetenciones-relationCuil",
            "#navBarMisRetenciones-relationLabel",
            "#navBarMisRetenciones",
        ):
            try:
                item = self.page.locator(selector)
                if await item.count() == 0:
                    continue
                texto = str(await item.first.text_content() or "")
                if digits in solo_digitos(texto):
                    return True
            except Exception:
                continue
        try:
            if await nav.count() and digits in solo_digitos(await nav.first.text_content() or ""):
                return True
        except Exception:
            pass
        return False

    async def _abrir_selector_representado(self) -> None:
        tarjetas = self.page.locator(".e-relation__card")
        try:
            if await tarjetas.count():
                return
        except Exception:
            pass
        try:
            await self.page.locator("#navBarMisRetenciones").wait_for(state="visible", timeout=8_000)
        except Exception:
            pass
        for selector in (
            "#navBarMisRetenciones-relationLabel",
            "#navBarMisRetenciones-relationCuil",
        ):
            try:
                localizador = self.page.locator(selector)
                if await localizador.count():
                    await localizador.first.click(timeout=3_000)
                    await tarjetas.first.wait_for(state="visible", timeout=4_000)
                    return
            except Exception:
                continue
        for selector in (
            "#e-navbar-dropdown-toggle",
            "#navBarMisRetenciones_avatar",
            "#navBarMisRetenciones [data-bs-toggle='dropdown']",
        ):
            try:
                localizador = self.page.locator(selector)
                if await localizador.count():
                    await localizador.first.click(timeout=3_000)
                    break
            except Exception:
                continue
        for candidato in (
            self.page.locator("#navBarMisRetenciones-dropdown-changeRelation"),
            self.page.get_by_role("link", name=re.compile(r"Seleccionar representado|Cambiar representado", re.I)),
            self.page.get_by_text(re.compile(r"Seleccionar representado|Cambiar representado", re.I)),
        ):
            try:
                if await candidato.count():
                    await candidato.first.click(timeout=3_000)
                    await tarjetas.first.wait_for(state="visible", timeout=5_000)
                    return
            except Exception:
                continue

    async def _asegurar_formulario(self) -> None:
        formulario = self.page.locator("#selectImpuestos")
        try:
            await formulario.wait_for(state="visible", timeout=3_000)
        except Exception:
            await self.abrir_url(INICIO_URL, timeout_ms=45_000)
            await self._esperar(1_000)
            nueva = self.page.locator("#tabNuevaConsulta-tab")
            try:
                if await nueva.count() and await nueva.get_attribute("aria-selected") != "true":
                    await nueva.click(timeout=5_000)
            except Exception:
                pass
            try:
                await formulario.wait_for(state="visible", timeout=20_000)
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se encontró el formulario de consulta de Mis Retenciones",
                    diagnostic_code="retenciones_form_missing",
                ) from exc
        if self._representado_cuit and not await self._representado_activo(self._representado_cuit):
            await self.seleccionar_representado(self._representado_cuit)

    async def descargar_csv(
        self,
        *,
        impuesto: str,
        tipo: str,
        destino: Path,
        desde: str,
        hasta: str,
        para_aplicativo: bool = False,
    ) -> Path:
        """Prepara filtros, consulta y materializa el CSV (port de V2)."""
        destino = Path(destino)
        await self.paso("preparar_consulta", self._preparar_consulta(impuesto, tipo, desde, hasta, para_aplicativo))
        archivo = await self.paso(
            "exportar_csv",
            self._consultar_y_descargar(destino, export_option="siap" if para_aplicativo else None),
        )
        self._normalizar_exportacion_csv(Path(archivo))
        return Path(archivo)

    @staticmethod
    def _normalizar_exportacion_csv(destino: Path) -> None:
        """Convierte el SpreadsheetML que ARCA puede entregar como CSV/XLS."""
        try:
            contenido = destino.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise TargetUnavailableError(
                "no se pudo validar el archivo exportado de Mis Retenciones",
                diagnostic_code="retenciones_export_format_invalid",
            ) from exc
        if not contenido.lstrip().startswith("<"):
            return
        try:
            workbook = ET.fromstring(contenido)
        except ET.ParseError as exc:
            raise TargetUnavailableError(
                "el archivo exportado no es CSV ni SpreadsheetML válido",
                diagnostic_code="retenciones_export_format_invalid",
            ) from exc

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        if local_name(workbook.tag) != "Workbook":
            raise TargetUnavailableError(
                "el portal no entregó un archivo de datos de Mis Retenciones",
                diagnostic_code="retenciones_export_format_invalid",
            )

        filas: list[list[str]] = []
        for fila in (nodo for nodo in workbook.iter() if local_name(nodo.tag) == "Row"):
            celdas: list[str] = []
            for celda in (nodo for nodo in fila if local_name(nodo.tag) == "Cell"):
                indice = next(
                    (valor for clave, valor in celda.attrib.items() if local_name(clave) == "Index"),
                    None,
                )
                if indice:
                    try:
                        posicion = max(0, int(indice) - 1)
                    except ValueError as exc:
                        raise TargetUnavailableError(
                            "el SpreadsheetML de Mis Retenciones contiene índices inválidos",
                            diagnostic_code="retenciones_export_format_invalid",
                        ) from exc
                    while len(celdas) < posicion:
                        celdas.append("")
                dato = next((nodo for nodo in celda.iter() if local_name(nodo.tag) == "Data"), None)
                celdas.append("".join(dato.itertext()).strip() if dato is not None else "")
            if celdas:
                filas.append(celdas)
        if not filas:
            raise TargetUnavailableError(
                "el SpreadsheetML de Mis Retenciones no contiene filas exportables",
                diagnostic_code="retenciones_export_format_invalid",
            )

        salida = io.StringIO(newline="")
        csv.writer(salida).writerows(filas)
        try:
            destino.write_text(salida.getvalue(), encoding="utf-8-sig")
        except OSError as exc:
            raise TargetUnavailableError(
                "no se pudo guardar el CSV normalizado de Mis Retenciones",
                diagnostic_code="retenciones_export_write_failed",
            ) from exc

    async def _preparar_consulta(
        self, impuesto: str, tipo: str, desde: str, hasta: str, para_aplicativo: bool
    ) -> None:
        await self._asegurar_formulario()
        if not await self._configurar_modo_exportacion("siap" if para_aplicativo else ""):
            raise TargetUnavailableError(
                "no se pudo seleccionar el modo de exportación",
                diagnostic_code="retenciones_export_mode_failed",
            )
        codigo = str(impuesto or "").strip()
        etiqueta = _IMPUESTOS.get(codigo, codigo)
        if not await self._seleccionar_impuesto(etiqueta, codigo):
            raise TargetUnavailableError(
                "no se pudo seleccionar el impuesto en Mis Retenciones",
                diagnostic_code="retenciones_tax_not_selectable",
            )
        if not await self._seleccionar_tipo(tipo):
            raise TargetUnavailableError(
                "no se pudo seleccionar el tipo de operación en Mis Retenciones",
                diagnostic_code="retenciones_operation_not_selectable",
            )
        if not await self._seleccionar_rango_fechas(desde, hasta):
            raise TargetUnavailableError(
                "no se pudo ingresar el rango de fechas en Mis Retenciones",
                diagnostic_code="retenciones_date_range_failed",
            )
        await self._esperar(500)

    async def _configurar_modo_exportacion(self, modo: str) -> bool:
        esperado = (modo or "").strip().lower()
        if esperado not in {"", "siap"}:
            return False
        encontrado = esperado == ""
        for valor in ("ivaSimple", "siap"):
            checkbox = self.page.locator(f"input[type='checkbox'][value='{valor}']")
            try:
                if await checkbox.count() == 0:
                    continue
                marcado = await checkbox.is_checked()
                seleccionar = valor.lower() == esperado
                if seleccionar:
                    encontrado = True
                if seleccionar and not marcado:
                    await checkbox.check(timeout=5_000)
                elif not seleccionar and marcado:
                    await checkbox.uncheck(timeout=5_000)
            except Exception:
                return False
        return encontrado

    async def _seleccionar_impuesto(self, etiqueta: str, codigo: str) -> bool:
        actual = ""
        for selector in ("#selectImpuestos-assist", ".multiselect-single-label-text"):
            try:
                loc = self.page.locator(selector)
                if await loc.count():
                    actual = re.sub(r"\s+", " ", await loc.first.text_content() or "").strip()
                    if actual:
                        break
            except Exception:
                continue
        if actual and (codigo in actual or etiqueta in actual):
            return True

        entrada = self.page.locator("#selectImpuestos")
        try:
            await entrada.wait_for(state="visible", timeout=15_000)
            await entrada.click(timeout=5_000)
            try:
                await entrada.press("Control+A")
                await entrada.press("Backspace")
            except Exception:
                pass
        except Exception:
            pass
        dropdown = self.page.locator("#selectImpuestos-dropdown")
        for selector in ("#selectImpuestos_caret", ".multiselect-wrapper"):
            try:
                if not await dropdown.is_visible() and await self.page.locator(selector).count():
                    await self.page.locator(selector).first.click(timeout=3_000)
            except Exception:
                continue
        try:
            await dropdown.wait_for(state="visible", timeout=5_000)
        except Exception:
            pass
        try:
            seleccionadas = self.page.locator(
                "#selectImpuestos-multiselect-options li.multiselect-option.is-selected"
            )
            for indice in range(await seleccionadas.count()):
                try:
                    await seleccionadas.nth(indice).click(timeout=2_000)
                except Exception:
                    pass
        except Exception:
            pass
        for prefijo in ("IMP", "SS", "ADU", "SIR"):
            opcion = self.page.locator(f"#selectImpuestos-multiselect-option-{prefijo}_{codigo}")
            try:
                if await opcion.count():
                    await opcion.first.click(timeout=3_000)
                    return True
            except Exception:
                continue
        opciones = dropdown.locator('[role="group"][aria-label="Impositivas"]')
        scopes = [opciones, dropdown]
        for scope in scopes:
            for texto in _variantes_impuesto(etiqueta, codigo):
                try:
                    opcion = scope.get_by_role("option", name=texto)
                    if await opcion.count():
                        await opcion.first.click(timeout=3_000)
                        return True
                except Exception:
                    continue
            for patron in _patrones_impuesto(etiqueta, codigo):
                try:
                    opcion = scope.get_by_role("option", name=patron)
                    if await opcion.count():
                        await opcion.first.click(timeout=3_000)
                        return True
                except Exception:
                    continue
        return False

    async def _seleccionar_tipo(self, tipo: str) -> bool:
        contenedor = self.page.locator("#tipoOperacion")
        try:
            await contenedor.wait_for(state="visible", timeout=5_000)
            etiquetas = contenedor.locator("label")
            if await etiquetas.count() == 0:
                return False
            if (tipo or "").lower().startswith("ret"):
                patron = re.compile(r"retenci[oó]n", re.I)
            elif (tipo or "").lower().startswith("per"):
                patron = re.compile(r"percepci[oó]n", re.I)
            else:
                patron = re.compile(re.escape(tipo or ""), re.I)
            objetivo = etiquetas.filter(has_text=patron)
            if await objetivo.count():
                await objetivo.first.click(timeout=3_000)
                return True
            opcion = contenedor.get_by_label(patron)
            if await opcion.count():
                await opcion.first.check(timeout=3_000)
                return True
        except Exception:
            return False
        return False

    async def _seleccionar_rango_fechas(self, desde: str, hasta: str) -> bool:
        if not desde or not hasta:
            return False
        loc_desde = self.page.locator("#fechaRetencionDesde__input")
        loc_hasta = self.page.locator("#fechaRetencionHasta__input")
        try:
            await loc_desde.wait_for(state="attached", timeout=5_000)
            await loc_hasta.wait_for(state="attached", timeout=5_000)
        except Exception:
            return False
        try:
            actual_desde = (await loc_desde.input_value()).strip()
            actual_hasta = (await loc_hasta.input_value()).strip()
            if actual_desde == desde and actual_hasta == hasta:
                return True
        except Exception:
            pass
        if not await self._setear_fecha(loc_desde, desde):
            return False
        # El segundo datepicker depende del primero en la SPA. Esperar a que
        # Angular actualice sus límites evita que descarte el valor de ``hasta``.
        await self._esperar(500)
        return await self._setear_fecha(loc_hasta, hasta)

    async def _setear_fecha(self, campo: Any, valor: str) -> bool:
        for intento in range(3):
            try:
                await campo.wait_for(state="attached", timeout=5_000)
            except Exception:
                await self._esperar(400)
                continue
            try:
                await campo.click(timeout=2_000, force=True)
            except Exception:
                pass
            try:
                await campo.fill("", force=True)
                await campo.fill(valor, force=True)
                await campo.press("Enter")
                await self._esperar(300)
                if (await campo.input_value()).strip() == valor:
                    return True
            except Exception:
                pass
            if intento == 1:
                try:
                    await campo.click(timeout=2_000, force=True)
                    await campo.fill("", force=True)
                    await campo.type(valor, delay=50)
                    await campo.press("Enter")
                    await self._esperar(300)
                    if (await campo.input_value()).strip() == valor:
                        return True
                except Exception:
                    pass
            if intento == 2:
                try:
                    handle = await campo.element_handle()
                    if handle:
                        await handle.evaluate(
                            "(el, val) => { el.value = val; "
                            "el.dispatchEvent(new Event('input', {bubbles:true})); "
                            "el.dispatchEvent(new Event('change', {bubbles:true})); "
                            "el.dispatchEvent(new Event('blur', {bubbles:true})); }",
                            valor,
                        )
                        await self._esperar(300)
                        if (await campo.input_value()).strip() == valor:
                            return True
                except Exception:
                    pass
        try:
            if (await campo.input_value()).strip():
                return True
        except Exception:
            pass
        try:
            # El control usa un value accessor Angular. Invocar el setter
            # nativo dispara los eventos que la asignación directa puede omitir.
            await campo.evaluate(
                """(el, val) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, val);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""",
                valor,
            )
            await campo.press("Enter")
            await self._esperar(500)
            return bool((await campo.input_value()).strip())
        except Exception:
            return False

    async def _consultar_y_descargar(
        self, destino: Path, *, export_option: str | None = None, required_marker: str = ""
    ) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        boton_consultar = (
            self.page.locator("#btnConsultarRetenciones"),
            self.page.get_by_role("button", name=re.compile("Consultar", re.I)),
            self.page.locator("button:has-text('Consultar')"),
        )
        if not await self._clickear(boton_consultar, total_ms=12_000):
            raise TargetUnavailableError(
                "no se pudo iniciar la consulta de Mis Retenciones",
                diagnostic_code="retenciones_query_button_missing",
            )
        estado = await self._esperar_resultados()
        if estado != "results":
            raise TargetUnavailableError(
                "Mis Retenciones no devolvió resultados exportables",
                diagnostic_code="retenciones_no_results",
            )
        if required_marker:
            try:
                texto = await self.page.locator("body").inner_text(timeout=3_000)
            except Exception:
                texto = ""
            if required_marker.lower() not in texto.lower():
                raise TargetUnavailableError(
                    "los resultados no corresponden al modo IVA Simple",
                    diagnostic_code="iva_simple_result_marker_missing",
                )
        if not await self._clickear(
            (
                self.page.locator("#btnExportarOtrosFormatos"),
                self.page.get_by_role("button", name=re.compile("Exportar", re.I)),
                self.page.locator("button:has-text('Exportar')"),
            ),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(
                "no se encontró la opción de exportación de Mis Retenciones",
                diagnostic_code="retenciones_export_button_missing",
            )
        if not await self._seleccionar_formato(export_option):
            raise TargetUnavailableError(
                "no se pudo seleccionar el formato de exportación solicitado",
                diagnostic_code="retenciones_export_format_missing",
            )
        select_all = self.page.locator("#tablaResultadosexport_all_btnOK")
        try:
            if await select_all.is_visible(timeout=1_000):
                await select_all.click(timeout=4_000)
        except Exception:
            pass
        confirmar = self.page.locator("#modal-exportar-consulta_btnOK, #modal-sinresultados_btnOK")
        try:
            await confirmar.first.wait_for(state="visible", timeout=10_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo confirmar la exportación de Mis Retenciones",
                diagnostic_code="retenciones_export_confirmation_missing",
            ) from exc
        if not await self._clickear((confirmar,), total_ms=5_000):
            raise TargetUnavailableError(
                "no se pudo confirmar la exportación de Mis Retenciones",
                diagnostic_code="retenciones_export_confirmation_missing",
            )
        enlaces = (
            self.page.locator('#tablaExportarAplicativo .ag-row[row-index="0"] a[title="Descargar Archivo"]'),
            self.page.locator("#tablaExportarAplicativo a[title='Descargar Archivo']"),
            self.page.locator("a.boton_mt[download], a[download][href*='descarga']"),
        )
        enlace = None
        for candidato in enlaces:
            try:
                await candidato.first.wait_for(state="visible", timeout=8_000)
                enlace = candidato.first
                break
            except Exception:
                continue
        if enlace is None:
            raise TargetUnavailableError(
                "no apareció el enlace de descarga de Mis Retenciones",
                diagnostic_code="retenciones_download_link_missing",
            )
        try:
            target = await enlace.get_attribute("target")
        except Exception:
            target = ""
        if target == "_blank" and self._context is not None:
            return await self._capturar_descarga_nueva_pestana(enlace, destino)
        return await self.capturar_descarga(
            destino,
            enlace.click(timeout=5_000),
            espera_ms=20_000,
        )

    async def _capturar_descarga_nueva_pestana(self, enlace: Any, destino: Path) -> Path:
        """Captura el evento en la pestaña emergente usada por la grilla ARCA."""
        loop = asyncio.get_running_loop()
        recibida: asyncio.Future[Any] = loop.create_future()
        paginas: list[Any] = []

        def al_descargar(descarga: Any) -> None:
            if not recibida.done():
                recibida.set_result(descarga)

        def al_abrir_pagina(pagina: Any) -> None:
            paginas.append(pagina)
            pagina.on("download", al_descargar)

        self.page.on("download", al_descargar)
        self._context.on("page", al_abrir_pagina)
        try:
            await enlace.click(timeout=5_000)
            try:
                descarga = await asyncio.wait_for(recibida, timeout=20)
            except asyncio.TimeoutError as exc:
                raise TargetUnavailableError(
                    "el portal no entregó la descarga esperada",
                    diagnostic_code="portal_download_missing",
                ) from exc
            await descarga.save_as(str(destino))
        finally:
            try:
                self._context.remove_listener("page", al_abrir_pagina)
            except Exception:
                pass
            try:
                self.page.remove_listener("download", al_descargar)
            except Exception:
                pass
            for pagina in paginas:
                try:
                    pagina.remove_listener("download", al_descargar)
                except Exception:
                    pass
        if not destino.exists() or destino.stat().st_size == 0:
            raise TargetUnavailableError(
                "la descarga del portal llegó vacía",
                diagnostic_code="portal_download_empty",
            )
        return destino

    async def _esperar_resultados(self, timeout_ms: int = 30_000) -> str:
        limite = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < limite:
            for selector in ("#modal-sire", "#modal-confirmar"):
                try:
                    modal = self.page.locator(selector)
                    if await modal.count() and await modal.is_visible(timeout=200):
                        boton = self.page.locator(
                            "#modal-sire_btnOK" if selector == "#modal-sire" else "#modal-confirmar_btnOK"
                        )
                        try:
                            if await boton.count():
                                await boton.click(timeout=2_000)
                        except Exception:
                            pass
                        raise TargetUnavailableError(
                            "Mis Retenciones informó un error para la consulta",
                            diagnostic_code="retenciones_query_rejected",
                        )
                except TargetUnavailableError:
                    raise
                except Exception:
                    pass
            try:
                sin_resultados = self.page.locator("#modal-sinresultados, #mensajeSinResultados")
                if await sin_resultados.count() and await sin_resultados.first.is_visible(timeout=200):
                    return "no_results"
            except Exception:
                pass
            for selector in ("#btnExportarOtrosFormatos", "#tablaResultados"):
                try:
                    loc = self.page.locator(selector)
                    if await loc.count() and await loc.first.is_visible(timeout=200):
                        return "results"
                except Exception:
                    pass
            try:
                body = (await self.page.locator("body").inner_text(timeout=300)) or ""
                if re.search(r"no se encontraron resultados|no hay resultados para tu consulta", body, re.I):
                    return "no_results"
            except Exception:
                pass
            await self._esperar(250)
        return "no_results"

    async def _seleccionar_formato(self, export_option: str | None) -> bool:
        if export_option:
            patrones = {
                "siap": re.compile(r"Exportar\s+para\s+SIAP", re.I),
                "iva_simple": re.compile(r"Exportar\s+para\s+IVA\s+SIMPLE", re.I),
                "xls": re.compile(r"Exportar\s+a\s+Excel\s*\(\s*\.xls\s*\)", re.I),
            }
            patron = patrones.get(export_option.strip().lower())
            if patron is None:
                return False
            menu = self.page.locator(".dropdown-menu.show")
            for scope in (menu, self.page):
                for opcion in (
                    scope.get_by_role("button", name=patron),
                    scope.locator("button", has_text=patron),
                ):
                    try:
                        if await opcion.count() and await opcion.first.is_visible(timeout=2_000):
                            await opcion.first.click(timeout=5_000)
                            return True
                    except Exception:
                        continue
            return False
        menu = self.page.locator(".dropdown-menu.show")
        for extension in (".CSV", ".xls"):
            for scope in (menu, self.page):
                try:
                    opcion = scope.get_by_text(extension, exact=False)
                    if await opcion.count() and await opcion.first.is_visible(timeout=2_000):
                        await opcion.first.click(timeout=5_000)
                        return True
                except Exception:
                    continue
        return False
