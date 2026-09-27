"""Consulta de compensaciones y afectaciones del Sistema de Cuentas (V2)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

URL_COMPENSACIONES = (
    "https://ctacte.cloud.afip.gob.ar/contribuyente/scripts/vue/"
    "consultaCompensacionesAfectaciones/index.html"
)


class CompensacionesPortal(PortalArca):
    """Acciones de consulta/exportación en el submódulo de compensaciones."""

    nombre = "compensaciones"

    async def consultar(self, desde: str, hasta: str) -> str:
        """Abre la pantalla, aplica el rango y consulta sin iniciar trámites."""
        return await self.paso(
            "consultar", self._consultar(str(desde), str(hasta))
        )

    async def exportar(self, formato: str, destino: Path) -> Path:
        """Exporta una consulta ya ejecutada en XLS, CSV o PDF."""
        formato = str(formato or "").strip().upper()
        if formato not in {"XLS", "CSV", "PDF"}:
            raise TargetUnavailableError(
                "formato de exportación no soportado",
                diagnostic_code="compensaciones_format_unknown",
            )
        return await self.paso(
            "exportar",
            self.capturar_descarga(
                Path(destino),
                lambda: self._elegir_formato(formato),
                espera_ms=30_000,
            ),
        )

    async def _consultar(self, desde: str, hasta: str) -> str:
        await self.page.goto(URL_COMPENSACIONES, wait_until="domcontentloaded")
        try:
            await self.page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass

        await self._esperar_filtros()
        campos = self.page.locator("input[name='fecha']")
        if await campos.count() >= 2:
            campo_desde, campo_hasta = campos.nth(0), campos.nth(1)
        else:
            campo_desde = await self._primero_visible(
                (
                    self.page.get_by_role(
                        "textbox", name=re.compile(r"Fecha\s+Desde", re.I)
                    ),
                    self.page.locator(
                        "input[placeholder*='Desde' i], input[name*='desde' i], "
                        "input[id*='desde' i]"
                    ),
                ),
                total_ms=3_000,
            )
            campo_hasta = await self._primero_visible(
                (
                    self.page.get_by_role(
                        "textbox", name=re.compile(r"Fecha\s+Hasta", re.I)
                    ),
                    self.page.locator(
                        "input[placeholder*='Hasta' i], input[name*='hasta' i], "
                        "input[id*='hasta' i]"
                    ),
                ),
                total_ms=3_000,
            )
            if campo_desde is None or campo_hasta is None:
                raise TargetUnavailableError(
                    "no se encontraron los campos de fecha en Compensaciones",
                    diagnostic_code="compensaciones_date_fields_missing",
                )

        await campo_desde.click()
        await campo_desde.fill(desde)
        await campo_hasta.click()
        await campo_hasta.fill(hasta)

        consultar = await self._primero_visible(
            (
                self.page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I)),
                self.page.get_by_role("link", name=re.compile(r"CONSULTAR", re.I)),
                self.page.locator("button:has-text('CONSULTAR')"),
                self.page.locator("a:has-text('CONSULTAR')"),
            ),
            total_ms=3_000,
        )
        if consultar is None:
            raise TargetUnavailableError(
                "no se encontró el botón CONSULTAR en Compensaciones",
                diagnostic_code="compensaciones_consult_button_missing",
            )
        await consultar.click()
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        await self._esperar(1_200)
        return "sin_resultados" if await self._sin_resultados() else "con_resultados"

    async def _esperar_filtros(self, timeout_ms: int = 35_000) -> None:
        """Espera la SPA como en V2 antes de usar los controles del submódulo."""
        limite = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < limite:
            try:
                fechas = self.page.locator("input[name='fecha']")
                if await fechas.count() >= 2 and await fechas.first.is_visible(
                    timeout=300
                ):
                    return
            except Exception:
                pass
            try:
                consultar = self.page.get_by_role(
                    "button", name=re.compile(r"CONSULTAR", re.I)
                )
                if await consultar.count() and await consultar.first.is_visible(
                    timeout=300
                ):
                    return
            except Exception:
                pass
            await self._esperar(400)
        raise TargetUnavailableError(
            "no se cargó la pantalla de Consulta Compensaciones y Afectaciones",
            diagnostic_code="compensaciones_screen_not_ready",
        )

    async def _sin_resultados(self) -> bool:
        indicadores = (
            self.page.locator(
                "div.d-empty h4",
                has_text=re.compile(r"No\s+se\s+encontraron\s+resultados", re.I),
            ),
            self.page.get_by_role(
                "heading", name=re.compile(r"No\s+se\s+encontraron\s+resultados", re.I)
            ),
            self.page.get_by_text(
                re.compile(r"No\s+se\s+encontraron\s+resultados", re.I)
            ),
        )
        limite = asyncio.get_running_loop().time() + 1.2
        while asyncio.get_running_loop().time() < limite:
            for indicador in indicadores:
                try:
                    if await indicador.count() and await indicador.first.is_visible(
                        timeout=600
                    ):
                        return True
                except Exception:
                    continue
            await asyncio.sleep(0.2)
        return False

    async def _elegir_formato(self, formato: str) -> None:
        exportar = await self._primero_visible(
            (
                self.page.get_by_role("button", name=re.compile(r"Exportar", re.I)),
                self.page.get_by_role("link", name=re.compile(r"Exportar", re.I)),
                self.page.locator("button:has-text('Exportar')"),
                self.page.locator("a:has-text('Exportar')"),
                self.page.locator("div.buttons.bt-imp"),
            ),
            total_ms=1_800,
        )
        if exportar is None:
            raise TargetUnavailableError(
                "el portal no mostró el botón Exportar",
                diagnostic_code="compensaciones_export_missing",
            )

        patron = re.compile(rf"\b{re.escape(formato)}\b", re.I)
        opciones = (
            self.page.get_by_role("button", name=patron),
            self.page.get_by_role("link", name=patron),
            self.page.locator(f"button:has-text('{formato}')"),
            self.page.locator(f"a:has-text('{formato}')"),
            self.page.get_by_text(patron),
        )
        opcion = await self._primero_visible(opciones, total_ms=1_200)
        if opcion is None:
            await exportar.click()
            await self._esperar(350)
            opciones = (
                self.page.get_by_role("button", name=patron),
                self.page.get_by_role("link", name=patron),
                self.page.locator(f"button:has-text('{formato}')"),
                self.page.locator(f"a:has-text('{formato}')"),
                self.page.get_by_text(patron),
            )
            opcion = await self._primero_visible(opciones, total_ms=2_000)
        if opcion is None:
            raise TargetUnavailableError(
                f"el portal no ofreció el formato {formato}",
                diagnostic_code="compensaciones_format_unavailable",
            )
        await opcion.click()
