"""Portal Monotributo: facturómetro (monto, tope y categoría).

Port de ``api-bots-mrbot-v2/app/bot/facturometro_bot.py``
(``bot_facturometro``). El plugin V3 pide ``leer_facturometro(cuit)`` y
este portal resuelve la lectura sobre la página del servicio
``MONOTRIBUTO`` abierto por la sesión ARCA.

La lectura replica el orden de V2: espera de la página del servicio,
selección del representado (con los selectores de V2 y, si no están, los
combos/listas genéricos de portales), click en ``Inicio`` si existe,
lectura de ``#spanFacturometroMonto`` / ``#spanFacturometroCategoriaTope``
/ ``#spanFacturometroCategoria2`` y, si esos nodos no aparecen, el mismo
fallback AJAX contra ``inicio.aspx/CalcularFacturacion``. Sin MinIO ni
screenshot: el contrato V3 declara que el bot no produce artefactos.

Dos diferencias deliberadas con V2:

- V2 seguía adelante cuando no lograba seleccionar el representado y
  devolvía el facturómetro del CUIT activo. Acá, si la página expone el
  CUIT visible (``#hidCUITContribuyente``) y no coincide con el pedido,
  se corta con ``facturometro_representado_distinto``.
- El portal de Monotributo solo ofrece selector cuando la sesión puede
  representar a más de un CUIT; cuando el representado es el titular,
  la página ya muestra sus datos y no hay nada que seleccionar.
"""
from __future__ import annotations

import time
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

#: Identificadores que publica el servicio de Monotributo.
IDS_FACTUROMETRO = (
    "spanFacturometroMonto",
    "spanFacturometroCategoriaTope",
    "spanFacturometroCategoria2",
)

#: Marcadores de que la página del servicio terminó de renderizar.
SELECTORES_PORTAL_LISTO = (
    "#hidCUITContribuyente",
    "#divFacturometro",
    "div#secContainer",
    "form#form1",
    'select[name="$PropertySelection"]',
    "span.nombre-propio.nombre-activo.pull-right",
)

#: Selectores del representado usados por V2 en este portal.
SELECTORES_REPRESENTADO_V2 = (
    'a.usr[usr="{cuit}"]',
    'a.usr[usr="{digitos}"]',
    'small.pull-right:has-text("{formateado}")',
)

#: Lectura del trío publicado en el DOM.
JS_LECTURA_FACTUROMETRO = """
() => {
    const g = (id) => { const el = document.getElementById(id); return el ? (el.innerText || '').trim() : ''; };
    return {
        monto: g('spanFacturometroMonto'),
        tope: g('spanFacturometroCategoriaTope'),
        categoria: g('spanFacturometroCategoria2'),
    };
}
"""

#: Fallback de V2: el endpoint que usa el propio portal para calcular.
JS_CALCULAR_FACTURACION = """
async () => {
    try {
        const resp = await fetch('inicio.aspx/CalcularFacturacion', {
            method: 'POST',
            headers: {'Content-Type': 'application/json; charset=utf-8'},
            body: '{}'
        });
        const j = await resp.json();
        return {
            visible: j.d.visible,
            pendiente: j.d.pendiente,
            valor: j.d.valor,
            valorTope: j.d.valorTope,
            categoria: j.d.categoria,
            alertaDetalle: j.d.alertaDetalle,
        };
    } catch (e) { return {error: e.message}; }
}
"""


