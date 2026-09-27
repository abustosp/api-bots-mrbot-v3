"""Portal SETI: consulta de pagos VEP y exportación a CSV.

Port de ``api-bots-mrbot/app/bot/consulta_pagos_vep_bot.py`` (V2). El plugin V3
pide ``exportar_pagos_csv(periodo, destino)`` después de elegir el representado.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

URL_VEPS = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
_JS_LIMPIAR_OVERLAYS = """
() => {
    document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
    document.querySelectorAll('.modal.show').forEach((el) => el.classList.remove('show'));
    return true;
}
"""


class ConsultaPagosVepPortal(PortalArca):
    """Consulta de VEPs presentados y su exportación."""

    nombre = "consulta_pagos_vep"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._preparado = False

    async def preparar(self) -> None:
        """Abre la SPA de consulta de VEPs dentro de la sesión del servicio."""
        if self._preparado:
            return
        await self._limpiar_overlays()
        await self.paso("abrir_spa", self._abrir_spa())
        self._preparado = True

    async def _abrir_spa(self) -> None:
        await self.abrir_url(URL_VEPS, timeout_ms=45_000)
        for _ in range(3):
            await self._esperar(1_500)
            try:
                listo = await self.page.locator("#app").count() and (
                    await self.page.locator("input#cuitcontribuyente.multiselect-search").count()
                )
            except Exception:
                listo = 0
            if listo:
                return
            try:
                await self.page.reload(wait_until="domcontentloaded", timeout=20_000)
            except Exception:
                pass

    async def _limpiar_overlays(self) -> None:
        try:
            await self.page.evaluate(_JS_LIMPIAR_OVERLAYS)
        except Exception:
            pass
        await self._esperar(300)

    # ------------------------------------------------------------------

    async def seleccionar_representado(self, cuit: str) -> None:
        """Elige el CUIT del contribuyente en el multiselect de la SPA."""
        await self.preparar()
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        for _ in range(3):
            await self._limpiar_overlays()
            if not await self._abrir_selector():
                continue
            if await self._click_opcion(digits):
                return
        raise TargetUnavailableError(
            "no se pudo seleccionar el CUIT representado en VEP",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def _abrir_selector(self) -> bool:
        if await self._panel_visible():
            return True
        disparadores = (
            self.page.locator("input#cuitcontribuyente.multiselect-search"),
            self.page.get_by_role(
                "combobox", name=re.compile(r"Cuit del Contribuyente", re.I)
            ),
        )
        for disparador in disparadores:
            try:
                await disparador.first.wait_for(state="visible", timeout=8_000)
            except Exception:
                continue
            for _ in range(2):
                try:
                    await disparador.first.click(timeout=4_000)
                except Exception:
                    pass
                for _ in range(12):
                    await self._esperar(400)
                    if await self._panel_visible():
                        return True
        return False

    async def _panel_visible(self) -> bool:
        try:
            opciones = self.page.locator("[id^='cuitcontribuyente-multiselect-option']")
            return bool(await opciones.count())
        except Exception:
            return False

    async def _click_opcion(self, digits: str) -> bool:
        opcion = self.page.locator(f"#cuitcontribuyente-multiselect-option-{digits}")
        if await opcion.count() == 0:
            candidatos = self.page.locator("[id^='cuitcontribuyente-multiselect-option']")
            for indice in range(await candidatos.count()):
                candidato = candidatos.nth(indice)
                texto = solo_digitos(await candidato.text_content() or "")
                valor = solo_digitos(await candidato.get_attribute("value") or "")
                if digits in {texto, valor}:
                    opcion = candidato
                    break
        if await opcion.count() == 0:
            return False
        try:
            elemento_id = await opcion.first.get_attribute("id")
            if elemento_id:
                await self.page.evaluate(
                    """(id) => {
                        const el = document.getElementById(id);
                        if (!el) return false;
                        ['mousedown', 'mouseup', 'click'].forEach(t =>
                            el.dispatchEvent(new MouseEvent(t, { bubbles: true })));
                        return true;
                    }""",
                    elemento_id,
                )
            else:
                await opcion.first.click(force=True, timeout=4_000)
        except Exception:
            return False
        await self._esperar(700)
        return True

    # ------------------------------------------------------------------

    async def exportar_pagos_csv(self, destino: Path, periodo: str = "72") -> Path:
        """Selecciona el período y descarga el CSV de pagos."""
        await self.preparar()
        await self.paso("seleccionar_periodo", self._seleccionar_periodo(periodo))
        # V2 aplicaba el filtro antes de exportar; sin esto el portal no
        # muestra el botón de descarga.
        await self.paso("aplicar_filtro", self._aplicar_filtro())
        return await self.paso(
            "exportar_csv",
            self.capturar_descarga(Path(destino), self._disparar_descarga, espera_ms=60_000),
        )

    async def _aplicar_filtro(self) -> None:
        aplicar = self.page.get_by_role("button", name=re.compile(r"^Aplicar$", re.I))
        if await aplicar.count() == 0:
            return
        try:
            await aplicar.first.click(timeout=5_000)
        except Exception:
            await aplicar.first.click(timeout=5_000, force=True)
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        await self._esperar(1_500)

    async def _seleccionar_periodo(self, periodo: str) -> None:
        pedido = str(periodo or "72")
        combo = self.page.get_by_role("combobox", name=re.compile(r"^\d+$")).last
        if await combo.count() == 0:
            return
        try:
            await combo.click(timeout=5_000)
        except Exception:
            return
        opcion = self.page.get_by_role("option", name=re.compile(rf"^{re.escape(pedido)}$", re.I))
        if await opcion.count() == 0:
            opcion = self.page.get_by_text(pedido, exact=True)
        if await opcion.count():
            try:
                await opcion.first.click(timeout=4_000)
            except Exception:
                pass
        await self._esperar(800)

    async def _disparar_descarga(self) -> None:
        boton = await self._primero_visible(
            (
                self.page.get_by_role("button", name=re.compile(r"download|descargar", re.I)),
                self.page.locator(
                    "button[title*='descarg' i], button[aria-label*='descarg' i],"
                    " [role='button'][title*='download' i]"
                ),
            ),
            total_ms=15_000,
        )
        if boton is None:
            raise TargetUnavailableError(
                "el portal VEP no mostró el botón de descarga",
                diagnostic_code="vep_download_missing",
            )
        try:
            await boton.click(timeout=5_000)
        except Exception:
            await boton.click(timeout=5_000, force=True)
        opcion = self.page.get_by_title("Exportar a CSV")
        if await opcion.count() == 0:
            opcion = self.page.locator("[title*='Exportar a CSV' i]")
        if await opcion.count() == 0:
            raise TargetUnavailableError(
                "el portal VEP no ofreció 'Exportar a CSV'",
                diagnostic_code="vep_csv_option_missing",
            )
        try:
            await opcion.first.click(timeout=5_000)
        except Exception:
            await opcion.first.click(timeout=5_000, force=True)
