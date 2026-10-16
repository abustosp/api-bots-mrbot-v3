"""Portal Libro de Sueldos Digital: descarga de DDJJ (PDF) por liquidación.

Port de la versión dev
(``bots_dev/libro_sueldos_digital/libro_sueldos_digital_bot.py``). La sesión
fiscal y la apertura del servicio las hace ``ArcaSession``; esta clase
implementa la navegación por ``Liquidaciones y DDJJ``, la lectura de los
períodos paginados y la descarga del Libro de Sueldos Digital de cada
liquidación. Sin capturas ni HAR: solo acciones del portal.

El portal pagina de a 1 período por defecto: se selecciona 5 por página
(postback) para poder recorrer todo el rango solicitado. Si un período tiene
varias liquidaciones se descargan todas.
"""
from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError

from .base import PortalArca, solo_digitos

#: La paginación del sitio devuelve 5 períodos por página.
PERIODOS_PAGE_SIZE = 10
#: Tope defensivo de páginas del listado de períodos.
MAX_PERIODOS_PAGES = 60

NAV_TIMEOUT_MS = 60_000
LS_PAGE_TIMEOUT_MS = 90_000
GENERAR_TIMEOUT_MS = 180_000
DOWNLOAD_TIMEOUT_MS = 180_000

DESCARGAR_BTN_SELECTOR = (
    "input#ctl00_ContentPlaceHolder1_btnDescargarLibro, "
    "input[value*='DESCARGAR LIBRO SUELDOS' i]"
)
GENERAR_BTN_SELECTOR = (
    "input#ctl00_ContentPlaceHolder1_btnReenviarVentanilla2, "
    "input[value*='GENERAR LIBRO SUELDOS' i]"
)
SALIR_BTN_SELECTOR = "input#ctl00_ContentPlaceHolder1_btnSalir"

#: Selector de representado: con más de un CUIT representado el servicio abre
#: en ``IndexContribuyente.aspx`` con este combo y el botón Aceptar.
REPRESENTADO_SELECT_SELECTOR = "select#ddlCUIT, select[name='ddlCUIT']"
REPRESENTADO_ACEPTAR_SELECTOR = "input#btnAceptar, input[name='btnAceptar']"
EMPLEADOR_CUIT_SELECTOR = "#ctl00_lblCuit"
EMPLEADOR_DENOMINACION_SELECTOR = "#ctl00_lblEmpleador"

PERIODO_YYYYMM_RE = re.compile(r"^(\d{4})(\d{2})$")
PERIODO_LABEL_RE = re.compile(r"^(\d{2})/(\d{4})$")

#: Cuenta (en DOM) de períodos con sus liquidaciones y controles Ver LS.
LISTAR_PERIODOS_JS = """
() => {
  const periodos = [];
  const labels = document.querySelectorAll(
    'span[id^="ctl00_ContentPlaceHolder1_rptPadre_"][id$="_Label2"]'
  );
  labels.forEach((lbl) => {
    const idx = ((lbl.id || '').match(/rptPadre_(ctl\\d+)_Label2$/) || [])[1];
    if (!idx) return;
    const liqs = [];
    const verls = document.querySelectorAll(
      'input[id^="ctl00_ContentPlaceHolder1_rptPadre_' + idx +
      '_uc_Liquidaciones_rptLiquidaciones_"][id$="_VerLS"]'
    );
    verls.forEach((inp) => {
      const m = (inp.id || '').match(/rptLiquidaciones_(ctl\\d+)_VerLS$/);
      const fila = inp.closest('tr');
      const celdaNro = fila ? fila.querySelector('td[nroliq]') : null;
      const nroTxt = celdaNro ? (celdaNro.getAttribute('nroliq') || '').trim() : '';
      liqs.push({
        nro: nroTxt || (m ? m[1].replace('ctl', '') : ''),
        ctl: m ? m[1] : '',
        id: inp.id,
        title: inp.title || inp.value || '',
        visible: inp.offsetParent !== null,
      });
    });
    periodos.push({
      label: (lbl.innerText || '').trim(),
      element_id: lbl.id,
      visible: lbl.offsetParent !== null,
      liquidaciones: liqs,
    });
  });
  return periodos;
}
"""


