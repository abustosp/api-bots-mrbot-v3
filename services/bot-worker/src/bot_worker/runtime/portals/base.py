"""Base común de los servicios de portal portados de V1/V2.

Reúne los helpers que los bots V2 repetían: esperar y clickear por lista de
selectores, leer tablas, capturar una descarga, resolver reCAPTCHA y
seleccionar el CUIT representado. Las subclases implementan solo las
acciones propias de su portal.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from bot_worker.bots.errors import (
    CaptchaUnsolvableError,
    TargetUnavailableError,
)

# Extracción de tablas (port de `_TABLE_EXTRACT_JS` de los bots V2).
TABLAS_JS = """
() => {
    const tables = document.querySelectorAll('table');
    const result = [];
    tables.forEach((table, idx) => {
        const captionEl = table.querySelector('caption');
        let title = captionEl ? captionEl.innerText.trim() : '';
        if (!title) {
            const prev = table.previousElementSibling;
            if (prev) title = prev.innerText.trim().split('\\n')[0] || '';
        }
        if (!title) title = 'tabla_' + (idx + 1);
        const headers = [];
        const headerCells = table.querySelectorAll('thead th, thead td, tr:first-child th');
        headerCells.forEach(th => headers.push(th.innerText.trim().replace(/\\s+/g, ' ')));
        const rows = [];
        const bodyRows = headers.length
            ? table.querySelectorAll('tbody tr')
            : table.querySelectorAll('tr');
        bodyRows.forEach((tr, trIdx) => {
            if (!headers.length && trIdx === 0) return;
            const cells = [];
            tr.querySelectorAll('td, th').forEach(td => {
                cells.push(td.innerText.trim().replace(/\\s+/g, ' '));
            });
            if (cells.some(c => c !== '')) rows.push(cells);
        });
        if (rows.length > 0 || headers.length > 0) result.push({ title, headers, rows });
    });
    return result;
}
"""

_ESPACIOS = re.compile(r"\s+")


def solo_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


class PortalArca:
    """Acciones compartidas de un portal del organismo.

    ``page`` es la pestaña del servicio (popup o pestaña actual). La sesión ya
    está logueada y con el servicio abierto cuando se instancia.
    """

    nombre = "portal"
    #: milisegundos de espera máxima por cada selector candidato
    timeout_selector_ms = 3_000

    def __init__(
        self,
        page: Any,
        *,
        captcha: Any = None,
        context: Any = None,
        service_name: str = "",
    ) -> None:
        self._page = page
        self._captcha = captcha
        self._context = context
        self._service_name = service_name

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    @property
    def page(self) -> Any:
        if self._page is None:
            raise TargetUnavailableError(
                "página del portal no disponible",
                diagnostic_code="portal_page_missing",
            )
        return self._page

    # ------------------------------------------------------------------
    # Helpers de interacción

    async def _primero_visible(
        self, localizadores: Sequence[Any], *, total_ms: int = 8_000
    ) -> Any | None:
        """Primer localizador con elementos y visible, esperando hasta ``total_ms``."""
        limite = asyncio.get_running_loop().time() + total_ms / 1000
        while True:
            for candidato in localizadores:
                try:
                    if await candidato.count() == 0:
                        continue
                    if await candidato.first.is_visible(timeout=300):
                        return candidato.first
                except Exception:
                    continue
            if asyncio.get_running_loop().time() >= limite:
                return None
            await asyncio.sleep(0.3)

    async def _clickear(self, localizadores: Sequence[Any], *, total_ms: int = 8_000) -> bool:
        objetivo = await self._primero_visible(localizadores, total_ms=total_ms)
        if objetivo is None:
            return False
        try:
            await objetivo.scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        try:
            await objetivo.click(timeout=self.timeout_selector_ms)
            return True
        except Exception:
            try:
                handle = await objetivo.element_handle()
                if handle is not None:
                    await handle.evaluate("el => el.click()")
                    return True
            except Exception:
                pass
        return False

    async def _texto(self, localizador: Any) -> str:
        try:
            return _ESPACIOS.sub(" ", (await localizador.inner_text(timeout=3_000) or "")).strip()
        except Exception:
            return ""

    async def _esperar(self, ms: int = 1_000) -> None:
        with contextlib.suppress(Exception):
            await self.page.wait_for_timeout(ms)

    async def paso(self, nombre: str, coro: Any) -> Any:
        """Ejecuta un paso del portal y etiqueta el fallo con su nombre.

        El diagnóstico queda como ``portal_step_<nombre>`` y la causa original
        viaja encadenada (``cause``), así el operador ve en qué paso falló sin
        exponer texto del sitio.
        """
        from bot_worker.bots.errors import ErrorDeBot

        try:
            return await coro
        except ErrorDeBot:
            raise
        except Exception as exc:
            limpio = re.sub(r"[^a-z0-9_]", "_", str(nombre).lower())[:48]
            raise TargetUnavailableError(
                f"falló el paso {limpio} del portal",
                diagnostic_code=f"portal_step_{limpio}",
            ) from exc

    async def _url(self) -> str:
        return str(getattr(self.page, "url", "") or "")

    async def tablas(self) -> list[dict[str, Any]]:
        """Tablas del documento actual (port de ``_TABLE_EXTRACT_JS``)."""
        try:
            datos = await self.page.evaluate(TABLAS_JS)
        except Exception:
            return []
        return [t for t in (datos or []) if isinstance(t, dict)]

    async def tablas_del_frame(self, selector_frame: str) -> list[dict[str, Any]]:
        """Tablas dentro de un iframe, con respaldo en la página principal."""
        try:
            frame = self.page.frame_locator(selector_frame)
            datos = await frame.locator("body").evaluate(TABLAS_JS)
            if datos:
                return [t for t in datos if isinstance(t, dict)]
        except Exception:
            pass
        return await self.tablas()

    async def capturar_descarga(self, destino: Path, accion: Any, *, espera_ms: int = 60_000) -> Path:
        """Ejecuta ``accion`` esperando una descarga y la guarda en ``destino``."""
        destino = Path(destino)
        try:
            async with self.page.expect_download(timeout=espera_ms) as info:
                await accion()
            descarga = await info.value
            await descarga.save_as(str(destino))
        except Exception as exc:
            detalle = str(exc) if isinstance(exc, TargetUnavailableError) else type(exc).__name__
            raise TargetUnavailableError(
                f"el portal no entregó la descarga esperada ({detalle[:120]})",
                diagnostic_code="portal_download_missing",
            ) from exc
        if not destino.exists() or destino.stat().st_size == 0:
            raise TargetUnavailableError(
                "la descarga del portal llegó vacía",
                diagnostic_code="portal_download_empty",
            )
        return destino

    async def resolver_recaptcha(self, *, timeout_s: int = 120) -> bool:
        """Detecta y resuelve un reCAPTCHA v2 con el proveedor configurado.

        Devuelve ``True`` si no había captcha o si se inyectó el token.
        """
        from .recaptcha import detectar_sitekey, inyectar_token, resolver

        sitekey = await detectar_sitekey(self.page)
        if not sitekey:
            return True
        if self._captcha is None or not getattr(self._captcha, "enabled", False):
            raise CaptchaUnsolvableError("captcha presente y sin proveedor configurado")
        token = await resolver(self._captcha, sitekey=sitekey, url=await self._url(), timeout_s=timeout_s)
        await inyectar_token(self.page, token)
        return True

    # ------------------------------------------------------------------
    # Selección de representado (port genérico de los bots V2)

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona el CUIT representado en el portal abierto.

        Port de los selectores usados por los bots V2: combo
        ``$PropertySelection`` de los portales AFIP clásicos, listas de
        opciones y, por último, el nombre activo de Mis Comprobantes.
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        formateado = f"{digits[:2]}-{digits[2:10]}-{digits[10]}"

        if await self._seleccionar_en_combo(digits):
            return
        if await self._seleccionar_en_lista(digits, formateado):
            return
        if await self._ya_esta_seleccionado(digits):
            return
        # Mapa de selectores (nombre propio y cantidad encontrada): orienta al
        # operador sin exponer texto del sitio.
        mapa = await self._mapa_de_selectores()
        raise TargetUnavailableError(
            f"no se pudo seleccionar el CUIT representado ({mapa})",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def _mapa_de_selectores(self) -> str:
        partes: list[str] = []
        for selector in (
            'select[name="$PropertySelection"]',
            "select[id*='representado' i]",
            "select[id*='contribuyente' i]",
            "span.nombre-propio.nombre-activo.pull-right",
            "iframe",
            "table",
        ):
            try:
                partes.append(f"{selector}={await self.page.locator(selector).count()}")
            except Exception:
                partes.append(f"{selector}=?")
        return " ".join(partes)

    async def _seleccionar_en_combo(self, digits: str) -> bool:
        for selector in (
            'select[name="$PropertySelection"]',
            "select[id*='representado' i]",
            "select[name*='representado' i]",
            "select[id*='contribuyente' i]",
        ):
            try:
                combo = self.page.locator(selector)
                if await combo.count() == 0:
                    continue
                try:
                    await combo.first.wait_for(state="visible", timeout=3_000)
                except Exception:
                    continue
                opciones = combo.first.locator("option")
                for indice in range(await opciones.count()):
                    opcion = opciones.nth(indice)
                    texto = str(await opcion.text_content() or "").strip()
                    valor = str(await opcion.get_attribute("value") or "").strip()
                    if digits not in {solo_digitos(texto), solo_digitos(valor)} and texto != digits:
                        continue
                    if valor:
                        await combo.first.select_option(value=valor)
                    else:
                        await combo.first.select_option(label=texto)
                    await self._esperar(700)
                    return True
            except Exception:
                continue
        return False

    async def _seleccionar_en_lista(self, digits: str, formateado: str) -> bool:
        localizadores = (
            self.page.get_by_role("link", name=re.compile(re.escape(formateado))),
            self.page.get_by_role("button", name=re.compile(re.escape(formateado))),
            self.page.locator(f"small.pull-right:has-text('{formateado}')"),
            self.page.locator(f"a:has-text('{formateado}')"),
            self.page.locator(f"li:has-text('{formateado}')"),
            self.page.locator(f"span:has-text('{formateado}')"),
        )
        if await self._clickear(localizadores, total_ms=6_000):
            await self._esperar(1_000)
            return True
        # Algunos portales listan solo los últimos dígitos en el texto.
        sufijo = digits[-8:]
        if await self._clickear(
            (
                self.page.locator(f"a:has-text('{sufijo}')"),
                self.page.locator(f"li:has-text('{sufijo}')"),
            ),
            total_ms=3_000,
        ):
            await self._esperar(1_000)
            return True
        return False

    async def _ya_esta_seleccionado(self, digits: str) -> bool:
        for selector in (
            "span.nombre-propio.nombre-activo.pull-right",
            "span.nombre-propio.nombre-activo.pull-righ",
            "[id*='representado' i]",
        ):
            try:
                activo = self.page.locator(selector)
                if not await activo.count():
                    continue
                texto = await self._texto(activo.first)
                if texto and solo_digitos(texto).endswith(digits):
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Utilidades de navegación

    async def abrir_url(self, url: str, *, timeout_ms: int = 30_000) -> None:
        await self.page.goto(url, timeout=timeout_ms)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)

    async def seleccionar_pestana(self, nombres: Iterable[str]) -> bool:
        """Click en una pestaña o link por nombre (port de ``_find_tab_locator``)."""
        return await self._clickear(self._pestanas_de(self.page, nombres), total_ms=8_000)

    async def seleccionar_pestana_en_marco(
        self, frame_selector: str, nombres: Iterable[str]
    ) -> bool:
        """Igual que ``seleccionar_pestana`` pero dentro de un iframe.

        Los portales AFIP clásicos (SCT) dibujan las secciones dentro del
        iframe; se busca ahí primero y, si no aparece, en la página.
        """
        try:
            marco = self.page.frame_locator(frame_selector)
            if await self._clickear(self._pestanas_de(marco, nombres), total_ms=8_000):
                return True
        except Exception:
            pass
        return await self.seleccionar_pestana(nombres)

    @staticmethod
    def _pestanas_de(ambito: Any, nombres: Iterable[str]) -> tuple[Any, ...]:
        localizadores: list[Any] = []
        for nombre in nombres:
            patron = re.compile(nombre, re.I)
            localizadores.extend(
                (
                    ambito.get_by_role("tab", name=patron),
                    ambito.get_by_role("link", name=patron),
                    ambito.locator("a", has_text=patron),
                    ambito.locator("button", has_text=patron),
                    ambito.locator("li", has_text=patron),
                )
            )
        return tuple(localizadores)
