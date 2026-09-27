"""Portal SISTEMA DE CUENTAS (SCT): vencimientos, deudas y DDJJ pendientes.

Port de ``api-bots-mrbot/app/bot/sct_bot.py`` (V2). El plugin V3 pide
``descargar_reporte(seccion, formato, destino)`` por cada par solicitado.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

_IFRAME = "iframe"

# Rótulos de pestaña por sección (V2 usa los nombres del portal).
PESTANAS = {
    "vencimientos": ("Vencimientos",),
    "deudas": ("Deudas",),
    "ddjj_pendientes": ("DDJJ pendientes", "DDJJ"),
}

# Rótulos de la opción de exportación por formato.
FORMATOS = {
    "xlsx": ("XLS", "XLSX", "Excel"),
    "excel": ("XLS", "XLSX", "Excel"),
    "csv": ("CSV",),
    "pdf": ("PDF",),
}


class SctPortal(PortalArca):
    """Reportes del Sistema de Cuentas de ARCA."""

    nombre = "sct"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Espera la carga del portal y elige el representado (port de V2)."""
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await super().seleccionar_representado(cuit)

    async def preparar(self) -> None:
        """Espera el iframe del SCT (port del bloque previo de V2)."""
        try:
            marco = self.page.locator(_IFRAME)
            await marco.first.wait_for(state="visible", timeout=20_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se encontró el iframe del Sistema de Cuentas",
                diagnostic_code="sct_frame_missing",
            ) from exc

    async def descargar_reporte(self, seccion: str, formato: str, destino: Path) -> Path:
        """Descarga la sección en el formato pedido dentro de ``destino``."""
        seccion = str(seccion or "").strip().lower()
        formato = str(formato or "").strip().lower()
        rotulos = PESTANAS.get(seccion)
        if rotulos is None:
            raise TargetUnavailableError(
                f"sección desconocida en SCT: {seccion}",
                diagnostic_code="sct_section_unknown",
            )
        opciones = FORMATOS.get(formato)
        if opciones is None:
            raise TargetUnavailableError(
                f"formato desconocido en SCT: {formato}",
                diagnostic_code="sct_format_unknown",
            )
        await self._abrir_seccion(rotulos)
        exportar = await self._boton_exportar()
        if exportar is None:
            raise TargetUnavailableError(
                "el portal SCT no mostró el botón Exportar",
                diagnostic_code="sct_export_missing",
            )
        return await self.capturar_descarga(
            Path(destino),
            lambda: self._elegir_formato(exportar, opciones),
            espera_ms=30_000,
        )

    # ------------------------------------------------------------------

    async def _abrir_seccion(self, rotulos: tuple[str, ...]) -> None:
        if not await self.seleccionar_pestana(rotulos):
            raise TargetUnavailableError(
                "no se pudo abrir la sección pedida en SCT",
                diagnostic_code="sct_section_open_failed",
            )
        await self._esperar(2_000)

    async def _boton_exportar(self) -> Any | None:
        marco = self.page.frame_locator(_IFRAME)
        candidatos = (
            marco.get_by_role("button", name=re.compile("Exportar", re.I)),
            marco.get_by_role("link", name=re.compile("Exportar", re.I)),
            marco.locator("div.buttons.bt-imp"),
            marco.locator("button:has-text('Exportar')"),
            marco.locator("a:has-text('Exportar')"),
        )
        return await self._primero_visible(candidatos, total_ms=8_000)

    async def _elegir_formato(self, exportar: Any, opciones: tuple[str, ...]) -> None:
        """Abre el menú de exportación y elige el formato pedido."""
        marco = self.page.frame_locator(_IFRAME)
        for opcion in opciones:
            patron = re.compile(rf"\b{re.escape(opcion)}\b", re.I)
            candidatos = (
                marco.get_by_role("button", name=patron),
                marco.get_by_role("link", name=patron),
            )
            elegido = await self._primero_visible(candidatos, total_ms=1_200)
            if elegido is None:
                try:
                    await exportar.click(timeout=3_000)
                except Exception:
                    pass
                await self._esperar(400)
                elegido = await self._primero_visible(candidatos, total_ms=2_000)
            if elegido is None:
                continue
            await elegido.click(timeout=5_000)
            return
        raise TargetUnavailableError(
            "el portal SCT no ofreció el formato pedido",
            diagnostic_code="sct_format_unavailable",
        )