def _parse_periodo(valor: str) -> tuple[int, int] | None:
    """Acepta ``YYYYMM`` o ``MM/YYYY`` y devuelve (anio, mes)."""
    valor = (valor or "").strip()
    match = PERIODO_YYYYMM_RE.match(valor)
    if match:
        anio, mes = int(match.group(1)), int(match.group(2))
    else:
        match = PERIODO_LABEL_RE.match(valor)
        if not match:
            return None
        mes, anio = int(match.group(1)), int(match.group(2))
    if not 1 <= mes <= 12:
        return None
    return anio, mes


def _periodo_yyyymm(periodo: tuple[int, int]) -> str:
    return f"{periodo[0]:04d}{periodo[1]:02d}"


def _periodo_label(periodo: tuple[int, int]) -> str:
    return f"{periodo[1]:02d}/{periodo[0]}"


class LibroSueldosDigitalPortal(PortalArca):
    """Acciones del servicio Libro de Sueldos Digital."""

    nombre = "libro_sueldos_digital"

    # -------------------------------------------------------------- pasos --

    async def abrir_liquidaciones_y_ddjj(self) -> None:
        """Abre ``Liquidaciones y DDJJ`` y deja el listado con 5 por página."""
        await self.paso(
            "abrir_liquidaciones",
            self._clickear(
                (
                    self.page.get_by_role(
                        "link", name=re.compile(r"Liquidaciones\s+y\s+DDJJ", re.I)
                    ),
                    self.page.locator("a:has-text('LIQUIDACIONES Y DDJJ')"),
                    self.page.locator("a:has-text('Liquidaciones y DDJJ')"),
                ),
                total_ms=20_000,
            ),
        )
        with contextlib.suppress(Exception):
            await self.page.wait_for_url(
                re.compile(r"periodos\.aspx", re.I), timeout=NAV_TIMEOUT_MS
            )
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)

        # El listado pagina de a 1 período por defecto: seleccionamos 5 por
        # página (postback) igual que el flujo manual para recorrer el rango.
        try:
            select = self.page.locator("#ctl00_ContentPlaceHolder1_regxpagina")
            if await select.count() > 0 and (await select.first.input_value()) != "5":
                with contextlib.suppress(Exception):
                    async with self.page.expect_navigation(timeout=30_000):
                        await select.first.select_option("5")
                with contextlib.suppress(Exception):
                    await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass

    async def seleccionar_representado(self, cuit: str | None = None) -> str:
        """Elige el representado cuando el servicio pide seleccionar CUIT.

        Con más de un representado el servicio abre en
        ``IndexContribuyente.aspx`` (combo ``ddlCUIT`` + botón ``Aceptar``):
        se elige el CUIT pedido —o el primero, la preselección del sitio, si
        el pedido no lo explicita— y se confirma antes de navegar.

        Devuelve el CUIT elegido (11 dígitos) o ``""`` cuando el servicio
        abrió directamente en el empleador de la sesión.
        """
        selector = self.page.locator(REPRESENTADO_SELECT_SELECTOR)
        if await selector.count() == 0:
            return ""

        opciones = await selector.first.evaluate(
            "el => Array.from(el.options).map(o => ({"
            "value: (o.value || '').trim(), "
            "label: (o.textContent || '').trim()"
            "}))"
        ) or []
        esperado = solo_digitos(cuit or "")
        elegido = ""
        if esperado:
            for opcion in opciones:
                valor = str(opcion.get("value") or "").strip()
                etiqueta = str(opcion.get("label") or "").strip()
                if (
                    solo_digitos(valor) == esperado
                    or solo_digitos(etiqueta).startswith(esperado)
                ):
                    elegido = valor or esperado
                    break
            if not elegido:
                raise TargetUnavailableError(
                    "el CUIT representado solicitado no está disponible en el selector",
                    diagnostic_code="represented_cuit_not_selectable",
                )
        else:
            elegido = str(
                (opciones[0] if opciones else {}).get("value") or ""
            ).strip()
            if not elegido:
                raise TargetUnavailableError(
                    "el selector de representados no tiene opciones",
                    diagnostic_code="represented_cuit_not_selectable",
                )

        await selector.first.select_option(value=elegido)
        confirmado = await self.paso(
            "confirmar_representado",
            self._clickear(
                (self.page.locator(REPRESENTADO_ACEPTAR_SELECTOR),),
                total_ms=10_000,
            ),
        )
        if not confirmado:
            raise TargetUnavailableError(
                "no se pudo confirmar el representado seleccionado",
                diagnostic_code="represented_cuit_not_confirmed",
            )
        # Aceptar deja la sesión en el empleador (Index.aspx): el CUIT del
        # encabezado confirma que la selección se aplicó.
        with contextlib.suppress(Exception):
            await self.page.locator(EMPLEADOR_CUIT_SELECTOR).first.wait_for(
                state="visible", timeout=NAV_TIMEOUT_MS
            )
        return solo_digitos(elegido)

    async def empleador_actual(self) -> dict[str, str]:
        """CUIT y denominación del empleador que muestra la sesión."""
        empleador = {"cuit": "", "denominacion": ""}
        for clave, selector in (
            ("cuit", EMPLEADOR_CUIT_SELECTOR),
            ("denominacion", EMPLEADOR_DENOMINACION_SELECTOR),
        ):
            try:
                locator = self.page.locator(selector)
                if await locator.count() > 0:
                    empleador[clave] = self._limpiar(
                        await locator.first.inner_text(timeout=5_000)
                    )
            except Exception:
                continue
        return empleador

    async def listar_liquidaciones(
        self, desde: str, hasta: str
    ) -> list[dict[str, Any]]:
        """Períodos del rango con todas sus liquidaciones descargables."""
        rango_desde = _parse_periodo(desde)
        rango_hasta = _parse_periodo(hasta)
        if rango_desde is None or rango_hasta is None or rango_desde > rango_hasta:
            raise TargetUnavailableError(
                "rango de períodos inválido para Libro de Sueldos Digital",
                diagnostic_code="lsd_period_range_invalid",
            )

        objetivos: list[dict[str, Any]] = []
        vistos: set[tuple[str, str]] = set()
        firma_anterior: tuple[str, ...] = ()
        base_url = await self._url_periodos()

        for pagina in range(1, MAX_PERIODOS_PAGES + 1):
            await self.paso(
                "listar_periodos",
                self.abrir_url(f"{base_url}?p={pagina}&t={PERIODOS_PAGE_SIZE}"),
            )
            datos = await self.page.evaluate(LISTAR_PERIODOS_JS) or []

            if not datos:
                break

            etiquetas: list[tuple[int, int]] = []
            for item in datos:
                periodo = _parse_periodo(item.get("label", ""))
                if periodo is None:
                    continue
                etiquetas.append(periodo)
                if not (rango_desde <= periodo <= rango_hasta):
                    continue
                for liquidacion in item.get("liquidaciones") or []:
                    if not liquidacion.get("visible"):
                        continue
                    # Los ids del repeater (ctl00..ctl04) se repiten en cada
                    # página: deduplicamos por período + nro de liquidación.
                    clave = (
                        _periodo_yyyymm(periodo),
                        str(liquidacion.get("nro") or liquidacion.get("ctl") or ""),
                    )
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    objetivos.append(
                        {
                            "pagina": pagina,
                            "periodo": _periodo_yyyymm(periodo),
                            "periodo_label": _periodo_label(periodo),
                            "liquidacion": liquidacion.get("nro"),
                            "verls_id": liquidacion.get("id"),
                            "verls_title": liquidacion.get("title"),
                        }
                    )

            firma = tuple(sorted(_periodo_yyyymm(p) for p in etiquetas))
            if firma and firma == firma_anterior:
                break
            firma_anterior = firma

            if etiquetas and min(etiquetas) < rango_desde:
                break

        objetivos.sort(key=lambda o: (o["periodo"], str(o["liquidacion"])))
        return objetivos

    async def descargar_libro(self, *, objetivo: dict[str, Any], destino: Path) -> Path:
        """Descarga el LS Digital (PDF) de una liquidación de un período."""
        destino = Path(destino)
        base_url = await self._url_periodos()
        pagina = objetivo.get("pagina") or 1

        await self.paso(
            "abrir_periodo",
            self.abrir_url(f"{base_url}?p={pagina}&t={PERIODOS_PAGE_SIZE}"),
        )
        verls = self.page.locator(f"input#{objetivo['verls_id']}")
        try:
            await verls.first.wait_for(state="visible", timeout=20_000)
        except Exception as exc:
            raise TargetUnavailableError(
                "la liquidación solicitada no está disponible en el listado",
                diagnostic_code="lsd_liquidation_not_found",
            ) from exc
        with contextlib.suppress(Exception):
            await verls.first.scroll_into_view_if_needed(timeout=3_000)
        await self.paso("abrir_liquidacion", verls.first.click())

        try:
            estado = await self._esperar_botones_ls()
            if estado == "generar":
                generar = self.page.locator(GENERAR_BTN_SELECTOR)
                await self.paso("generar_libro", generar.first.click())
                await self.paso(
                    "esperar_libro_generado",
                    self.page.locator(DESCARGAR_BTN_SELECTOR).first.wait_for(
                        state="visible", timeout=GENERAR_TIMEOUT_MS
                    ),
                )
            await self.capturar_descarga(
                destino,
                self.page.locator(DESCARGAR_BTN_SELECTOR).first.click,
                espera_ms=DOWNLOAD_TIMEOUT_MS,
            )
        finally:
            await self._salir()
        return destino

    # ------------------------------------------------------------ apoyo --

    async def _url_periodos(self) -> str:
        url = await self._url()
        if "periodos.aspx" in url.lower():
            return url.split("?", 1)[0]
        return (
            "https://serviciossegsoc.afip.gob.ar/LibrodeSueldos/app/"
            "Contribuyente/LibroSueldos/periodos.aspx"
        )

    @staticmethod
    def _limpiar(texto: Any) -> str:
        return _ESPACIOS.sub(" ", str(texto or "")).strip()

    async def _esperar_botones_ls(self, timeout_ms: int = LS_PAGE_TIMEOUT_MS) -> str:
        """Espera la página del LS y devuelve ``descargar`` o ``generar``."""
        descargar = self.page.locator(DESCARGAR_BTN_SELECTOR)
        generar = self.page.locator(GENERAR_BTN_SELECTOR)
        limite = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < limite:
            if "auth.afip.gob.ar" in await self._url():
                raise TargetUnavailableError(
                    "la sesión fiscal expiró",
                    diagnostic_code="lsd_session_expired",
                )
            for nombre, locator in (("descargar", descargar), ("generar", generar)):
                try:
                    if await locator.count() > 0 and await locator.first.is_visible():
                        return nombre
                except Exception:
                    continue
            await self._esperar(500)
        raise TargetUnavailableError(
            "el Libro de Sueldos no quedó disponible para la liquidación",
            diagnostic_code="lsd_ls_page_missing",
        )

    async def _salir(self) -> None:
        with contextlib.suppress(Exception):
            salir = self.page.locator(SALIR_BTN_SELECTOR)
            if await salir.count() > 0 and await salir.first.is_visible():
                await salir.first.click(timeout=15_000)
                with contextlib.suppress(Exception):
                    await self.page.wait_for_load_state(
                        "domcontentloaded", timeout=NAV_TIMEOUT_MS
                    )


_ESPACIOS = re.compile(r"\s+")
