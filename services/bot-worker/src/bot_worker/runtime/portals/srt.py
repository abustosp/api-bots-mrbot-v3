"""Portal E-SERVICIOS SRT: alícuotas ART por CUIT.

Port de ``api-bots-mrbot/app/bot/srt_bot.py`` (V2). El plugin V3 pide
``consultar_cuit(cuit)`` por cada CUIT del lote y consolida el resultado.
"""
from __future__ import annotations

import re
from typing import Any

from bot_worker.bots.errors import CaptchaUnsolvableError, TargetUnavailableError

from .base import PortalArca

ALICUOTAS_URL = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"
_SIN_DATOS = (
    re.compile(r"no\s+tiene\s+afiliaci[oó]n\s+vigente", re.I),
    re.compile(r"no\s+registra\s+afiliaci[oó]n", re.I),
)
_IFRAME_RESULTADO = "#ifrmResultadoPorCuit"


class SrtPortal(PortalArca):
    """Consulta de alícuotas del Sistema de Riesgos del Trabajo."""

    nombre = "srt"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._preparado = False

    async def preparar(self) -> None:
        """Deja la página de consulta lista (port del preámbulo de V2).

        V2 abría el servicio desde Clave Fiscal, clickeaba "Ingresar" cuando el
        contribuyente lo pedía y recién después navegaba a la consulta. Sin ese
        preámbulo la página no trae el campo de CUIT.
        """
        await self._click_ingresar_temporal()
        await self.abrir_consulta()

    async def _click_ingresar_temporal(self) -> bool:
        """Click en "Ingresar" de la selección de representado (solo algunos CUIT)."""
        objetivo = await self._primero_visible(
            (
                self.page.locator("a[onclick*='LoguearRespresentado']"),
                self.page.locator("a[onclick*='LoguearRepresentado']"),
                self.page.locator("a.btn-success[onclick*='Loguear']"),
            ),
            total_ms=2_000,
        )
        if objetivo is None:
            return False
        try:
            await objetivo.click(timeout=5_000)
        except Exception:
            return False
        await self._esperar(1_500)
        return True

    async def abrir_consulta(self) -> None:
        """Abre la consulta de alícuotas dentro del portal SRT.

        Port de ``_open_srt_query_page`` de V2: primero se busca el enlace de la
        consulta en el portal (mantiene la sesión SSO que abrió Clave Fiscal) y
        solo como respaldo se navega directo a la URL. Navegar directo sin la
        sesión del portal devuelve ``/ErrorValidate.aspx``.
        """
        enlace = await self._primero_visible(
            (
                self.page.get_by_role("link", name=re.compile(r"consulta\s+de\s+al[ií]cuotas", re.I)),
                self.page.get_by_role("tab", name=re.compile(r"al[ií]cuotas", re.I)),
                self.page.locator("a", has_text=re.compile(r"al[ií]cuotas", re.I)),
                self.page.get_by_role("button", name=re.compile(r"al[ií]cuotas", re.I)),
            ),
            total_ms=8_000,
        )
        if enlace is not None:
            try:
                await enlace.click(timeout=6_000)
                await self._esperar(1_500)
            except Exception:
                pass
        if "ErrorValidate" in await self._url() or await self._campo_cuit() is None:
            await self.abrir_url(ALICUOTAS_URL)
            await self._esperar(1_000)

    async def _campo_cuit(self) -> Any | None:
        return await self._primero_visible(
            (
                self.page.locator("#txtCuilCuit"),
                self.page.locator("input[id*='cuit' i], input[name*='cuit' i]"),
                self.page.locator("input[type='number']"),
            ),
            total_ms=3_000,
        )

    async def _pista_de_pagina(self) -> str:
        """Ruta y cantidad de elementos clave: orienta sin exponer la página."""
        from urllib.parse import urlparse

        url = urlparse(await self._url())
        partes = [f"host={url.netloc}", f"ruta={url.path[:40]}"]
        for selector, etiqueta in (
            ("#txtCuilCuit", "txtCuilCuit"),
            ("input[type='number']", "input_number"),
            ("iframe", "iframes"),
            ("#btnConsultar", "btnConsultar"),
            ("a[onclick*='Loguear']", "loguear"),
        ):
            try:
                partes.append(f"{etiqueta}={await self.page.locator(selector).count()}")
            except Exception:
                partes.append(f"{etiqueta}=?")
        return " ".join(partes)

    async def consultar_cuit(self, cuit: str) -> dict[str, Any]:
        """Consulta la alícuota ART del CUIT y devuelve las tablas del portal."""
        digits = re.sub(r"\D", "", str(cuit or ""))
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT inválido para la consulta SRT",
                diagnostic_code="srt_cuit_invalid",
            )
        if not self._preparado:
            await self.preparar()
            self._preparado = True
        await self._limpiar_consulta_previa()
        if not await self._ingresar_cuit(digits):
            pista = await self._pista_de_pagina()
            raise TargetUnavailableError(
                f"no se pudo ingresar el CUIT en el portal SRT ({pista})",
                diagnostic_code="srt_cuit_input_failed",
            )
        # reCAPTCHA de la consulta (port de _detect_and_solve_captcha).
        await self.resolver_recaptcha()
        if not await self._consultar():
            raise TargetUnavailableError(
                "no se pudo ejecutar la consulta en el portal SRT",
                diagnostic_code="srt_query_failed",
            )
        await self._esperar_resultado()
        mensaje = await self._sin_afiliacion()
        if mensaje:
            return {"status": "SIN_AFILIACION_VIGENTE", "message": mensaje, "data": None}
        tablas = await self.tablas_del_frame(_IFRAME_RESULTADO)
        if not tablas:
            return {
                "status": "SIN_AFILIACION_VIGENTE",
                "message": "No se encontraron datos de alícuotas",
                "data": None,
            }
        return {"status": "OK", "message": None, "data": tablas}

    # ------------------------------------------------------------------

    async def _limpiar_consulta_previa(self) -> None:
        for selector in ("#btnLimpiar", "button:has-text('Limpiar')"):
            localizador = self.page.locator(selector)
            if await self._clickear((localizador,), total_ms=1_500):
                await self._esperar(500)
                return

    async def _ingresar_cuit(self, digits: str) -> bool:
        candidatos = (
            self.page.locator("#txtCuilCuit"),
            self.page.get_by_role("spinbutton", name=re.compile(r"CUIT/CUIL|CUIT|CUIL", re.I)),
            self.page.locator("input[aria-label*='CUIT' i]"),
            self.page.locator("input[placeholder*='CUIT' i]"),
            self.page.locator("input[id*='cuit' i], input[name*='cuit' i]"),
            self.page.locator("input[type='number']"),
        )
        objetivo = await self._primero_visible(candidatos, total_ms=8_000)
        if objetivo is None:
            return False
        for intento in ("fill", "type"):
            try:
                if intento == "fill":
                    await objetivo.fill(digits, timeout=5_000)
                else:
                    await objetivo.click(timeout=3_000)
                    await objetivo.type(digits, delay=40, timeout=8_000)
                return True
            except Exception:
                continue
        return False

    async def _consultar(self) -> bool:
        return await self._clickear(
            (
                self.page.locator("#btnConsultar"),
                self.page.get_by_role("button", name=re.compile(r"^Consultar$", re.I)),
                self.page.locator("input[type='submit'][value*='Consultar' i]"),
                self.page.locator("button:has-text('Consultar')"),
            ),
            total_ms=8_000,
        )

    async def _esperar_resultado(self) -> None:
        try:
            await self.page.wait_for_selector(
                f"{_IFRAME_RESULTADO}, #myModal, [id*='Resultado' i]", timeout=20_000
            )
        except Exception:
            raise CaptchaUnsolvableError("el portal SRT no devolvió resultado") from None
        await self._esperar(1_000)

    async def _sin_afiliacion(self) -> str | None:
        """Mensaje de 'sin afiliación vigente' si el portal lo informa."""
        for selector in ("#myModal .modal-body", "#myModal"):
            try:
                modal = self.page.locator(selector)
                if await modal.count() == 0:
                    continue
                texto = await self._texto(modal.first)
                for patron in _SIN_DATOS:
                    if patron.search(texto):
                        return texto
            except Exception:
                continue
        try:
            frame = self.page.frame_locator(_IFRAME_RESULTADO)
            texto = await self._texto(frame.locator("body"))
            for patron in _SIN_DATOS:
                if patron.search(texto):
                    return texto
        except Exception:
            pass
        try:
            texto = await self._texto(self.page.locator("body"))
            for patron in _SIN_DATOS:
                if patron.search(texto):
                    return texto[:300]
        except Exception:
            pass
        return None
