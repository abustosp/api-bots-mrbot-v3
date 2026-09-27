"""Flujo de consulta/descarga del certificado MiPyME (LUFE), port de V1/V2."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

_COMBO_LABEL = "Ingrese al menos 3 caracteres"
_SELECTOR_OPCIONES = "[role='option'], li[role='option'], .MuiAutocomplete-option"
_PATRON_CUIT = re.compile(r"(?<!\d)(\d{2}[-. ]?\d{8}[-. ]?\d)(?!\d)")


class CertificadoMipymePortal(PortalArca):
    """Selecciona el representado en LUFE y descarga su certificado vigente."""

    nombre = "certificado_mipyme"

    async def seleccionar_representado(self, cuit: str) -> None:
        """Selecciona sólo una opción cuyo CUIT coincide con el solicitado.

        V1/V2 y el flujo inline V3 elegían la primera opción cuando el CUIT
        pedido no aparecía. Eso podía descargar el certificado de otra entidad.
        Aquí se agota la búsqueda y se falla explícitamente en ese caso.
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        await self.paso("seleccionar_representado", self._seleccionar_cuit(digits))

    async def _seleccionar_cuit(self, digits: str) -> None:
        self._verificar_respuesta_servicio(await self._contenido_pagina())
        combo = self.page.get_by_role("combobox", name=_COMBO_LABEL)
        await combo.wait_for(state="visible", timeout=10_000)
        await combo.click()
        await self._intentar_expandir_combo()
        await self._esperar(1_500)

        opciones = self.page.locator(_SELECTOR_OPCIONES)
        if await self._click_opcion_coincidente(opciones, digits):
            await self._confirmar_seleccion()
            return

        # El listado inicial puede estar vacío o paginado. Buscar por CUIT,
        # pero nunca aceptar la primera sugerencia si no es la entidad pedida.
        await combo.fill(digits)
        for _ in range(5):
            await self._esperar(500)
            if await self._click_opcion_coincidente(opciones, digits):
                await self._confirmar_seleccion()
                return
        raise TargetUnavailableError(
            "no se encontró el CUIT representado solicitado en el selector LUFE",
            diagnostic_code="represented_cuit_not_selectable",
        )

    async def _intentar_expandir_combo(self) -> None:
        for label in ("expand combobox", "Abrir"):
            try:
                boton = self.page.get_by_role("button", name=label)
                if await boton.count() and await boton.first.is_visible():
                    await boton.first.click()
                    await self._esperar(500)
                    return
            except Exception:
                continue

    async def _click_opcion_coincidente(self, opciones: Any, digits: str) -> bool:
        total = await opciones.count()
        for indice in range(total):
            opcion = opciones.nth(indice)
            texto = await opcion.inner_text()
            encontrados = {
                solo_digitos(match.group(1)) for match in _PATRON_CUIT.finditer(texto)
            }
            if digits in encontrados:
                await opcion.click()
                return True
        return False

    async def _confirmar_seleccion(self) -> None:
        # En LUFE el botón aparece sólo en algunas versiones de la pantalla.
        try:
            boton = self.page.get_by_role("button", name="Seleccionar")
            await boton.wait_for(state="visible", timeout=5_000)
            await boton.click()
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            # Algunas vistas mantienen conexiones abiertas; la presencia del
            # enlace de descarga, comprobada más abajo, es la señal final.
            pass
        contenido = await self._contenido_pagina()
        self._verificar_respuesta_servicio(contenido)
        if "No se encuentra habilitado" in contenido:
            raise TargetUnavailableError(
                "no se encuentra habilitado el servicio de Certificado MiPyME",
                diagnostic_code="mipyme_service_not_enabled",
            )

    async def _contenido_pagina(self) -> str:
        """Obtiene HTML tras estabilizar navegación, tolerando cambios de página."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=30_000)
        except Exception:
            pass
        try:
            return await self.page.content()
        except Exception:
            # Playwright puede rechazar content() mientras la SPA navega.
            return ""

    @staticmethod
    def _verificar_respuesta_servicio(contenido: str) -> None:
        if re.search(r"(?:504\s*\|?\s*Gateway Timeout|Gateway Timeout)", contenido, re.I):
            raise TargetUnavailableError(
                "el servicio LUFE respondió 504 Gateway Timeout",
                diagnostic_code="mipyme_service_gateway_timeout",
            )

    async def descargar_certificado(self, destino: Path) -> Path:
        """Descarga y comprueba que el artefacto recibido sea un PDF no vacío."""
        self._verificar_respuesta_servicio(await self._contenido_pagina())
        enlace = self.page.get_by_role(
            "link", name=re.compile(r"Descargar Certificado MiPyME", re.I)
        )
        await enlace.wait_for(state="visible", timeout=15_000)
        archivo = await self.paso(
            "descargar_certificado",
            self.capturar_descarga(
                Path(destino),
                lambda: enlace.click(timeout=30_000),
                espera_ms=30_000,
            ),
        )
        try:
            with archivo.open("rb") as descargado:
                cabecera = descargado.read(5)
        except OSError as exc:
            raise TargetUnavailableError(
                "no se pudo leer el certificado descargado",
                diagnostic_code="mipyme_certificate_unreadable",
            ) from exc
        if cabecera != b"%PDF-":
            raise TargetUnavailableError(
                "el archivo descargado no tiene formato PDF válido",
                diagnostic_code="mipyme_certificate_invalid_pdf",
            )
        return archivo
