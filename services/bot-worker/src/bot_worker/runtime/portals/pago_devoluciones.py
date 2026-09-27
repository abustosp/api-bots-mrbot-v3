"""Portal ARCA para pagos de devoluciones y exportación Excel.

Porta ``consultar_y_exportar`` de
``api-bots-mrbot/app/bot/pago_devoluciones_bot.py`` al runtime V3. La
selección del representado se hace desde el plugin con el helper común de
``PortalArca`` antes de iniciar la consulta.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos


class PagoDevolucionesPortal(PortalArca):
    """Consulta pagos y guarda la exportación del portal en ``destino``."""

    nombre = "pago_devoluciones"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona el CUIT con el combo específico usado por V1/V2."""
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        selector = self.page.locator(
            "#ctl00_ddlExtranetEmpresa, select[name='ctl00$ddlExtranetEmpresa']"
        )
        try:
            if await selector.count() > 0:
                combo = selector.first
                with contextlib.suppress(Exception):
                    await combo.wait_for(state="attached", timeout=10_000)
                values = await combo.evaluate(
                    "el => Array.from(el.options || []).map(opt => (opt.value || '').trim())"
                )
                if digits not in values:
                    raise TargetUnavailableError(
                        "el representado no está disponible en Pago Devoluciones",
                        diagnostic_code="represented_cuit_not_selectable",
                    )
                actual = solo_digitos(await combo.input_value())
                if actual != digits:
                    await combo.select_option(value=digits)
                    await self._esperar(700)
                    actual = solo_digitos(await combo.input_value())
                if actual != digits:
                    await self.page.evaluate(
                        """({selector, value}) => {
                            const select = document.querySelector(selector);
                            if (!select) return false;
                            select.value = value;
                            select.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        }""",
                        {"selector": "#ctl00_ddlExtranetEmpresa", "value": digits},
                    )
                    await self._esperar(700)
                    actual = solo_digitos(await combo.input_value())
                if actual != digits:
                    raise TargetUnavailableError(
                        "no se pudo seleccionar el representado en Pago Devoluciones",
                        diagnostic_code="represented_cuit_not_selectable",
                    )
                return
        except TargetUnavailableError:
            raise
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo seleccionar el representado en Pago Devoluciones",
                diagnostic_code="represented_cuit_not_selectable",
            ) from exc
        await super().seleccionar_representado(cuit)

    async def consultar_y_exportar(
        self, cuit_representado: str, destino: Path
    ) -> Path:
        """Abre Consultar, exporta Excel y guarda la descarga solicitada."""
        if len(solo_digitos(cuit_representado)) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido para Pago Devoluciones",
                diagnostic_code="pago_devoluciones_cuit_invalid",
            )
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        await self.paso("abrir_consulta", self._abrir_consulta())
        exportar = await self._boton_exportar()
        return await self.paso(
            "exportar",
            self.capturar_descarga(
                destino,
                lambda: self._disparar_exportacion(exportar),
                espera_ms=45_000,
            ),
        )

    async def _abrir_consulta(self) -> None:
        consultar = await self._primero_visible(
            (
                self.page.get_by_role("link", name=re.compile(r"Consultar", re.I)),
                self.page.get_by_role("button", name=re.compile(r"Consultar", re.I)),
                self.page.locator("a:has-text('Consultar')"),
            ),
            total_ms=5_000,
        )
        if consultar is None:
            raise TargetUnavailableError(
                "el portal Pago Devoluciones no mostró Consultar",
                diagnostic_code="pago_devoluciones_consultar_missing",
            )
        await consultar.click(timeout=10_000)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("networkidle", timeout=12_000)

    async def _boton_exportar(self) -> Any:
        exportar = await self._primero_visible(
            (
                self.page.get_by_role("link", name=re.compile(r"Exportar", re.I)),
                self.page.get_by_role("button", name=re.compile(r"Exportar", re.I)),
                self.page.locator("a:has-text('Exportar')"),
                self.page.locator("button:has-text('Exportar')"),
            ),
            total_ms=5_000,
        )
        if exportar is None:
            raise TargetUnavailableError(
                "el portal Pago Devoluciones no mostró Exportar",
                diagnostic_code="pago_devoluciones_export_missing",
            )
        return exportar

    async def _disparar_exportacion(self, exportar: Any) -> None:
        await exportar.click(timeout=10_000)

        # En algunas versiones del servicio Exportar abre una confirmación;
        # en otras dispara directamente la descarga.
        aceptar = await self._primero_visible(
            (
                self.page.get_by_role("button", name=re.compile(r"Aceptar", re.I)),
                self.page.get_by_role("link", name=re.compile(r"Aceptar", re.I)),
                self.page.locator("button:has-text('Aceptar')"),
            ),
            total_ms=4_000,
        )
        if aceptar is not None:
            await aceptar.click(timeout=5_000)
