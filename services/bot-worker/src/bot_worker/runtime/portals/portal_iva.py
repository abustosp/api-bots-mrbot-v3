"""Acceso a libros de Portal IVA (port desde V1/V2).

Incluye la selección de representado y período, la descarga de libros CSV,
la importación desde ARCA, el borrado de registros del libro y la importación
de TXT de comprobantes con alícuotas opcionales. No implementa presentación
de declaraciones.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

log = logging.getLogger("bot_worker.portals.portal_iva")

PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
LIVA_BASE = "https://liva.afip.gob.ar/liva/jsp"
_NO_ACCESO = re.compile(
    r"no posee activa la caracterizaci[oó]n requerida|"
    r"cuit sin representados v[aá]lidos con acceso a liquidaciones de iva",
    re.I,
)


def _cuit_formateado(cuit: str) -> str:
    digits = solo_digitos(cuit)
    if len(digits) != 11:
        raise TargetUnavailableError(
            "CUIT representado inválido", diagnostic_code="represented_cuit_invalid"
        )
    return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"


def _normalizar_periodo(periodo: str) -> str:
    """Normaliza AAAAMM o MM/AAAA al valor AAAAMM usado por Portal IVA."""
    value = str(periodo or "").strip()
    patterns = (
        (r"(\d{2})/(\d{4})", lambda m: f"{m.group(2)}{m.group(1)}"),
        (r"(\d{2})-(\d{4})", lambda m: f"{m.group(2)}{m.group(1)}"),
        (r"(\d{4})/(\d{2})", lambda m: f"{m.group(1)}{m.group(2)}"),
        (r"(\d{4})-(\d{2})", lambda m: f"{m.group(1)}{m.group(2)}"),
    )
    for pattern, convert in patterns:
        match = re.fullmatch(pattern, value)
        if match:
            value = convert(match)
            break
    if not re.fullmatch(r"\d{6}", value) or not 1 <= int(value[4:]) <= 12:
        raise TargetUnavailableError(
            "período inválido para Portal IVA",
            diagnostic_code="portal_iva_period_invalid",
        )
    return value


class PortalIvaPortal(PortalArca):
    """Selección SPA del Portal IVA y descarga de libros existentes."""

    nombre = "portal_iva"

    async def seleccionar_representado(
        self, cuit: str, *, cuit_representante: str | None = None
    ) -> None:
        """Selecciona el CUIT objetivo en el selector de relaciones del SPA."""
        digits = solo_digitos(cuit)
        formatted = _cuit_formateado(digits)
        await self._asegurar_portal_cargado()

        if await self._representado_activo(formatted):
            return
        if solo_digitos(cuit_representante or "") == digits:
            propio = self.page.get_by_text(
                re.compile(re.escape(formatted)), exact=False
            )
            if await self._primero_visible((propio,), total_ms=1_200) is not None:
                return

        await self._clickear(
            (
                self.page.get_by_title("cambio relación"),
                self.page.locator("[title*='cambio relación' i]"),
            ),
            total_ms=4_000,
        )
        # El título 'Representar a...' es común a todas las filas del selector:
        # se restringe por texto al CUIT pedido para no elegir otro representado.
        representar = self.page.get_by_title("Representar a...")
        elegido = await self._clickear(
            (
                representar.filter(has_text=formatted),
                representar.filter(has_text=digits),
            ),
            total_ms=5_000,
        )
        if not elegido:
            await self._clickear(
                (
                    representar,
                    self.page.get_by_text("Representar a...", exact=False),
                ),
                total_ms=5_000,
            )
        await self._esperar(700)

        if not elegido:
            # Solo cuando el filtro no encontró la fila se usa el clic genérico
            # seguido del clic por CUIT.
            elegido = await self._clickear(
                (
                    self.page.get_by_role("button", name=re.compile(re.escape(formatted))),
                    self.page.get_by_text(re.compile(re.escape(formatted)), exact=False),
                    self.page.get_by_text(re.compile(re.escape(digits)), exact=False),
                ),
                total_ms=8_000,
            )
        if elegido:
            await self._asegurar_portal_cargado()

        try:
            contenido = await self.page.content()
        except Exception:
            contenido = ""
        if _NO_ACCESO.search(contenido):
            raise TargetUnavailableError(
                "el CUIT no tiene una relación habilitada para Portal IVA",
                diagnostic_code="portal_iva_no_representados_validos",
            )
        if await self._representado_activo(formatted):
            return

        raise TargetUnavailableError(
            "no se pudo seleccionar el CUIT representado en Portal IVA",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def seleccionar_periodo(self, periodo: str) -> None:
        """Valida el período en Portal IVA y entra a la sección de libros.

        Solo navega por la pantalla de selección. No guarda borradores ni pulsa
        controles de continuación/presentación.
        """
        target = _normalizar_periodo(periodo)
        await self._asegurar_portal_cargado()
        await self._verificar_sesion()

        url = await self._url()
        if "changeRelation" in url:
            await self._abrir_inicio()

        selector_periodo = self.page.locator("#periodo")
        if await selector_periodo.count() == 0:
            await self._clickear(
                (
                    self.page.get_by_role(
                        "button",
                        name=re.compile(r"iva\.home\.btn\.nueva|nueva.*declaraci[oó]n", re.I),
                    ),
                    self.page.get_by_label(
                        re.compile(r"Sin texto \(iva\.home\.btn\.nueva", re.I)
                    ),
                    self.page.get_by_text(re.compile(r"Nueva", re.I)),
                ),
                total_ms=12_000,
            )
            await self._esperar(1_500)
            selector_periodo = await self._primero_visible(
                (self.page.locator("#periodo"),), total_ms=12_000
            )

        if selector_periodo is not None and await selector_periodo.count() > 0:
            opciones = selector_periodo.locator("option")
            valor_elegido = ""
            for indice in range(await opciones.count()):
                opcion = opciones.nth(indice)
                valor = str(await opcion.get_attribute("value") or "").strip()
                etiqueta = str(await opcion.text_content() or "").strip()
                if target in {
                    _periodo_opcional(valor),
                    _periodo_opcional(etiqueta),
                }:
                    valor_elegido = valor
                    break
            if not valor_elegido:
                raise TargetUnavailableError(
                    "el período solicitado no está disponible en Portal IVA",
                    diagnostic_code="portal_iva_period_not_available",
                )
            await selector_periodo.select_option(value=valor_elegido)
            validado = await self._clickear(
                (
                    self.page.get_by_role("button", name=re.compile(r"Validar", re.I)),
                    self.page.get_by_label(
                        re.compile(r"Sin texto \(iva\.btn\.home\.validar\.periodo", re.I)
                    ),
                ),
                total_ms=10_000,
            )
            if not validado:
                raise TargetUnavailableError(
                    "no se pudo validar el período de Portal IVA",
                    diagnostic_code="portal_iva_period_validation_failed",
                )
            await self._esperar(1_500)
        else:
            # Si la SPA ya está posicionada en un período, aceptarlo solo cuando
            # el texto visible coincide exactamente con el solicitado.
            etiqueta = self.page.locator("#periodoPresentacion")
            texto = await self._texto(etiqueta.first) if await etiqueta.count() else ""
            if _periodo_opcional(texto) != target:
                raise TargetUnavailableError(
                    "no se encontró el selector del período en Portal IVA",
                    diagnostic_code="portal_iva_period_selector_missing",
                )

        await self._clickear(
            (
                self.page.get_by_role("button", name=re.compile(r"iva\.btn\.home\.liva", re.I)),
                self.page.get_by_label(re.compile(r"Sin texto \(iva\.btn\.home\.liva", re.I)),
                self.page.get_by_role("button", name=re.compile(r"Libro IVA", re.I)),
            ),
            total_ms=5_000,
        )
        await self._esperar(1_000)

    async def preparar_periodo(
        self,
        periodo: str,
        *,
        operaciones_ng_o_e: bool = False,
        prorrateo_global: bool = False,
        prorrateo_asignacion_directa: bool = False,
        prorrateo_ambos: bool = False,
        importacion_definitiva_bienes: bool = False,
        importacion_servicios: bool = False,
        regimen_turiva: bool = False,
        bienes_usados: bool = False,
        ninguna_anteriores: bool = True,
    ) -> list[dict[str, str]]:
        """Port V1 ``prepare_period`` including the declarations options.

        The period selector is retained by :meth:`seleccionar_periodo`; once LIVA
        is open V1 applies all flags and saves them before any import or download.
        Returning the options keeps V1's ``periodos_seleccionables`` observable.
        """
        disponibles: list[dict[str, str]] = []
        selector = self.page.locator("#periodo")
        try:
            opciones = selector.locator("option")
            for indice in range(await opciones.count()):
                opcion = opciones.nth(indice)
                valor = str(await opcion.get_attribute("value") or "").strip()
                etiqueta = str(await opcion.text_content() or "").strip()
                if valor or etiqueta:
                    disponibles.append({"value": valor, "label": etiqueta})
        except Exception:
            pass
        await self.seleccionar_periodo(periodo)

        async def marcar(selector_css: str, activo: bool) -> None:
            control = self.page.locator(selector_css)
            try:
                if not await control.count():
                    return
                marcado = await control.first.is_checked()
                if activo and not marcado:
                    await control.first.check(timeout=5_000)
                elif not activo and marcado:
                    await control.first.uncheck(timeout=5_000)
            except Exception:
                return

        # La SPA arma las opciones de la declaración después de abrir LIVA: se
        # espera el anclaje del control para no omitir el marcado en silencio.
        try:
            await self.page.locator("#operacionesNoGravadasExentas").first.wait_for(
                state="attached", timeout=20_000
            )
        except Exception:
            log.warning(
                "Portal IVA: #operacionesNoGravadasExentas no quedó disponible; "
                "se omite el marcado de opciones de la declaración"
            )
        await marcar("#operacionesNoGravadasExentas", operaciones_ng_o_e)
        if operaciones_ng_o_e:
            prorrateo = "3" if prorrateo_ambos else "2" if prorrateo_global else "1" if prorrateo_asignacion_directa else ""
            if prorrateo:
                try:
                    await self.page.locator("#tipoProrrateo").select_option(value=prorrateo)
                except Exception:
                    pass
        await marcar("#importacionDefinitivaBienes", importacion_definitiva_bienes)
        await marcar("#importacionServicios", importacion_servicios)
        await marcar("#regimenTurIVA", regimen_turiva)
        await marcar("#bienesUsadosMaterialReciclable", bienes_usados)
        await marcar("#ningunaOperacion", ninguna_anteriores)
        try:
            guardar = self.page.locator("#btnGuardar")
            if await guardar.count():
                await guardar.first.click(timeout=10_000)
                await self._esperar(2_000)
        except Exception:
            pass
        return disponibles

    async def descargar_libro(
        self,
        libro: str,
        destino: Path,
        *,
        refrescar_desde_arca: bool = False,
        limpiar_despues: bool = False,
    ) -> Path:
        """Abre el libro existente pedido y guarda su CSV en ``destino``.

        ``refrescar_desde_arca`` ejecuta 'Importar desde ARCA' antes de bajar
        el CSV y ``limpiar_despues`` borra los registros al terminar (flujo V1
        ``ejecutar_descarga``). Ambos son opt-in porque modifican el libro del
        organismo.
        """
        book = _libro_valido(libro)
        destino = Path(destino)
        locators_csv = (
            self.page.get_by_role("button", name=re.compile(r"CSV", re.I)),
            self.page.get_by_role("link", name=re.compile(r"CSV", re.I)),
            self.page.get_by_text("CSV", exact=True),
        )
        if await self._primero_visible(locators_csv, total_ms=500) is None:
            await self._abrir_libro(book)

        if refrescar_desde_arca:
            try:
                await self.importar_desde_arca(book)
            except TargetUnavailableError:
                # V1 ignora la falla de la importación previa y baja el CSV igual.
                pass

        async def descargar_csv() -> None:
            if not await self._clickear(locators_csv, total_ms=10_000):
                raise TargetUnavailableError(
                    f"no se encontró la descarga CSV del libro de {book}",
                    diagnostic_code="portal_iva_csv_button_missing",
                )

        await self.capturar_descarga(destino, descargar_csv, espera_ms=45_000)
        _extraer_csv_si_es_zip(destino)
        if limpiar_despues:
            await self.eliminar_todos_registros(book)
        await self._volver_al_menu()
        return destino

    async def importar_libro(
        self, libro: str, comprobantes: Path, alicuotas: Path | None = None
    ) -> bool:
        """Reemplaza los registros del libro por los TXT dados (flujo V1 ``ejecutar_carga``).

        Abre el libro, borra sus registros, importa los archivos y vuelve al menú.
        """
        book = _libro_valido(libro)
        await self._abrir_libro(book)
        await self.eliminar_todos_registros(book)
        await self.importar_txt(book, comprobantes, alicuotas)
        await self._volver_al_menu()
        return True

    async def importar_desde_arca(self, libro: str) -> bool:
        """Pulsa 'Importar desde ARCA' en el libro abierto (port de ``import_from_arca``)."""
        _libro_valido(libro)
        if not await self._clickear(
            (
                self.page.get_by_role("button", name=re.compile(r"Importar", re.I)),
                self.page.get_by_text("Importar", exact=False),
            ),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(
                "no se encontró el menú Importar de Portal IVA",
                diagnostic_code="portal_iva_import_menu_missing",
            )
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"Importar desde ARCA", re.I)),
                self.page.get_by_text(re.compile(r"Importar desde ARCA", re.I)),
            ),
            total_ms=12_000,
        ):
            raise TargetUnavailableError(
                "no se encontró 'Importar desde ARCA' en Portal IVA",
                diagnostic_code="portal_iva_import_arca_missing",
            )
        await self._esperar(2_000)
        if not await self._clickear(
            (self.page.locator("#btnImportarAFIPImportar"),), total_ms=5_000
        ):
            raise TargetUnavailableError(
                "no se encontró el botón de importación desde ARCA",
                diagnostic_code="portal_iva_import_arca_button_missing",
            )
        await self._esperar(8_000)
        await self._cerrar_modal_procesado()
        await self._esperar(3_000)
        return True

    async def eliminar_todos_registros(self, libro: str) -> bool:
        """Pulsa 'Eliminar Todos...' y confirma (port de ``delete_all_records``).

        Devuelve ``False`` sin error cuando el libro no tiene registros para borrar.
        """
        _libro_valido(libro)
        if not await self._clickear(
            (
                self.page.get_by_role("button", name="Eliminar Todos..."),
                self.page.get_by_text("Eliminar Todos...", exact=False),
            ),
            total_ms=8_000,
        ):
            return False
        confirmado = await self._clickear(
            (
                self.page.locator(".bootstrap-dialog .btn-danger"),
                self.page.locator(".modal.in .btn-danger"),
                self.page.locator(".modal-dialog .btn-danger"),
            ),
            total_ms=5_000,
        )
        if not confirmado:
            await self._cerrar_modal_procesado()
            return False
        await self._esperar(3_000)
        return True

    async def importar_txt(
        self, libro: str, comprobantes: Path, alicuotas: Path | None = None
    ) -> bool:
        """Sube el TXT de comprobantes y, si se pasa, el de alícuotas (port de ``import_files``)."""
        book = _libro_valido(libro)
        etiqueta_cbte, etiqueta_alic = _ETIQUETAS_ARCHIVOS[book]
        if not await self._clickear(
            (
                self.page.get_by_role("button", name=re.compile(r"Importar", re.I)),
                self.page.get_by_text("Importar", exact=False),
            ),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(
                "no se encontró el menú Importar de Portal IVA",
                diagnostic_code="portal_iva_import_menu_missing",
            )
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"Importar Archivos", re.I)),
                self.page.get_by_text(re.compile(r"Importar Archivos", re.I)),
            ),
            total_ms=12_000,
        ):
            raise TargetUnavailableError(
                f"no se encontró 'Importar Archivos' para {book}",
                diagnostic_code="portal_iva_import_files_missing",
            )
        boton = self.page.locator("#btnImportarArchivosImportar")
        await self.page.get_by_label(etiqueta_cbte).set_input_files(str(comprobantes))
        await self._clickear((boton,), total_ms=5_000)
        await self._esperar(2_000)
        if alicuotas is not None:
            await self.page.get_by_label(etiqueta_alic).set_input_files(str(alicuotas))
            await self._clickear((boton,), total_ms=5_000)
            await self._esperar(2_000)
        await self._clickear((boton,), total_ms=5_000)
        await self._esperar(5_000)
        await self._cerrar_modal_procesado()
        await self._esperar(3_000)
        return True

    async def descartar_borrador(self) -> bool:
        """Descarta el borrador de la declaración activa (port de V1 ``discard_draft``).

        Vuelve al menú del borrador cuando 'Vista previa' no está a la vista,
        abre la vista previa, confirma 'Descartar Borrador' y verifica que el
        borrador ya no exista. Solo se llama después de terminar las descargas
        pedidas.
        """
        return await self.paso("descartar_borrador", self._descartar_borrador())

    async def _descartar_borrador(self) -> bool:
        vista_previa = self.page.locator("#btnVistaPrevia")
        if await self._primero_visible((vista_previa,), total_ms=1_000) is None:
            # Las descargas terminan en el libro o en el inicio: el menú del
            # borrador es donde vive 'Vista previa' (flujo V2).
            await self._volver_al_menu()
            await self._clickear(
                (
                    self.page.get_by_role(
                        "link", name=re.compile(r"Borrador\s+Presentaci[oó]n", re.I)
                    ),
                    self.page.get_by_role(
                        "link", name=re.compile(r"Libro IVA - Borrador", re.I)
                    ),
                    self.page.get_by_text("Libro IVA - Borrador", exact=False),
                ),
                total_ms=8_000,
            )
            await self._esperar(2_000)

        if not await self._clickear(
            (
                vista_previa,
                self.page.get_by_role(
                    "button", name=re.compile(r"Vista\s+previa", re.I)
                ),
                self.page.get_by_text("Vista previa", exact=False),
            ),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(
                "no se encontró 'Vista previa' del borrador en Portal IVA",
                diagnostic_code="portal_iva_preview_missing",
            )

        eliminar = self.page.locator("#btnEliminarBorrador")
        try:
            await eliminar.first.wait_for(state="visible", timeout=20_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no apareció 'Eliminar Borrador' en Portal IVA",
                diagnostic_code="portal_iva_draft_delete_missing",
            ) from exc
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        # El diálogo real no usa .modal ni .bootstrap-dialog y el primer clic
        # puede quedar sin efecto si el manejador aún no está enlazado: se
        # reintenta hasta ver el botón de confirmación.
        confirmar = self.page.locator("button.btn-danger").filter(
            has_text=re.compile(r"Descartar\s+Borrador", re.I)
        )
        for _ in range(4):
            if not await self._clickear((eliminar,), total_ms=10_000):
                raise TargetUnavailableError(
                    "no se pudo pulsar 'Eliminar Borrador' en Portal IVA",
                    diagnostic_code="portal_iva_draft_delete_click_failed",
                )
            if await self._primero_visible((confirmar,), total_ms=6_000) is not None:
                break
            await self._esperar(1_500)

        if not await self._clickear(
            (
                confirmar,
                self.page.locator("[role='dialog'] button.btn-danger").filter(
                    has_text=re.compile(r"Descartar", re.I)
                ),
                self.page.get_by_role(
                    "button", name=re.compile(r"Descartar\s+Borrador", re.I)
                ),
            ),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(
                "no se pudo confirmar 'Descartar Borrador' en Portal IVA",
                diagnostic_code="portal_iva_draft_confirm_failed",
            )
        await self._esperar(4_000)

        # Un borrador descartado deja de existir: el botón no debe seguir visible.
        if await self._primero_visible((eliminar,), total_ms=2_000) is not None:
            raise TargetUnavailableError(
                "el borrador sigue presente tras confirmar el descarte",
                diagnostic_code="portal_iva_draft_still_present",
            )
        return True

    async def _abrir_libro(self, book: str) -> None:
        """Entra al libro de ventas o compras desde el menú de Portal IVA."""
        href = "verVentas.do" if book == "ventas" else "verCompras.do"
        nombre = "Ventas" if book == "ventas" else "Compras"
        registrados = re.compile(rf"Libro\s+{nombre}\s+Registr", re.I)
        continuar = re.compile(rf"Continuar\s+al\s+Libro\s+{nombre}", re.I)
        titulo = re.compile(rf"Libro\s+{nombre}", re.I)
        abierto = await self._clickear(
            (
                self.page.get_by_role("link", name=registrados),
                self.page.get_by_role("link", name=continuar),
                self.page.get_by_role("link", name=titulo),
                self.page.locator(f"a[href*='{href}']").filter(has_text=titulo),
                self.page.locator(f"a[href*='{href}']"),
                self.page.get_by_role("button", name=continuar),
            ),
            total_ms=10_000,
        )
        if not abierto:
            raise TargetUnavailableError(
                f"no se encontró el acceso al libro de {book} en Portal IVA",
                diagnostic_code="portal_iva_book_not_visible",
            )
        await self._esperar(1_500)

    async def _cerrar_modal_procesado(self) -> None:
        """Marca 'Procesada' y cierra el diálogo, como ``maybe_click_processed`` y ``maybe_close_modal``."""
        await self._clickear(
            (
                self.page.get_by_text("Procesada", exact=False),
                self.page.get_by_text("Procesando", exact=False),
            ),
            total_ms=5_000,
        )
        await self._clickear(
            (
                self.page.locator(".bootstrap-dialog .close"),
                self.page.locator(".bootstrap-dialog [data-dismiss='modal']"),
                self.page.get_by_role("button", name=re.compile(r"Cancelar|Cerrar|Close", re.I)),
            ),
            total_ms=5_000,
        )

    async def _representado_activo(self, formatted: str) -> bool:
        banner = self.page.get_by_text(
            re.compile(rf"Representando\s+a:\s*{re.escape(formatted)}", re.I)
        )
        if await self._primero_visible((banner,), total_ms=500) is not None:
            return True
        activo = self.page.locator("span.nombre-propio.nombre-activo.pull-right")
        if await activo.count():
            return solo_digitos(await self._texto(activo.first)).endswith(
                solo_digitos(formatted)
            )
        return False

    async def _asegurar_portal_cargado(self) -> None:
        for state in ("domcontentloaded", "networkidle"):
            try:
                await self.page.wait_for_load_state(state, timeout=20_000)
            except Exception:
                pass
        await self._esperar(1_500)
        for _ in range(2):
            try:
                html = await self.page.content()
            except Exception:
                html = ""
            compact = re.sub(r"\s+", "", html).lower()
            if compact and len(compact) >= 120 and compact != "<html><head></head><body></body></html>":
                return
            await self._abrir_inicio()
        raise TargetUnavailableError(
            "no se pudo cargar Portal IVA",
            diagnostic_code="portal_iva_page_blank",
        )

    async def _abrir_inicio(self) -> None:
        token = f"{int(time.time() * 1000):x}"[-6:]
        await self.abrir_url(f"{PORTAL_IVA_INIT_URL}?_k={token}", timeout_ms=20_000)
        await self._esperar(1_500)

    async def _verificar_sesion(self) -> None:
        expiracion = self.page.get_by_text(
            re.compile(r"usuario no est[aá] logueado|sesi[oó]n ha expirado", re.I)
        )
        if await self._primero_visible((expiracion,), total_ms=500) is not None:
            raise TargetUnavailableError(
                "la sesión de Portal IVA expiró",
                diagnostic_code="portal_iva_session_expired",
            )

    async def _volver_al_menu(self) -> None:
        """Vuelve a la lista del portal por navegación, sin alterar datos."""
        url = await self._url()
        if "menuPresentacion" in url:
            return
        if await self._clickear(
            (
                self.page.locator("a[href*='menuPresentacion.do']"),
                self.page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I)),
                self.page.get_by_role("link", name=re.compile(r"Inicio", re.I)),
            ),
            total_ms=1_500,
        ):
            await self._esperar(1_000)
            return
        if "liva.afip.gob.ar" in url:
            try:
                await self.abrir_url(f"{LIVA_BASE}/menuPresentacion.do", timeout_ms=15_000)
            except Exception:
                return


_ETIQUETAS_ARCHIVOS = {
    "ventas": ("Archivo Ventas *", "Archivo IVA Ventas *"),
    "compras": ("Archivo Compras *", "Archivo IVA Compras *"),
}


def _libro_valido(libro: str) -> str:
    book = str(libro or "").strip().lower()
    if book not in _ETIQUETAS_ARCHIVOS:
        raise TargetUnavailableError(
            "tipo de libro desconocido para Portal IVA",
            diagnostic_code="portal_iva_book_unknown",
        )
    return book


def _extraer_csv_si_es_zip(destino: Path) -> None:
    """Portal IVA entrega el CSV dentro de un ZIP: lo deja plano en ``destino``.

    Sin esto el artefacto ``text/csv`` sería binario y la muestra JSON
    arrastraría bytes NUL que PostgreSQL rechaza.
    """
    import zipfile

    if not zipfile.is_zipfile(destino):
        return
    with zipfile.ZipFile(destino) as archivo:
        csvs = [n for n in archivo.namelist() if n.lower().endswith(".csv")]
        if not csvs:
            raise TargetUnavailableError(
                "el ZIP de Portal IVA no trae CSV",
                diagnostic_code="portal_csv_unexpected_format",
            )
        contenido = archivo.read(csvs[0])
    destino.write_bytes(contenido)


def _periodo_opcional(value: str) -> str:
    try:
        return _normalizar_periodo(value)
    except TargetUnavailableError:
        return ""