class FacturometroPortal(PortalArca):
    """Lectura del facturómetro de Monotributo del contribuyente."""

    nombre = "facturometro"
    #: Presupuesto para esperar el trío del facturómetro tras navegar.
    presupuesto_lectura_ms = 25_000

    async def leer_facturometro(self, representado_cuit: str) -> dict[str, Any]:
        """Devuelve ``{monto, tope, categoria}`` del representado.

        Falla con ``facturometro_no_disponible`` cuando el organismo no
        publica el trío para ese CUIT (mismo caso que V2 reportaba como
        "Facturómetro no disponible") y con
        ``facturometro_representado_distinto`` si la página quedó sobre
        otro contribuyente.
        """
        if representado_cuit:
            await self.paso(
                "seleccionar_representado",
                self._seleccionar_representado_monotributo(representado_cuit),
            )
        await self.paso("volver_a_inicio", self._volver_a_inicio())
        await self.paso("abrir_facturometro", self._abrir_panel())

        if representado_cuit:
            await self._verificar_representado(representado_cuit)

        lectura = await self._leer_del_dom()
        if not lectura.get("monto"):
            lectura = await self._leer_por_api() or {}

        if not lectura.get("monto"):
            raise TargetUnavailableError(
                "el facturómetro no está disponible para el contribuyente",
                diagnostic_code="facturometro_no_disponible",
            )
        return {
            "monto": str(lectura.get("monto") or "").strip() or None,
            "tope": str(lectura.get("tope") or "").strip() or None,
            "categoria": str(lectura.get("categoria") or "").strip() or None,
        }

    # ------------------------------------------------------------------
    # Pasos del flujo

    async def _seleccionar_representado_monotributo(self, cuit: str) -> None:
        """Selecciona el representado como V2, tolerando su ausencia.

        El portal solo ofrece selector cuando la sesión puede representar
        a más de un CUIT; si el representado es el titular, la página ya
        muestra sus datos y V2 continuaba sin seleccionar nada. Si la
        página del servicio no llegó a renderizar, se corta con
        ``represented_cuit_not_selectable`` (diagnóstico reintentable).
        """
        digits = solo_digitos(cuit)
        if len(digits) != 11:
            raise TargetUnavailableError(
                "CUIT representado inválido",
                diagnostic_code="represented_cuit_invalid",
            )
        if not await self._esperar_portal_listo():
            raise TargetUnavailableError(
                "la página del servicio MONOTRIBUTO no terminó de cargar "
                f"({await self._mapa_de_selectores()})",
                diagnostic_code="represented_cuit_not_selectable",
            )

        formateado = f"{digits[:2]}-{digits[2:10]}-{digits[10]}"
        v2 = tuple(
            self.page.locator(selector.format(cuit=cuit, digitos=digits, formateado=formateado))
            for selector in SELECTORES_REPRESENTADO_V2
        )
        if await self._clickear(v2, total_ms=4_000):
            await self._esperar_carga()
            return
        if await self._seleccionar_en_combo(
            digits
        ) or await self._seleccionar_en_lista(digits, formateado):
            await self._esperar_carga()
            return
        # Sin selector: la página ya corresponde a un contribuyente (caso del
        # titular de la sesión). La coincidencia exacta la valida
        # ``_verificar_representado``.

    async def _verificar_representado(self, cuit: str) -> None:
        """Confirma que la página quedó sobre el CUIT pedido.

        Si el portal no expone el CUIT visible no se puede afirmar nada y
        se continúa (igual que V2); si lo expone y no coincide, se corta
        para no devolver datos de otro contribuyente.
        """
        mostrado = await self._cuit_mostrado()
        if not mostrado:
            return
        if mostrado != solo_digitos(cuit):
            raise TargetUnavailableError(
                "el portal de Monotributo quedó sobre otro CUIT representado",
                diagnostic_code="facturometro_representado_distinto",
            )

    async def _volver_a_inicio(self) -> None:
        """Click en ``Inicio`` si el portal lo ofrece (opcional en V2)."""
        await self._clickear(
            (
                self.page.get_by_role("link", name="Inicio"),
                self.page.locator("a:text-is('Inicio')"),
            ),
            total_ms=5_000,
        )
        await self._esperar(500)

    async def _abrir_panel(self) -> None:
        """Despliega el panel de facturación si el div del facturómetro
        no está a la vista todavía (port del click en V2)."""
        if await self._div_facturometro() == 0:
            return
        await self._clickear(
            (
                self.page.locator("a:has-text('Facturación electrónica')"),
                self.page.locator("button:has-text('Facturación electrónica')"),
            ),
            total_ms=5_000,
        )
        await self._esperar(500)

    # ------------------------------------------------------------------
    # Lectura

    async def _leer_del_dom(self, total_ms: int | None = None) -> dict[str, Any]:
        """Trío publicado en los spans, esperando la hidratación.

        El click en ``Inicio`` vuelve a navegar a ``Inicio.aspx``: el
        facturómetro tarda en reaparecer. V2 esperaba con
        ``wait_for_function``; acá se sondea la lectura hasta que haya
        monto o se agote el presupuesto.
        """
        limite = time.monotonic() + (total_ms or self.presupuesto_lectura_ms) / 1000
        while True:
            if await self._div_facturometro():
                try:
                    datos = await self.page.evaluate(JS_LECTURA_FACTUROMETRO)
                except Exception:
                    datos = None
                if isinstance(datos, dict) and str(datos.get("monto") or "").strip():
                    return datos
            if time.monotonic() >= limite:
                return {}
            await self._esperar(500)

    async def _leer_por_api(self) -> dict[str, Any]:
        """Fallback AJAX de V2 contra ``inicio.aspx/CalcularFacturacion``."""
        try:
            ajax = await self.page.evaluate(JS_CALCULAR_FACTURACION)
        except Exception:
            return {}
        if not isinstance(ajax, dict) or ajax.get("error"):
            return {}
        if ajax.get("visible") and ajax.get("valor"):
            return {
                "monto": ajax.get("valor"),
                "tope": ajax.get("valorTope"),
                "categoria": ajax.get("categoria"),
            }
        return {}

    # ------------------------------------------------------------------
    # Utilidades de página

    async def _div_facturometro(self) -> int:
        try:
            return await self.page.locator("#divFacturometro").count()
        except Exception:
            return 0

    async def _cuit_mostrado(self) -> str:
        """CUIT del contribuyente visible, en dígitos, o ``""``."""
        for selector in (
            "#hidCUITContribuyente",
            "input[id$='hidCUITContribuyente']",
            "input[name$='hidCUITContribuyente']",
        ):
            try:
                localizador = self.page.locator(selector)
                if await localizador.count() == 0:
                    continue
                digitos = solo_digitos(await localizador.first.input_value())
                if len(digitos) == 11:
                    return digitos
            except Exception:
                continue
        for selector in (
            "span.nombre-propio.nombre-activo.pull-right",
            "span.nombre-propio.nombre-activo.pull-righ",
        ):
            try:
                localizador = self.page.locator(selector)
                if await localizador.count() == 0:
                    continue
                digitos = solo_digitos(await self._texto(localizador.first))
                if len(digitos) == 11:
                    return digitos
            except Exception:
                continue
        return await self._cuit_del_html()

    async def _cuit_del_html(self) -> str:
        """Último recurso: el CUIT embebido en el HTML de la página."""
        import re

        try:
            html = await self.page.content()
        except Exception:
            return ""
        patron = re.search(
            r"id=[\"']hidCUITContribuyente[\"'][^>]*value=[\"']([^\"']+)[\"']",
            html or "",
            re.IGNORECASE,
        )
        if patron is None:
            patron = re.search(
                r"value=[\"']([^\"']+)[\"'][^>]*id=[\"']hidCUITContribuyente[\"']",
                html or "",
                re.IGNORECASE,
            )
        if patron is None:
            return ""
        digitos = solo_digitos(patron.group(1))
        return digitos if len(digitos) == 11 else ""

    async def _esperar_portal_listo(self, total_ms: int = 20_000) -> bool:
        """Espera a que la página del servicio muestre sus marcadores.

        ``open_service`` resuelve en ``domcontentloaded``; el portal de
        Monotributo hidrata después. Sin esta espera una sesión válida se
        reporta como representado no seleccionable de forma intermitente.
        """
        limite = time.monotonic() + total_ms / 1000
        while True:
            for selector in SELECTORES_PORTAL_LISTO:
                try:
                    if await self.page.locator(selector).count():
                        return True
                except Exception:
                    continue
            if time.monotonic() >= limite:
                return False
            await self._esperar(500)

    async def _esperar_carga(self) -> None:
        """Espera el re-render posterior a elegir representado (port de V2)."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        await self._esperar(700)


__all__ = [
    "FacturometroPortal",
    "IDS_FACTUROMETRO",
    "SELECTORES_PORTAL_LISTO",
    "SELECTORES_REPRESENTADO_V2",
    "JS_LECTURA_FACTUROMETRO",
    "JS_CALCULAR_FACTURACION",
]
