"""Portal CCMA: acciones de la cuenta corriente sobre la pagina de ARCA (port V1 ``ccma_bot``).

El plugin ``bots/ccma`` decide que consultar; este modulo sabe como leerlo de la
pagina. Implementa el protocolo de ``bots/ccma/movimientos.py`` sobre Playwright:
el estado de la tabla, las filas visibles y el clic en ``#mas1`` con la
verificacion de cambio que V1 hace despues de cada pagina.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca

MOVIMIENTOS_TABLA = 'table[width="729"][border="0"][cellpadding="0"][height="5"][class="CabeceraTablaBorde_ConsDeuFec"]'
MOVIMIENTOS_FILA = "tbody tr[valign='middle'][align='center']"
MOVIMIENTOS_SIGUIENTE = "#mas1"
RESUMEN_CELDA = "td.CeldaTitularResaltado"
CAMPOS_RESUMEN = (
    "periodo",
    "deuda_capital",
    "deuda_accesorios",
    "total_deuda",
    "credito_capital",
    "credito_accesorios",
    "total_a_favor",
)

_JS_ESTADO = """
({ tableSelector, rowSelector, nextSelector }) => {
    const clean = (v) => (v || "").replace(/\\u00a0/g, " ").trim();
    const table = document.querySelector(tableSelector);
    const nextButton = document.querySelector(nextSelector);
    const rows = table ? Array.from(table.querySelectorAll(rowSelector)) : [];
    const visibles = rows.filter((r) => window.getComputedStyle(r).display !== "none");
    const datos = visibles.filter((r) => {
        const celdas = r.querySelectorAll("td.CeldaBorde_ConsDeuFec");
        return celdas.length >= 10 && clean(celdas[6]?.textContent) !== "Saldo";
    });
    const periodo = (r) => (r ? clean(r.querySelectorAll("td.CeldaBorde_ConsDeuFec")[2]?.textContent) : "");
    return {
        tablePresent: Boolean(table),
        primerPeriodo: clean(document.getElementById("primerPeriodo")?.value),
        ultimoPeriodo: clean(document.getElementById("ultimoPeriodo")?.value),
        nextVisible: Boolean(nextButton && window.getComputedStyle(nextButton).display !== "none"),
        firstVisiblePeriodo: periodo(datos[0]),
        lastVisiblePeriodo: periodo(datos[datos.length - 1]),
        visibleDataRows: datos.length,
    };
}
"""

_JS_FILAS = """
({ tableSelector, rowSelector }) => {
    const clean = (v) => (v || "").replace(/\\u00a0/g, " ").trim();
    const table = document.querySelector(tableSelector);
    if (!table) return [];
    return Array.from(table.querySelectorAll(rowSelector))
        .filter((r) => window.getComputedStyle(r).display !== "none")
        .map((r) => Array.from(r.querySelectorAll("td.CeldaBorde_ConsDeuFec")).map((c) => clean(c.textContent)))
        .filter((c) => c.length >= 10 && c[6] !== "Saldo")
        .map((c) => ({
            periodo: c[2], impuesto: c[3], concepto: c[4], subconcepto: c[5],
            descripcion: c[6], fecha_movimiento: c[7], debe: c[8], haber: c[9],
        }));
}
"""


class CcmaPortal(PortalArca):
    """Cuenta corriente CCMA: resumen de saldos y recorrido de movimientos."""

    nombre = "ccma"

    async def seleccionar_cuit(self, cuit: str) -> None:
        """Elige el CUIT representado si la pantalla muestra el listbox (V1 tolerante)."""
        try:
            await self.page.get_by_role("listbox").select_option(cuit, timeout=2_000)
            await self.page.get_by_role("button", name=re.compile(r"Elegir CUIT", re.I)).click()
        except Exception:
            # Sin listbox: la sesion ya quedo posicionada sobre el contribuyente.
            pass

    async def calcular_deuda(self, periodo_desde: str) -> None:
        """Carga el periodo desde y pide el calculo de deuda (V1)."""
        await self._cargar_periodo(periodo_desde)

    async def ir_a_cuenta_corriente(self, url: str) -> None:
        """Navega a la cuenta corriente tras el calculo (V1 ``page1.goto``)."""
        await self.page.goto(url)
        await self._esperar_resumen()

    async def seleccionar_rango(self, valor: str) -> None:
        """Muestra ``valor`` periodos por pagina. Si el selector no existe, sigue (V1)."""
        try:
            await self.page.locator("select[name='rango']").select_option(valor, timeout=3_000)
            await self._red_quieta()
        except Exception:
            pass

    async def imprimir_pdf(self, html: str, destino: Path) -> Path:
        """Renderiza el HTML consolidado a ``destino`` en una pestana auxiliar (V1 ``_subir_pdf_ccma``)."""
        auxiliar = await self._context.new_page()
        try:
            await auxiliar.set_content(html, wait_until="domcontentloaded")
            try:
                await auxiliar.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:
                pass
            await auxiliar.emulate_media(media="screen")
            await auxiliar.pdf(
                path=str(destino),
                format="A4",
                landscape=True,
                print_background=True,
                margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"},
            )
        finally:
            try:
                await auxiliar.close()
            except Exception:
                pass
        return Path(destino)

    async def _red_quieta(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

    async def _cargar_periodo(self, periodo_desde: str) -> None:
        await self.page.get_by_role("cell", name="PERÍODO DESDE (MM/AAAA)", exact=True).click()
        campo = self.page.locator("input[name='perdesde2']")
        await campo.click()
        # V1 mueve el cursor al final antes de completar el campo.
        await campo.press("ArrowRight")
        await campo.fill(periodo_desde)
        await self.page.get_by_role("button", name="CALCULO DE DEUDA").click()

    async def _esperar_resumen(self, timeout_ms: int = 12_000) -> None:
        """Espera a que el resumen tenga al menos tres celdas con texto (V1)."""
        await self.page.wait_for_function(
            """({ summarySelector }) => {
                const textos = Array.from(document.querySelectorAll(summarySelector))
                    .map((c) => (c.textContent || "").replace(/\\u00a0/g, " ").trim())
                    .filter(Boolean);
                return textos.length >= 3;
            }""",
            arg={"summarySelector": RESUMEN_CELDA},
            timeout=timeout_ms,
        )

    async def resumen(self, cuit: str, periodo_desde: str) -> dict[str, str]:
        """Saldos del resumen CCMA. Falla si faltan los campos principales (V1)."""
        datos = await self.page.evaluate(
            """({ cuit, summarySelector }) => {
                const clean = (v) => (v || "").replace(/\\u00a0/g, " ").trim();
                const textos = Array.from(document.querySelectorAll(summarySelector))
                    .map((c) => clean(c.textContent));
                const xp = (xpath) => {
                    const nodo = document.evaluate(xpath, document, null, 9, null).singleNodeValue;
                    return clean(nodo ? nodo.textContent : "");
                };
                const base = "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/table/tbody/tr[2]/td/table/tbody/tr";
                return {
                    cuit,
                    periodo: textos[0] || "",
                    deuda_capital: xp(base + "[2]/td[2]/table/tbody/tr/td[1]"),
                    deuda_accesorios: xp(base + "[3]/td[2]/table/tbody/tr/td[1]"),
                    total_deuda: textos[1] || "",
                    credito_capital: xp(base + "[2]/td[4]/table/tbody/tr/td[1]"),
                    credito_accesorios: xp(base + "[3]/td[4]/table/tbody/tr/td[1]"),
                    total_a_favor: textos[2] || "",
                };
            }""",
            {"cuit": str(cuit), "summarySelector": RESUMEN_CELDA},
        )
        faltan = [c for c in CAMPOS_RESUMEN if not (datos or {}).get(c)]
        if faltan:
            raise TargetUnavailableError(
                "no se pudieron obtener los campos principales de CCMA: " + ", ".join(faltan),
                diagnostic_code="ccma_summary_incomplete",
            )
        return {**datos, "periodo_desde": periodo_desde}

    async def estado(self) -> dict[str, Any]:
        return await self.page.evaluate(
            _JS_ESTADO,
            {"tableSelector": MOVIMIENTOS_TABLA, "rowSelector": MOVIMIENTOS_FILA, "nextSelector": MOVIMIENTOS_SIGUIENTE},
        )

    async def filas(self) -> list[dict[str, str]]:
        return await self.page.evaluate(
            _JS_FILAS, {"tableSelector": MOVIMIENTOS_TABLA, "rowSelector": MOVIMIENTOS_FILA}
        )

    async def siguiente_disponible(self) -> bool:
        return await self.page.locator(MOVIMIENTOS_SIGUIENTE).count() > 0

    async def avanzar(self) -> bool:
        """Clic en ``#mas1`` con los mismos reintentos que V1 (normal, forzado, JS)."""
        boton = self.page.locator(MOVIMIENTOS_SIGUIENTE).first
        try:
            await boton.scroll_into_view_if_needed(timeout=2_000)
        except Exception:
            pass
        for opciones in ({"timeout": 3_000}, {"timeout": 3_000, "force": True}):
            try:
                await boton.click(**opciones)
                await self._esperar_dom_listo()
                return True
            except Exception:
                continue
        try:
            handle = await boton.element_handle()
            if handle is not None:
                await handle.evaluate("(el) => el.click()")
                await self._esperar_dom_listo()
                return True
        except Exception:
            pass
        try:
            clicado = await self.page.evaluate(
                "(sel) => { const el = document.querySelector(sel); if (!el) return false; el.click(); return true; }",
                MOVIMIENTOS_SIGUIENTE,
            )
        except Exception:
            return False
        if not clicado:
            return False
        await self._esperar_dom_listo()
        return True

    async def _esperar_dom_listo(self) -> None:
        """Espera el ``domcontentloaded`` posterior al clic, como V1."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=1_500)
        except Exception:
            pass

    async def esperar_cambio(self, estado_previo: dict[str, Any], timeout_ms: int) -> bool:
        """Espera a que la tabla cambie respecto del estado previo (V1 ``_wait_for_movimientos_state_change``)."""
        claves = ("primerPeriodo", "ultimoPeriodo", "firstVisiblePeriodo", "lastVisiblePeriodo", "nextVisible")
        previo = {k: estado_previo.get(k, "") for k in claves}
        try:
            # El estado actual se calcula dentro del navegador con la misma logica de
            # ``_JS_ESTADO``, para comparar valores reales y no una funcion.
            await self.page.wait_for_function(
                """({ estadoJs, tableSelector, rowSelector, nextSelector, previo, claves }) => {
                    const actual = (new Function('return ' + estadoJs))()({ tableSelector, rowSelector, nextSelector });
                    return claves.some((k) => actual[k] !== previo[k]);
                }""",
                arg={
                    "estadoJs": _JS_ESTADO,
                    "tableSelector": MOVIMIENTOS_TABLA,
                    "rowSelector": MOVIMIENTOS_FILA,
                    "nextSelector": MOVIMIENTOS_SIGUIENTE,
                    "previo": previo,
                    "claves": list(claves),
                },
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    async def pdf(self, destino: Path) -> Path:
        """Imprime la pagina actual a ``destino``. Para el PDF consolidado usar ``bots/ccma/pdf.py``."""
        await self.page.pdf(path=str(destino))
        return Path(destino)
