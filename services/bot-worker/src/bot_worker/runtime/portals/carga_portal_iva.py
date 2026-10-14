"""Portal de carga de Portal IVA (LIVA): TXT de libros y CSV de apertura (port V1 ``carga_portal_iva_bot``).

El plugin ``bots/carga_portal_iva`` decide el orden y los reintentos; este modulo
sabe como operar la pantalla de LIVA. Porta de V1: abrir el libro, borrar los
registros previos (para no duplicar), importar los dos archivos de un libro con
verificacion de "Procesada", cargar los CSV de apertura con el modal de credito
fiscal y volver al inicio navegando por URL, sin clics que descarten la carga.

Las rutas de los archivos llegan ya resueltas dentro de ``work_dir`` (la
resuelve el plugin con ``artifact_store.resolve``); este portal no acepta
rutas relativas.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

log = logging.getLogger("bot_worker.portals.carga_portal_iva")

LIVA_BASE = "https://liva.afip.gob.ar/liva/jsp"
URL_MENU_PRESENTACION = f"{LIVA_BASE}/menuPresentacion.do"
URL_CREDITO_FISCAL_COMPRAS = f"{LIVA_BASE}/verComprasCreditoFiscalComputableGlobal.do"
URL_MENU_INFO_ADICIONAL = f"{LIVA_BASE}/menuInfoAdicional.jsp"

ETIQUETA_ARCHIVO = {
    "ventas": ("Archivo Ventas *", "Archivo IVA Ventas *"),
    "compras": ("Archivo Compras *", "Archivo IVA Compras *"),
}
HREF_LIBRO = {"ventas": "verVentas.do", "compras": "verCompras.do"}
# Enlace de cada seccion de CSV en "Informacion adicional" (V1 ``links_map`` y ``href_map``).
SECCION_CSV = {
    "CF": (re.compile(r"Crédito\s+Fiscal(?!\s+Comp)", re.I), "d=c"),
    "CF Restitucion": (re.compile(r"Restituci[oó]n.*Crédito\s+Fiscal", re.I), "d=rc"),
    "DF": (re.compile(r"Débito\s+Fiscal(?!\s+Comp)", re.I), "d=d"),
    "DF Restitucion": (re.compile(r"Restituci[oó]n.*Débito\s+Fiscal", re.I), "d=rd"),
}
_PROCESANDO = re.compile(r"Procesando", re.I)
_PROCESADA = re.compile(r"Procesada", re.I)


class CargaPortalIvaPortal(PortalArca):
    """Operaciones de carga sobre LIVA: libros, borrado, importacion y aperturas CSV."""

    nombre = "carga_portal_iva"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Mismo criterio que la descarga de libros (selector del SPA de Portal IVA)."""
        from .portal_iva import PortalIvaPortal

        await PortalIvaPortal(self.page, captcha=self._captcha, context=self._context).seleccionar_representado(cuit)

    async def preparar_periodo(self, periodo: str, **opciones: bool) -> list[dict[str, str]]:
        """Uses the shared Portal IVA V1 period-preparation flow for loads."""
        from .portal_iva import PortalIvaPortal

        portal = PortalIvaPortal(self.page, captcha=self._captcha, context=self._context)
        return await portal.preparar_periodo(periodo, **opciones)

    # ------------------------------------------------------------------
    # Esperas y utilidades

    async def esperar_pantalla_carga(self, timeout_ms: int = 60_000) -> None:
        """Espera a que desaparezcan los indicadores de carga de LIVA (como el V1)."""
        selectores = ("#divCargando", "#cargando", ".cargando", ".loading", ".spinner", ".overlay", ".modal-backdrop")
        limite = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < limite:
            ocupado = False
            for sel in selectores:
                try:
                    loc = self.page.locator(sel)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        ocupado = True
                        break
                except Exception:
                    continue
            if not ocupado:
                try:
                    ocupado = await self.page.get_by_text(re.compile(r"^\s*(Procesando|Cargando)\s*\.{0,3}\s*$", re.I)).first.is_visible(timeout=300)
                except Exception:
                    ocupado = False
            if not ocupado:
                break
            await self._esperar(1_000)
        await self.esperar_procesando()

    async def esperar_procesando(self) -> None:
        """Espera a que desaparezca "Procesando" (hasta 60 s), como V1 ``_esperar_procesando``."""
        procesando = self.page.get_by_text(_PROCESANDO)
        try:
            if await procesando.count() > 0:
                try:
                    await procesando.first.wait_for(state="visible", timeout=2_000)
                    await procesando.first.wait_for(state="hidden", timeout=60_000)
                except Exception:
                    pass
            await self._esperar(1_500)
        except Exception:
            pass

    async def _verificar_sesion(self) -> None:
        expiracion = self.page.get_by_text(
            re.compile(r"usuario no est[aá] logueado|sesi[oó]n ha expirado", re.I)
        )
        if await self._primero_visible((expiracion,), total_ms=500) is not None:
            raise TargetUnavailableError(
                "la sesion de Portal IVA expiro",
                diagnostic_code="portal_iva_session_expired",
            )

    async def _cerrar_avisos(self) -> None:
        """Cierra modales y notificaciones previas que tapan la pantalla (V1 paso 0)."""
        for localizador in (self.page.locator(".bootstrap-dialog .close"), self.page.locator("#btnTareasCerrar")):
            await self._clickear((localizador,), total_ms=2_000)
        await self._esperar(1_500)

    # ------------------------------------------------------------------
    # Navegacion

    async def volver_al_inicio(self) -> None:
        """Vuelve a ``menuPresentacion.do`` por URL (V1 ``_return_to_home``).

        No hace clics de UI primero: un clic en "Volver" puede abrir un dialogo
        que descarta la importacion ya hecha. Los clics quedan como respaldo.
        """
        await self._verificar_sesion()
        await self.esperar_procesando()
        if "menuPresentacion" in await self._url():
            return
        try:
            await self.page.goto(URL_MENU_PRESENTACION, wait_until="domcontentloaded", timeout=15_000)
            await self.esperar_procesando()
            if "menuPresentacion" in await self._url():
                return
        except Exception:
            pass
        for localizadores in (
            (self.page.locator("a[href*='menuPresentacion.do']"),),
            (self.page.get_by_role("link", name=re.compile(r"Inicio", re.I)),),
            (self.page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I)),),
        ):
            if await self._clickear(localizadores, total_ms=5_000):
                await self.esperar_procesando()
                if "menuPresentacion" in await self._url():
                    return

    async def _click_robusto(self, localizadores, *, total_ms: int = 20_000) -> bool:
        """Como ``click_first`` del V1: prueba click normal, luego forzado y por JS aunque no sea visible."""
        limite = asyncio.get_running_loop().time() + total_ms / 1000
        while True:
            for loc in localizadores:
                try:
                    if await loc.count() == 0:
                        continue
                    objetivo = loc.first
                    for modo in ("normal", "force", "js"):
                        try:
                            if modo == "normal":
                                await objetivo.click(timeout=3_000)
                            elif modo == "force":
                                await objetivo.click(timeout=3_000, force=True)
                            else:
                                await objetivo.evaluate("el => el.click()")
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue
            if asyncio.get_running_loop().time() >= limite:
                return False
            await self._esperar(1_000)

    async def abrir_libro(self, libro: str) -> None:
        """Abre el libro de ventas o compras (V1 ``_abrir_libro``)."""
        if libro not in HREF_LIBRO:
            raise TargetUnavailableError(
                "tipo de libro desconocido para la carga",
                diagnostic_code="portal_iva_book_unknown",
            )
        await self._verificar_sesion()
        await self.esperar_pantalla_carga()
        nombre = "Libro Ventas" if libro == "ventas" else "Libro Compras"
        abierto = await self._click_robusto(
            (
                self.page.locator(f"a[href*='{HREF_LIBRO[libro]}']"),
                self.page.get_by_role("link", name=re.compile(rf"{nombre}\s+Registr", re.I)),
                self.page.get_by_role("link", name=re.compile(nombre, re.I)),
                self.page.locator("#btnLibroVentas" if libro == "ventas" else "#btnLibroCompras"),
            ),
            total_ms=40_000,
        )
        if not abierto:
            # Respaldo: URL directa del libro (el click puede perderse con el SPA cargando).
            with contextlib.suppress(Exception):
                await self.page.goto(f"{LIVA_BASE}/{HREF_LIBRO[libro]}", wait_until="domcontentloaded", timeout=30_000)
                await self.esperar_pantalla_carga()
                abierto = any(
                    marca in await self._url()
                    for marca in (HREF_LIBRO[libro], f"ver{libro.capitalize()}")
                )
        if not abierto:
            await self.volver_al_inicio()
            await self.esperar_procesando()
            raise TargetUnavailableError(
                f"no se encontro el acceso a {nombre}",
                diagnostic_code="portal_iva_book_not_found",
            )
        await self._esperar(3_000)
        await self.esperar_procesando()

    # ------------------------------------------------------------------
    # Borrado previo

    async def eliminar_todo(self) -> bool:
        """Borra todos los registros del libro abierto (V1 ``_eliminar_todo``).

        Devuelve ``False`` si no hay boton de borrado o no se confirmo. El plugin
        decide si eso es un error: sin borrado previo la importacion duplicaria.
        """
        await self._verificar_sesion()
        await self._esperar(3_000)
        eliminado = await self._clickear(
            (
                self.page.get_by_role("button", name=re.compile(r"Eliminar\s+Todos?\.\.\.?", re.I)),
                self.page.locator("#btnEliminarTodo"),
            ),
            total_ms=8_000,
        )
        if not eliminado:
            return False
        await self._esperar(1_500)
        confirmado = await self._clickear(
            (
                self.page.locator(".bootstrap-dialog .btn-danger").filter(has_text=re.compile(r"Eliminar", re.I)),
                self.page.locator(".modal.in .btn-danger"),
                self.page.get_by_role("button", name=re.compile(r"Eliminar", re.I)),
            ),
            total_ms=5_000,
        )
        if not confirmado:
            await self._clickear((self.page.locator(".bootstrap-dialog .close"),), total_ms=3_000)
            return False
        await self._esperar(3_000)
        return True

    # ------------------------------------------------------------------
    # Importacion de TXT

    async def importar_txt(self, libro: str, comprobantes: Path, alicuotas: Path) -> None:
        """Importa un par comprobantes + alicuotas y verifica "Procesada" (V1 ``_importar_txts``).

        Las rutas deben venir resueltas dentro de ``work_dir``. El boton de importar se
        pulsa una sola vez; un fallo lanza error y el plugin decide el reintento.
        """
        comprobantes = Path(comprobantes)
        alicuotas = Path(alicuotas)
        if not comprobantes.is_file() or not alicuotas.is_file():
            raise TargetUnavailableError(
                "archivo de importacion ausente en work_dir",
                diagnostic_code="carga_input_missing",
            )
        await self._verificar_sesion()
        await self._cerrar_avisos()
        if not await self._clickear(
            (self.page.locator("#btnDropdownImportar"), self.page.get_by_role("button", name=re.compile(r"Importar", re.I))),
            total_ms=10_000,
        ):
            raise TargetUnavailableError(f"no se encontro Importar para {libro}", diagnostic_code="carga_import_menu_missing")
        await self._esperar(3_000)
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"Importar\s+(?:desde\s+)?Archivos?", re.I)),
                self.page.locator("#lnkImportarArchivo"),
            ),
            total_ms=12_000,
        ):
            raise TargetUnavailableError(f"no se encontro Importar Archivos para {libro}", diagnostic_code="carga_import_link_missing")
        await self._esperar(3_000)
        if libro == "compras":
            await self._elegir_cuit_comprador()
        comp_label, alic_label = ETIQUETA_ARCHIVO[libro]
        await self.page.get_by_label(comp_label).set_input_files(str(comprobantes), timeout=15_000)
        await self.page.get_by_label(alic_label).set_input_files(str(alicuotas), timeout=15_000)
        await self._esperar(1_500)
        await self.page.locator("#btnImportarArchivosImportar").evaluate("el => el.click()")
        await self._esperar(3_000)
        await self.esperar_procesando()
        if not await self._primero_visible((self.page.get_by_text(_PROCESADA),), total_ms=8_000):
            raise TargetUnavailableError(
                f"no se detecto 'Procesada' tras importar {libro}",
                diagnostic_code="carga_not_processed",
            )
        await self._clickear((self.page.get_by_text(_PROCESADA),), total_ms=5_000)
        await self._esperar(2_000)
        await self._clickear((self.page.locator("#btnTareasCerrar"),), total_ms=5_000)
        await self._esperar(3_000)

    async def _elegir_cuit_comprador(self) -> None:
        """En compras, elige el CUIT del comprador explicitamente (V1)."""
        opciones = self.page.locator("#cuitArchivo option")
        if await opciones.count() == 0:
            return
        valor = await opciones.first.get_attribute("value")
        if valor:
            await self.page.locator("#cuitArchivo").select_option(valor, timeout=5_000)
        await self._esperar(1_500)

    # ------------------------------------------------------------------
    # CSV de apertura

    async def abrir_seccion_csv(self, seccion: str) -> None:
        """Abre "Informacion adicional" y entra a la seccion CSV (V1 ``_navegar_a_info_adicional``)."""
        if seccion not in SECCION_CSV:
            raise TargetUnavailableError(
                "seccion de apertura desconocida",
                diagnostic_code="carga_csv_section_unknown",
            )
        if "menuInfoAdicional" not in await self._url():
            await self._verificar_sesion()
            if await self._clickear((self.page.locator("#btnAjustes"),), total_ms=3_000):
                await self._esperar(2_000)
            if not await self._clickear((self.page.locator("a[href*='menuInfoAdicional']"),), total_ms=5_000):
                await self.page.goto(URL_MENU_INFO_ADICIONAL, wait_until="domcontentloaded", timeout=15_000)
            await self._esperar(2_000)
        patron, href = SECCION_CSV[seccion]
        url_seccion = f"{LIVA_BASE}/verInfoAdicionalProgreso.do?{href}"
        for intento in (1, 2, 3):
            try:
                await self.page.goto(url_seccion, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            await self.esperar_pantalla_carga()
            if await self._seccion_cargada():
                return
            if intento == 2 and await self._click_robusto(
                (
                    self.page.locator(f".form-group a.btn-primary[href*='{href}']"),
                    self.page.locator("a.btn-primary").filter(has_text=patron),
                    self.page.get_by_role("link", name=patron),
                ),
                total_ms=15_000,
            ):
                await self.esperar_pantalla_carga()
                if await self._seccion_cargada():
                    return
            await self._esperar(3_000)
        raise TargetUnavailableError(f"no se pudo abrir la seccion {seccion}", diagnostic_code="carga_csv_link_missing")

    async def _seccion_cargada(self) -> bool:
        for sel in ("#tablaTotalesContainer", "input[id^='montoGravado']", "#archivo", "#btnImportarArchivo"):
            try:
                if await self.page.locator(sel).count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def cargar_csv(self, seccion: str, csv_path: Path) -> None:
        """Importa el CSV de la seccion y guarda (V1 ``cargar_apertura_csv``)."""
        csv_path = Path(csv_path)
        if not csv_path.is_file():
            raise TargetUnavailableError("CSV de apertura ausente en work_dir", diagnostic_code="carga_input_missing")
        await self.abrir_seccion_csv(seccion)
        if not await self._clickear((self.page.locator("#btnImportarArchivo"),), total_ms=20_000):
            await self._clickear(
                (
                    self.page.locator("button.btn-link").filter(has_text=re.compile(r"Importar", re.I)),
                    self.page.get_by_role("button", name=re.compile(r"Importar.*CSV|Importar.*Archivo", re.I)),
                ),
                total_ms=5_000,
            )
        await self._esperar(2_000)
        await self.page.locator("input[type='file']").first.set_input_files(str(csv_path), timeout=60_000)
        await self._esperar(1_500)
        await self._clickear((self.page.locator("#btnImportarArchivoImportar"),), total_ms=5_000)
        await self.esperar_procesando()
        await self._clickear(
            (self.page.get_by_text(_PROCESADA), self.page.get_by_text(_PROCESANDO)),
            total_ms=5_000,
        )
        await self._esperar(2_000)
        if seccion == "CF":
            await self.completar_credito_fiscal()
        await self._guardar()
        await self.esperar_procesando()
        await self._clickear(
            (
                self.page.locator("a[href*='menuInfoAdicional']"),
                self.page.locator("#btnVolver"),
                self.page.get_by_text(re.compile(r"Volver", re.I)),
            ),
            total_ms=8_000,
        )

    async def _guardar(self) -> None:
        await self._clickear(
            (
                self.page.locator("button[type='submit']"),
                self.page.get_by_role("button", name=re.compile(r"Guardar", re.I)),
            ),
            total_ms=10_000,
        )
        await self._esperar(2_000)

    # ------------------------------------------------------------------
    # Credito fiscal prorrateado

    async def completar_credito_fiscal(self, valor: str | None = None) -> bool:
        """Rellena el modal de credito fiscal de prorrateo, si aparece (V1 ``_handle_credito_fiscal_modal``).

        Con ``valor`` lo escribe tal cual. Sin ``valor`` toma el ultimo numero con formato
        de moneda de la pagina, como V1. Devuelve ``False`` si no hay modal o no hay numero.
        """
        campo = self.page.locator("#credFiscProrrateo")
        if await campo.count() == 0:
            return False
        if valor is not None:
            await self._escribir_credito(campo.first, valor)
            return True
        try:
            texto = await self.page.locator("body").inner_text()
        except Exception:
            texto = ""
        numeros = re.findall(r"(?:\$?\s*)([\d]{1,3}(?:\.\d{3})*(?:,\d{2})?)", texto)
        if not numeros:
            numeros = re.findall(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)", texto)
        if not numeros:
            return False
        await self._escribir_credito(campo.first, numeros[-1].replace(".", "").replace(",", "."))
        return True

    async def _escribir_credito(self, campo, valor: str) -> None:
        """Escribe el credito computable y dispara el recalculo del global (como el V1)."""
        await campo.fill(valor)
        with contextlib.suppress(Exception):
            await campo.dispatch_event("change")
        with contextlib.suppress(Exception):
            await self.page.evaluate(
                "() => { if (typeof actualizarCredFiscalGlobal === 'function') actualizarCredFiscalGlobal(); }"
            )
        await self._esperar(1_500)
        with contextlib.suppress(Exception):
            log.warning("DIAG credito_fiscal valor=%s global=%s", valor, await self.page.locator("#credFiscGlobal").input_value())

    async def cargar_credito_fiscal_compras(self, total: str) -> bool:
        """Completa el credito fiscal computable global de compras con ``total`` (V1 ``_ir_a_editar_credito_fiscal_en_compras``).

        Intenta la URL directa; si no hay modal, busca el enlace en el libro de compras.
        Vuelve al menu de presentacion al terminar, para dejar la sesion consistente.
        """
        await self.volver_al_inicio()
        await self.page.goto(URL_CREDITO_FISCAL_COMPRAS, wait_until="domcontentloaded", timeout=15_000)
        await self.esperar_procesando()
        with contextlib.suppress(Exception):
            cuerpo = (await self.page.locator("body").inner_text(timeout=3_000)).replace("\n", " | ")[:1500]
            campos = await self.page.evaluate(
                "() => [...document.querySelectorAll('input')].map(e => e.id + '=' + e.value).join(', ')"
            )
            log.warning("DIAG cf_global_pagina csv_total=%s BODY=%s INPUTS=%s", total, cuerpo, campos)
        if await self.page.locator("#credFiscProrrateo").count() > 0:
            if await self.completar_credito_fiscal(total):
                await self._guardar()
                await self.esperar_procesando()
                return True
        await self.abrir_libro("compras")
        patron = re.compile(r"Crédito\s+Fiscal\s+Computable\s+Global", re.I)
        if not await self._clickear(
            (
                self.page.locator("a, button, span, div").filter(has_text=patron),
                self.page.get_by_role("link", name=patron),
                self.page.locator("a[href*='verComprasCreditoFiscalComputableGlobal']"),
            ),
            total_ms=8_000,
        ):
            return False
        await self._esperar(2_000)
        manejado = await self.completar_credito_fiscal(total)
        await self._guardar()
        await self.volver_al_inicio()
        return manejado
