"""Portal de Mis Comprobantes: flujo "Historial de consultas" (port de V1).

Porta ``descarga_csv_consulta`` de ``api-bots-mrbot/app/bot/comprobantes_bot.py``.
La operacion ``historial`` no genera una consulta nueva: recorre las consultas ya
hechas de un tipo (emitidos o recibidos), localiza la del rango pedido y descarga
su CSV. El resto de las acciones de Mis Comprobantes sigue en ``ArcaServicePage``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.planillas import TIMEOUT_MC_MS_POR_DEFECTO

from .base import PortalArca

SECCIONES = {
    "emitidos": "Comprobantes Emitidos",
    "recibidos": "Comprobantes Recibidos",
}
# Mismos selectores, en el mismo orden, que prueba el bot V1 para las filas.
SELECTORES_FILAS = (
    "table tbody tr",
    "tbody tr",
    "tr:has-text('Fecha:')",
    ".table tbody tr",
)
PATRON_FECHA = re.compile(r"Fecha:\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})")
_ESPACIOS = re.compile(r"\s+")


def clasificar_estado(texto: str) -> str:
    """Estado de una fila del historial, con el mismo orden de prueba que V1."""
    bajo = texto.lower()
    if "finalizada" in bajo:
        return "Finalizada"
    if "en proceso" in bajo:
        return "En proceso"
    if "pendiente" in bajo:
        return "Pendiente"
    if "error" in bajo:
        return "Error"
    return "Desconocido"


def consultas_desde_filas(filas: Iterable[tuple[str, int]]) -> list[dict[str, Any]]:
    """Resumen de cada consulta: rango, estado y si tiene boton de exportar."""
    consultas: list[dict[str, Any]] = []
    for indice, (texto, botones) in enumerate(filas):
        info: dict[str, Any] = {
            "indice": indice,
            "estado": clasificar_estado(texto),
            "exportable": botones > 0,
            # V1 devuelve el texto completo de la fila en el resultado.
            "texto_completo": texto.strip(),
        }
        coincidencia = PATRON_FECHA.search(_ESPACIOS.sub(" ", texto))
        if coincidencia:
            info["fecha_desde"], info["fecha_hasta"] = coincidencia.groups()
        consultas.append(info)
    return consultas


def indice_consulta(textos: Iterable[str], desde: str, hasta: str) -> int | None:
    """Primera fila cuyo rango ``desde``/``hasta`` coincide por completo."""
    for indice, texto in enumerate(textos):
        coincidencia = PATRON_FECHA.search(_ESPACIOS.sub(" ", texto))
        if coincidencia and coincidencia.groups() == (desde, hasta):
            return indice
    return None


class ComprobantesPortal(PortalArca):
    """Historial de consultas de Mis Comprobantes (V1 ``descarga_csv_consulta``)."""

    nombre = "comprobantes"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Mismo criterio que el adaptador heredado de Mis Comprobantes."""
        from bot_worker.runtime.arca_login import ArcaServicePage

        await ArcaServicePage(self.page).seleccionar_representado(cuit)

    async def _red_quieta(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            # ARCA mantiene conexiones de telemetria abiertas; la pantalla manda.
            pass

    async def _filas(self) -> Any | None:
        for selector in SELECTORES_FILAS:
            filas = self.page.locator(selector)
            if await filas.count() > 0:
                return filas
        return None

    async def historial(
        self,
        tipo: str,
        desde: str,
        hasta: str,
        destino: Path,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Lista las consultas del tipo y descarga el CSV del rango pedido.

        Devuelve ``{"consultas": [...], "descargado": bool}``. Si no hay una
        consulta con ese rango, no descarga nada y no falla: el plugin decide.
        ``timeout_ms`` configura la espera del enlace ``CSV`` (V1 ``timeout_mc``).
        """
        seccion = SECCIONES.get(tipo)
        if seccion is None:
            raise TargetUnavailableError(
                "tipo de comprobante desconocido para el historial",
                diagnostic_code="comprobantes_type_unknown",
            )
        page = self.page
        await self._red_quieta()
        if not await self._clickear((page.get_by_text(seccion, exact=True),), total_ms=15_000):
            raise TargetUnavailableError(
                "no se encontro la seccion de comprobantes en Mis Comprobantes",
                diagnostic_code="comprobantes_section_missing",
            )
        await self._red_quieta()
        if not await self._clickear((page.get_by_role("link", name="Historial"),), total_ms=10_000):
            raise TargetUnavailableError(
                "no se encontro el enlace Historial de consultas",
                diagnostic_code="comprobantes_historial_link_missing",
            )
        await self._red_quieta()
        await self._esperar(2_000)

        filas = await self._filas()
        if filas is None:
            return {"consultas": [], "descargado": False}
        textos: list[str] = []
        botones: list[int] = []
        for indice in range(await filas.count()):
            fila = filas.nth(indice)
            textos.append(_ESPACIOS.sub(" ", (await fila.text_content() or "")).strip())
            botones.append(await fila.locator("button").count())
        consultas = consultas_desde_filas(zip(textos, botones))

        indice = indice_consulta(textos, desde, hasta)
        if indice is None or botones[indice] == 0:
            return {"consultas": consultas, "descargado": False}

        # Como V1: el segundo boton de la fila es el de acciones, si existe.
        controles = filas.nth(indice).locator("button")
        objetivo = controles.nth(1) if botones[indice] > 1 else controles.first
        await objetivo.click(timeout=self.timeout_selector_ms)
        await self._red_quieta()
        await self._esperar(500)
        await self.capturar_descarga(
            destino,
            lambda: page.get_by_role("link", name="CSV").click(
                timeout=timeout_ms or TIMEOUT_MC_MS_POR_DEFECTO
            ),
            espera_ms=timeout_ms or TIMEOUT_MC_MS_POR_DEFECTO,
        )
        await self._clickear((page.get_by_text("Menú Principal", exact=True),), total_ms=10_000)
        await self._red_quieta()
        return {"consultas": consultas, "descargado": True}
