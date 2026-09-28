"""Acceso de solo lectura a libros CSV de Portal IVA (port desde V1/V2).

Este portal contiene exclusivamente los pasos de selección de representado,
período y descarga. No implementa importación de TXT ni acciones de carga o
presentación de declaraciones.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

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
        await self._clickear(
            (
                self.page.get_by_title("Representar a..."),
                self.page.get_by_text("Representar a...", exact=False),
            ),
            total_ms=5_000,
        )
        await self._esperar(700)

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

    async def descargar_libro(self, libro: str, destino: Path) -> Path:
        """Abre el libro existente pedido y guarda su CSV en ``destino``."""
        book = str(libro or "").strip().lower()
        if book not in {"ventas", "compras"}:
            raise TargetUnavailableError(
                "tipo de libro desconocido para Portal IVA",
                diagnostic_code="portal_iva_book_unknown",
            )
        destino = Path(destino)
        href = "verVentas.do" if book == "ventas" else "verCompras.do"
        nombre = "Ventas" if book == "ventas" else "Compras"
        locators_csv = (
            self.page.get_by_role("button", name=re.compile(r"CSV", re.I)),
            self.page.get_by_role("link", name=re.compile(r"CSV", re.I)),
            self.page.get_by_text("CSV", exact=True),
        )
        if await self._primero_visible(locators_csv, total_ms=500) is None:
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

        async def descargar_csv() -> None:
            if not await self._clickear(locators_csv, total_ms=10_000):
                raise TargetUnavailableError(
                    f"no se encontró la descarga CSV del libro de {book}",
                    diagnostic_code="portal_iva_csv_button_missing",
                )

        await self.capturar_descarga(destino, descargar_csv, espera_ms=45_000)
        _extraer_csv_si_es_zip(destino)
        await self._volver_al_menu()
        return destino

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
