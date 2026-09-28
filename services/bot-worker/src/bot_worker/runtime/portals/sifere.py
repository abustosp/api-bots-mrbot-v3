"""Portal COMARB SIFERE Web: retenciones y percepciones por jurisdicción.

Port de ``api-bots-mrbot/app/bot/sifere_bot.py`` (V2). El plugin V3 pide
``consultar_jurisdiccion(codigo, periodo, destino)`` por cada jurisdicción.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

BASE_COMARB = "https://app1.comarb.gob.ar/siferewebconsultas"
URL_RETENCIONES = (
    BASE_COMARB + "/srcRetencion.do?method=retencion"
    "&juris={juris}&cuitAgente=&mesDesde={mes}&anioDesde={anio}"
    "&mesHasta={mes}&anioHasta={anio}"
)
URL_INICIO_RETENCIONES = BASE_COMARB + "/srcRetencion.do?method=retencionIn"


class SiferePortal(PortalArca):
    """Consultas SIFERE Web (COMARB) por jurisdicción."""

    nombre = "sifere"

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._representado_listo = False
        self._retenciones_abiertas = False

    # ------------------------------------------------------------------

    async def seleccionar_representado(self, cuit: str) -> None:
        """Elige el representado en el selector ``#selectedCuit`` del portal."""
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        await self.paso("entrar_al_portal", self._resolver_entry_point())
        selector, ambito = await self._buscar_selector_cuit()
        if selector is None and "portalcf.cloud.afip.gob.ar" in await self._url():
            # El click del catálogo puede disparar el SSO sin abrir el popup
            # en Chromium. Entrar por mainMenu.do evita la redirección HTTP
            # de la raíz COMARB y conserva la sesión del mismo contexto.
            await self.paso(
                "navegar_sifere",
                self.abrir_url(f"{BASE_COMARB}/mainMenu.do", timeout_ms=30_000),
            )
            await self.paso("resolver_entry_point", self._resolver_entry_point())
            selector, ambito = await self._buscar_selector_cuit()
        if selector is None:
            raise TargetUnavailableError(
                "el portal SIFERE no mostró el selector de representado",
                diagnostic_code="sifere_selector_missing",
            )
        try:
            await selector.select_option(digits)
        except Exception:
            try:
                await selector.select_option(value=digits)
            except Exception as exc:
                raise TargetUnavailableError(
                    "no se pudo elegir el CUIT representado en SIFERE",
                    diagnostic_code="represented_cuit_not_selectable",
                ) from exc
        try:
            enviado = await selector.evaluate(
                "select => {"
                "const form = select.form;"
                "if (!form) return false;"
                # Avoid Playwright waiting for a slow navigation triggered by
                # click(), and let evaluate() resolve before navigation starts.
                "setTimeout(() => {"
                "if (typeof form.requestSubmit === 'function') form.requestSubmit();"
                "else form.submit();"
                "}, 0);"
                "return true;"
                "}"
            )
        except Exception:
            enviado = False
        if not enviado and not await self._clickear(
            (
                ambito.get_by_role("button", name=re.compile(r"Seleccionar", re.I)),
                ambito.get_by_text("Seleccionar", exact=True),
                ambito.locator("button:has-text('Seleccionar')"),
                ambito.locator("input[type='submit'][value*='Seleccionar' i]"),
                ambito.locator("input[type='button'][value*='Seleccionar' i]"),
            ),
            total_ms=6_000,
        ):
            try:
                diagnostico = await selector.evaluate(
                    "select => {"
                    "const form = select.form;"
                    "const controls = Array.from(document.querySelectorAll("
                    "'button,input[type=submit],input[type=button],input[type=image],a'"
                    ")).map(el => ({"
                    "tag: el.tagName.toLowerCase(),"
                    "type: el.getAttribute('type') || '',"
                    "label: (el.innerText || el.value || el.title || '').trim().slice(0, 80)"
                    "})).filter(el => /seleccionar|continuar|ingresar|aceptar|entrar|submit/i.test(el.label));"
                    "return JSON.stringify({form: Boolean(form), form_controls: form ? form.querySelectorAll('button,input, a').length : 0, in_frame: window !== window.top, labels: controls});"
                    "}"
                )
            except Exception as exc:
                diagnostico = type(exc).__name__
            raise TargetUnavailableError(
                f"el portal SIFERE no confirmó la selección del representado ({diagnostico})",
                diagnostic_code="sifere_seleccionar_missing",
            )
        try:
            await ambito.wait_for_load_state("domcontentloaded", timeout=12_000)
        except Exception:
            pass
        await self._esperar(2_500)
        self._representado_listo = True

    async def _resolver_entry_point(self) -> None:
        """Acepta la pantalla intermedia ``entryPoint.login`` cuando aparece."""
        try:
            if "entryPoint.login" not in await self._url():
                return
        except Exception:
            return
        for etiqueta in ("Ingresar", "Continuar", "Aceptar"):
            if await self._clickear(
                (self.page.get_by_role("button", name=etiqueta),), total_ms=3_000
            ):
                await self._esperar(1_500)
                return
        try:
            await self.page.evaluate("document.forms[0].submit()")
        except Exception:
            pass
        await self._esperar(1_500)

    async def _buscar_selector_cuit(self) -> tuple[Any | None, Any]:
        """Selector ``#selectedCuit`` en la página o dentro de un iframe."""
        for _ in range(6):
            try:
                directo = self.page.locator("#selectedCuit")
                if await directo.count():
                    return directo.first, self.page
            except Exception:
                pass
            for marco in list(getattr(self.page, "frames", []) or []):
                try:
                    en_frame = marco.locator("#selectedCuit")
                    if await en_frame.count():
                        return en_frame.first, marco
                except Exception:
                    continue
            await self._esperar(1_000)
        return None, self.page

    # ------------------------------------------------------------------

    async def consultar_jurisdiccion(
        self, codigo: int | str, periodo: str, destino: Path
    ) -> dict[str, Any]:
        """Descarga el Excel de retenciones de la jurisdicción y período."""
        if not self._representado_listo:
            raise TargetUnavailableError(
                "no se seleccionó el representado antes de consultar SIFERE",
                diagnostic_code="sifere_representado_faltante",
            )
        texto = re.sub(r"\D", "", str(periodo))
        if len(texto) != 6:
            raise TargetUnavailableError(
                "período SIFERE inválido (se espera AAAAMM)",
                diagnostic_code="sifere_periodo_invalido",
            )
        anio, mes = texto[:4], texto[4:]
        # Struts inicializa su colección de jurisdicciones al abrir la pantalla
        # retencionIn. Entrar directamente a method=retencion provoca
        # "Failed to obtain specified collection" aunque el SSO sea válido.
        if not self._retenciones_abiertas:
            await self.paso(
                "abrir_retenciones",
                self.abrir_url(URL_INICIO_RETENCIONES),
            )
            self._retenciones_abiertas = True
        url = URL_RETENCIONES.format(juris=codigo, mes=mes, anio=anio)
        await self.paso("abrir_jurisdiccion", self.abrir_url(url))
        await self._esperar(1_200)
        destino = Path(destino)
        await self.paso(
            "descargar_excel",
            self.capturar_descarga(destino, self._click_excel, espera_ms=45_000),
        )
        return {"filas": _contar_filas(destino), "jurisdiccion": codigo, "periodo": texto}

    async def _click_excel(self) -> None:
        if not await self._clickear(
            (
                self.page.get_by_role("link", name=re.compile(r"^Excel$", re.I)),
                self.page.locator("a:has-text('Excel')"),
                self.page.locator("input[type='submit'][value*='Excel' i]"),
            ),
            total_ms=15_000,
        ):
            raise TargetUnavailableError(
                "el portal SIFERE no ofreció la descarga Excel",
                diagnostic_code="sifere_excel_missing",
            )


def _contar_filas(ruta: Path) -> int:
    """Cantidad de filas si el archivo es texto; 0 si es binario."""
    try:
        with open(ruta, "rb") as fh:
            crudo = fh.read(200_000)
        if crudo[:2] in (b"PK", b"\xd0\xcf"):
            return 0
        for codec in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                texto = crudo.decode(codec)
                break
            except UnicodeDecodeError:
                continue
        else:
            return 0
        return max(0, len([l for l in texto.splitlines() if l.strip()]) - 1)
    except Exception:
        return 0
