"""Portal CCMA (Cuenta Corriente de Monotributistas y Autónomos): VEP por volante.

Port de ``api-bots-mrbot/app/bot/vep_ccma_bot.py``: ``_scrape_volante_table``,
``_check_volante_rows``, ``_row_matches_filtro`` y el flujo de ``bot_vep_ccma``.
El plugin V3 abre el servicio con ``portal="vep_ccma"`` y llama, en orden,
``seleccionar_representado``, ``listar_obligaciones``, ``generar_volante``,
``descargar_detalle`` y ``descargar_qr``.

Decisiones:

- Los filtros (``filtro_impuestos``/``filtro_intereses``) y el QR se portan pero
  quedan inactivos: el esquema V3 no los acepta (el QR requiere ``medio_pago
  == "qr"``, que el esquema rechaza), así que el plugin no los envía.
- Generar el volante tiene efecto fiscal. Se reintenta solo ANTES de pulsar
  «GENERAR VEP O QR»: después del click no hay reintentos internos.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any, Sequence

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos
from .vep_archivo import (
    MEDIO_PAGO_IMG_IDS,
    descargar_detalle_vep,
    guardar_qr,
    validar_medio_pago,
)

PERIODO_DESDE = "04/2000"
RANGO_MESES = "48"
REINTENTOS_BOTON = 2

FIELD_MAP_IMPUESTOS: dict[str, str] = {
    "periodo": "periodo",
    "impuesto": "impuesto",
    "concepto": "concepto",
    "subconcepto": "subconcepto",
    "categoria": "categoria",
}
FIELD_MAP_INTERESES: dict[str, str] = {
    "periodo": "periodo",
    "impuesto": "impuesto",
    "concepto": "concepto",
    "subcpto": "subconcepto",
}

_JS_SCRAPE_VOLANTE = """
() => {
    const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();

    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    const rows = [];

    checkboxes.forEach((cb, index) => {
        const row = cb.closest('tr');
        if (!row) return;

        const cells = Array.from(row.querySelectorAll('td'));
        if (cells.length < 6) return;

        const cellTexts = cells.map(c => cleanText(c.textContent));

        const periodo = cellTexts[0] || '';
        if (periodo.length > 10 || !periodo) return;
        if (/[{<>}]/.test(cellTexts.join(''))) return;

        const cbName = (cb.name || '').toLowerCase();
        const tipo = cbName.includes('interes') ? 'interes' : 'capital';

        const parseImporte = (val) => {
            if (!val) return 0;
            const cleaned = val.replace(/,/g, '');
            const num = parseFloat(cleaned);
            return isNaN(num) ? 0 : num;
        };

        const esInteres = tipo === 'interes';
        const catStr = esInteres ? '' : (cellTexts[4] || '');
        const impStr = esInteres ? (cellTexts[4] || '') : (cellTexts[5] || '');

        rows.push({
            checkbox_index: index,
            checkbox_name: cb.name || '',
            checkbox_value: cb.value || '',
            tipo: tipo,
            periodo: periodo,
            impuesto: cellTexts[1] || '',
            concepto: cellTexts[2] || '',
            subconcepto: cellTexts[3] || '',
            categoria: esInteres ? '' : catStr,
            importe: parseImporte(impStr),
        });
    });

    return rows;
}
"""

_JS_CHECK_ROWS = """
({ indices }) => {
    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    indices.forEach(idx => {
        if (idx >= 0 && idx < checkboxes.length) {
            const cb = checkboxes[idx];
            if (!cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb.dispatchEvent(new Event('click', { bubbles: true }));
            }
        }
    });
}
"""

_JS_UNCHECK_ALL = """
() => {
    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = false;
        cb.dispatchEvent(new Event('change', { bubbles: true }));
        cb.dispatchEvent(new Event('click', { bubbles: true }));
    });
}
"""

_JS_CLICK_POR_VALUE = """(text) => {
    var safe = text.replace(/"/g, '\\\\"').replace(/'/g, "\\\\'");
    var el = document.querySelector('input[value*="' + safe + '"]:not([style*="display: none"])');
    if (!el) {
        var all = document.querySelectorAll('[value*="' + safe + '"]');
        for (var i = 0; i < all.length; i++) {
            if (all[i].offsetParent !== null) {
                el = all[i];
                break;
            }
        }
    }
    if (el) {
        el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
        return true;
    }
    return false;
}"""


# ----------------------------------------------------------------------
# Helpers de módulo (portados de V1)

async def _scrape_volante_table(page: Any) -> list[dict[str, Any]]:
    """Filas (capital e interés) de la tabla del volante de pago."""
    resultado = await page.evaluate(_JS_SCRAPE_VOLANTE)
    if not isinstance(resultado, list):
        return []
    return [fila for fila in resultado if isinstance(fila, dict)]


async def _check_volante_rows(page: Any, indices: Sequence[int]) -> None:
    await page.evaluate(_JS_CHECK_ROWS, {"indices": list(indices)})


async def _uncheck_all_volante_rows(page: Any) -> None:
    await page.evaluate(_JS_UNCHECK_ALL)


def _row_matches_filtro(
    row: dict[str, Any],
    filtro_entry: dict[str, Any],
    field_map: dict[str, str],
) -> bool:
    """Una fila coincide si cumple todas las claves del filtro (AND)."""
    for filter_key, filter_value in filtro_entry.items():
        row_key = field_map.get(filter_key, filter_key)
        row_value = str(row.get(row_key, "")).strip()
        if isinstance(filter_value, list):
            valores = [str(v).strip() for v in filter_value if v is not None]
            if not valores:
                continue
            if row_value not in valores:
                return False
        else:
            texto = str(filter_value).strip() if filter_value is not None else ""
            if texto and row_value != texto:
                return False
    return True


def indices_a_seleccionar(
    capital: Sequence[dict[str, Any]],
    intereses: Sequence[dict[str, Any]],
    *,
    filtro_impuestos: Sequence[dict[str, Any]] | None = None,
    filtro_intereses: Sequence[dict[str, Any]] | None = None,
) -> list[int]:
    """Índices de checkbox a tildar. Sin filtro se toman todas las filas.

    Los filtros están inactivos en V3 (el plugin no los envía), pero la
    lógica se conserva para cuando el esquema los acepte.
    """
    elegidos: set[int] = set()
    for filas, filtros, mapa in (
        (capital, filtro_impuestos, FIELD_MAP_IMPUESTOS),
        (intereses, filtro_intereses, FIELD_MAP_INTERESES),
    ):
        for fila in filas:
            if filtros:
                if any(
                    entrada and _row_matches_filtro(fila, entrada, mapa)
                    for entrada in filtros
                ):
                    elegidos.add(fila.get("checkbox_index", -1))
            else:
                elegidos.add(fila.get("checkbox_index", -1))
    elegidos.discard(-1)
    return sorted(i for i in elegidos if isinstance(i, int))


class VepCcmaPortal(PortalArca):
    """Volante de pago VEP de la CCMA."""

    nombre = "vep_ccma"
    reintentos_boton = REINTENTOS_BOTON

    def __init__(self, page: Any, **kwargs: Any) -> None:
        super().__init__(page, **kwargs)
        self._medio_pago = ""
        self._filas: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Representado

    async def seleccionar_representado(self, cuit: str) -> None:
        """Elige el CUIT en el listbox «Elegir CUIT».

        Sin listbox (cuenta con un único CUIT) no hay nada que elegir, como
        en V1. Si el listbox existe y no admite el CUIT, es un error.
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        lista = self.page.get_by_role("listbox")
        try:
            hay_lista = await lista.count() > 0
        except Exception:
            hay_lista = False
        if not hay_lista:
            return
        try:
            await lista.select_option(digits, timeout=2_000)
            await self.page.get_by_role(
                "button", name=re.compile(r"Elegir CUIT", re.I)
            ).click()
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo seleccionar el CUIT representado en CCMA",
                diagnostic_code="represented_cuit_not_selectable",
            ) from exc
        await self._esperar(700)

    # ------------------------------------------------------------------
    # Obligaciones

    async def listar_obligaciones(self) -> dict[str, list[dict[str, Any]]]:
        """Calcula la deuda, abre el volante y scrapea sus filas.

        Devuelve ``{"impuestos": [...], "intereses": [...]}``. Es de solo
        lectura: puede reintentarse sin riesgo.
        """
        await self.paso("calculo_deuda", self._calcular_deuda())
        await self.paso("abrir_volante", self._abrir_volante())
        filas = await self.paso("leer_volante", _scrape_volante_table(self.page))
        if not filas:
            raise TargetUnavailableError(
                "el volante de pago no tiene filas",
                diagnostic_code="vep_ccma_volante_vacio",
            )
        self._filas = filas
        return {
            "impuestos": [f for f in filas if f.get("tipo") == "capital"],
            "intereses": [f for f in filas if f.get("tipo") == "interes"],
        }

    async def _calcular_deuda(self) -> None:
        page = self.page
        await page.get_by_role("cell", name="PERÍODO DESDE (MM/AAAA)", exact=True).click()
        campo = page.locator('input[name="perdesde2"]')
        await campo.click()
        await campo.press("ArrowRight")
        await campo.fill(PERIODO_DESDE)
        await page.get_by_role("button", name="CALCULO DE DEUDA").click()
        with contextlib.suppress(Exception):
            await page.locator('select[name="rango"]').select_option(RANGO_MESES, timeout=3_000)
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=3_000)

    async def _abrir_volante(self) -> None:
        for _ in range(max(1, int(self.reintentos_boton))):
            if await self._click_boton_por_texto("VOLANTE DE PAGO"):
                await self._esperar(2_000)
                return
            await self._esperar(1_000)
        raise TargetUnavailableError(
            "no se encontró el botón VOLANTE DE PAGO",
            diagnostic_code="vep_ccma_volante_button_missing",
        )

    # ------------------------------------------------------------------
    # Generación

    async def generar_volante(
        self,
        seleccion: dict[str, Any],
        medio_pago: str,
        *,
        filtro_impuestos: Sequence[dict[str, Any]] | None = None,
        filtro_intereses: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Tilda las filas elegidas, genera el VEP y elige el medio de pago.

        ``seleccion`` trae las filas (de ``listar_obligaciones``) a pagar.
        Devuelve ``{"volante": [filas seleccionadas]}``.
        """
        medio = validar_medio_pago(medio_pago)
        capital = [f for f in (seleccion or {}).get("impuestos", []) if isinstance(f, dict)]
        intereses = [f for f in (seleccion or {}).get("intereses", []) if isinstance(f, dict)]
        indices = indices_a_seleccionar(
            capital,
            intereses,
            filtro_impuestos=filtro_impuestos,
            filtro_intereses=filtro_intereses,
        )
        if not indices:
            raise TargetUnavailableError(
                "no hay filas seleccionadas en el volante de pago",
                diagnostic_code="vep_ccma_seleccion_vacia",
            )
        elegidas = [
            f for f in capital + intereses if f.get("checkbox_index") in set(indices)
        ]
        # Fase previa al efecto (reintentable): tildar solo las filas elegidas
        # y ubicar el botón final.
        await self.paso("tildar_filas", self._tildar(indices))
        boton = await self._boton_generar()
        # Efecto fiscal: un único click, sin reintentos posteriores.
        try:
            await boton.scroll_into_view_if_needed()
            await boton.click(timeout=5_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "no se pudo confirmar el click en GENERAR VEP O QR",
                diagnostic_code="vep_ccma_generar_click_ambiguo",
            ) from exc
        await self._esperar(3_000)
        await self._cerrar_modales()
        self._medio_pago = medio
        await self.paso("seleccionar_medio_pago", self._elegir_medio_pago(medio))
        return {"volante": elegidas}

    async def _tildar(self, indices: Sequence[int]) -> None:
        await _uncheck_all_volante_rows(self.page)
        await _check_volante_rows(self.page, indices)
        await self._esperar(1_000)

    async def _boton_generar(self) -> Any:
        for _ in range(max(1, int(self.reintentos_boton))):
            boton = await self._primero_visible(self._candidatos_boton("GENERAR VEP O QR"), total_ms=2_000)
            if boton is not None:
                return boton
            await self._esperar(1_000)
        raise TargetUnavailableError(
            "no se encontró el botón GENERAR VEP O QR",
            diagnostic_code="vep_ccma_generar_button_missing",
        )

    async def _cerrar_modales(self) -> None:
        page = self.page
        for _ in range(3):
            for accion in (
                lambda: page.get_by_text("close", exact=True).click(timeout=2_000),
                lambda: page.get_by_role("button", name="Close").click(timeout=2_000),
                lambda: page.get_by_role("button", name="Aceptar").click(timeout=2_000),
            ):
                with contextlib.suppress(Exception):
                    await accion()

    async def _elegir_medio_pago(self, medio: str) -> None:
        page = self.page
        await page.locator(f'img[id="{MEDIO_PAGO_IMG_IDS[medio]}"]').click(timeout=5_000)
        await self._esperar(500)
        with contextlib.suppress(Exception):
            await page.get_by_role("button", name="Aceptar").click(timeout=5_000)

    # ------------------------------------------------------------------
    # Descargas

    async def descargar_detalle(self, destino: str | Path) -> Path:
        """Descarga el PDF del VEP generado."""
        return await self.paso("descargar_detalle", descargar_detalle_vep(self, Path(destino)))

    async def descargar_qr(self, destino: str | Path) -> bool:
        """Guarda el QR si el medio de pago fue ``qr``. Inactivo en V3."""
        if self._medio_pago != "qr":
            return False
        return await guardar_qr(self.page, Path(destino))

    # ------------------------------------------------------------------
    # Botones por texto (port de ``_click_button_by_text``)

    def _candidatos_boton(self, texto: str) -> tuple[Any, ...]:
        page = self.page
        patron = re.compile(re.escape(texto), re.I)
        seguro_up = texto.upper().replace("'", "\\'").replace('"', '\\"')
        seguro_low = texto.lower().replace("'", "\\'").replace('"', '\\"')
        return (
            page.get_by_role("button", name=patron),
            page.get_by_role("link", name=patron),
            page.get_by_text(texto, exact=True),
            page.locator(f"button:has-text('{seguro_up}')"),
            page.locator(f"button:has-text('{seguro_low}')"),
            page.locator(f"a:has-text('{seguro_up}')"),
            page.locator(f"a:has-text('{seguro_low}')"),
            page.locator(f"input[type='button'][value*='{seguro_up}']"),
            page.locator(f"input[type='submit'][value*='{seguro_up}']"),
            page.locator(f"input[value*='{seguro_up}']"),
            page.locator(f"[class*='button']:has-text('{seguro_up}')"),
            page.locator(f"[class*='btn']:has-text('{seguro_up}')"),
        )

    async def _click_boton_por_texto(self, texto: str) -> bool:
        if await self._clickear(self._candidatos_boton(texto), total_ms=2_000):
            return True
        try:
            return bool(await self.page.evaluate(_JS_CLICK_POR_VALUE, texto))
        except Exception:
            return False
