"""Bot Libro de Sueldos Digital (ARCA) - versión logger.

Descarga las DDJJ / Libro de Sueldos Digital (PDF) de todos los períodos del
rango solicitado. Si un período tiene varias liquidaciones, descarga todas.

Flujo:
1. Login en ARCA (auth.afip.gob.ar) con CUIT + clave del representante.
2. Abrir el servicio "LIBRO DE SUELDOS DIGITAL" (popup).
3. Si el servicio pide elegir representado (la cuenta representa a varios
   CUITs), seleccionarlo en ``IndexContribuyente`` y confirmar con Aceptar.
   Siempre se verifica que el empleador de la sesión sea el representado
   esperado antes de descargar (ver ``_verificar_representado``).
4. Ir a "Liquidaciones y DDJJ" (Periodos.aspx, paginado de a 5 períodos).
5. Por cada período dentro del rango: por cada liquidación, click en Ver LS,
   generar el Libro de Sueldos si hiciera falta, descargar el PDF y Salir.

Estructura de evidencias (ver bots_dev/AGENTS.md):
    ./temp/libro_sueldos_digital_{timestamp}/
        descargas/    PDFs descargados
        html/         HTML por paso
        screenshots/  screenshots por paso
        har/          HAR de toda la sesión
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    open_arca_service,
    wait_page_ready,
)

BOT_NAME = "libro_sueldos_digital"
BOT_DIR = Path(__file__).resolve().parent

LSD_BASE_URL = "https://serviciossegsoc.afip.gob.ar/LibrodeSueldos/app/Contribuyente"
LSD_PERIODOS_URL = f"{LSD_BASE_URL}/LibroSueldos/periodos.aspx"
LSD_SERVICE_NAME = "Libro de Sueldos Digital"

# La paginación del sitio devuelve 5 períodos por página.
PERIODOS_PAGE_SIZE = 10
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

# Selector de representado: cuando la cuenta representa a más de un CUIT, el
# servicio abre en IndexContribuyente.aspx con este combo y el botón Aceptar.
REPRESENTADO_SELECT_SELECTOR = "select#ddlCUIT, select[name='ddlCUIT']"
REPRESENTADO_ACEPTAR_SELECTOR = "input#btnAceptar, input[name='btnAceptar']"
EMPLEADOR_CUIT_SELECTOR = "#ctl00_lblCuit"
EMPLEADOR_DENOMINACION_SELECTOR = "#ctl00_lblEmpleador"

INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PERIODO_YYYYMM_RE = re.compile(r"^(\d{4})(\d{2})$")
PERIODO_LABEL_RE = re.compile(r"^(\d{2})/(\d{4})$")

# Cuenta (en DOM) de períodos con sus liquidaciones y controles Ver LS.
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


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _safe_filename(name: str) -> str:
    safe = INVALID_FILENAME_RE.sub("_", (name or "").strip()).strip(" .")
    return safe or "libro_sueldos_digital.pdf"


def _solo_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _parse_periodo(value: str) -> Optional[tuple[int, int]]:
    """Acepta 'YYYYMM' o 'MM/YYYY' y devuelve (anio, mes)."""
    value = (value or "").strip()
    match = PERIODO_YYYYMM_RE.match(value)
    if match:
        anio, mes = int(match.group(1)), int(match.group(2))
    else:
        match = PERIODO_LABEL_RE.match(value)
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


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (
            self.screenshots_dir,
            self.html_dir,
            self.har_dir,
            self.descargas_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            file_handler = logging.FileHandler(
                self.run_dir / f"{prefix}.log", encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str, *args) -> None:
        self.logger.info(message, *args)

    def log_error(self, message: str, *args) -> None:
        self.logger.error(message, *args)

    async def capture(self, page: Optional[Page], step: str) -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None", step)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        url = getattr(page, "url", "") or ""
        self.logger.info("step=%s name=%s url=%s", self.step, step, url)

        screenshot_path = self.screenshots_dir / f"{prefix}.png"
        html_path = self.html_dir / f"{prefix}.html"

        if url in ("", "about:blank"):
            # En headless no se puede capturar about:blank.
            self.logger.info("screenshot_skipped step=%s url=%s", step, url or "(vacia)")
        else:
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
                self.logger.info("screenshot=%s", screenshot_path)
            except Exception:
                self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            self.logger.info("html=%s", html_path)
        except Exception:
            self.logger.exception("html_failed step=%s", step)


async def execute_step(debug: DebugCapture, page: Optional[Page], step_name: str, action):
    debug.log("step_start=%s url=%s", step_name, getattr(page, "url", ""))
    try:
        result = await action()
        await debug.capture(page, step_name)
        debug.log("step_end=%s status=OK url=%s", step_name, getattr(page, "url", ""))
        return result
    except Exception as exc:
        debug.log_error(
            "step_end=%s status=ERROR url=%s error=%s",
            step_name,
            getattr(page, "url", ""),
            exc,
        )
        await debug.capture(page, f"{step_name}_error")
        raise


async def _iniciar_sesion_arca(
    page: Page,
    debug: DebugCapture,
    cuit_representante: str,
    clave_representante: str,
    error_logs: list[str],
) -> bool:
    login_result = await login_arca(
        page,
        cuit_representante,
        clave_representante,
        error_logs=error_logs,
        click_ver_todos=False,
    )
    await debug.capture(page, "post_login")

    if login_result.captcha_detected:
        debug.log("Captcha detectado")
        await debug.capture(page, "login_captcha")
        if "Captcha detectado" not in error_logs:
            error_logs.append("Captcha detectado")
        return False

    if login_result.password_change_required:
        debug.log("Se requiere cambiar la clave fiscal")
        await debug.capture(page, "login_cambio_clave")
        if "Se requiere cambiar la clave fiscal" not in error_logs:
            error_logs.append("Se requiere cambiar la clave fiscal")
        return False

    if not login_result.success:
        debug.log("Login ARCA fallido errores=%s", error_logs)
        return False

    debug.log("Login ARCA OK url=%s", page.url)
    return True


async def _abrir_servicio_lsd(
    page: Page,
    debug: DebugCapture,
    error_logs: list[str],
) -> Optional[Page]:
    service_page = await open_arca_service(
        page,
        service_name=LSD_SERVICE_NAME,
        service_matchers=(re.compile(r"LIBRO\s+DE\s+SUELDOS", re.I),),
        error_logs=error_logs,
        ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
        service_unavailable_message=(
            f"No se encuentra habilitado el servicio de {LSD_SERVICE_NAME}"
        ),
    )
    if service_page is None:
        debug.log("No se pudo abrir el servicio Libro de Sueldos Digital")
        await debug.capture(page, "servicio_no_habilitado")
        return None

    try:
        await service_page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(service_page)
    debug.log("Servicio abierto url=%s", service_page.url)
    await debug.capture(service_page, "servicio_abierto")
    return service_page


async def _ir_a_liquidaciones(page: Page, debug: DebugCapture) -> None:
    link = page.get_by_role("link", name=re.compile(r"Liquidaciones\s+y\s+DDJJ", re.I))
    await link.first.click(timeout=30_000)
    try:
        await page.wait_for_url(re.compile(r"periodos\.aspx", re.I), timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(page)
    debug.log("Liquidaciones y DDJJ abierto url=%s", page.url)
    await debug.capture(page, "liquidaciones_ddjj")

    # El listado pagina de a 1 período por defecto: seleccionamos 5 por página
    # (postback) igual que el flujo manual, para poder recorrer todo el rango.
    try:
        select = page.locator("#ctl00_ContentPlaceHolder1_regxpagina")
        if await select.count() > 0 and (await select.first.input_value()) != "5":
            debug.log("seleccionando regxpagina=5 (periodos por pagina)")
            try:
                async with page.expect_navigation(timeout=30_000):
                    await select.first.select_option("5")
            except PlaywrightTimeoutError:
                debug.log("postback regxpagina sin navegacion observable")
            await wait_page_ready(page)
    except Exception as exc:
        debug.log("No se pudo seleccionar regxpagina=5: %s", exc)
    await debug.capture(page, "liquidaciones_ddjj_pagina_5")


async def _leer_empleador(page: Page) -> dict[str, str]:
    empleador = {"cuit": "", "denominacion": ""}
    for clave, selector in (
        ("cuit", EMPLEADOR_CUIT_SELECTOR),
        ("denominacion", EMPLEADOR_DENOMINACION_SELECTOR),
    ):
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                empleador[clave] = (await locator.first.inner_text(timeout=5_000)).strip()
        except Exception:
            continue
    return empleador


async def _seleccionar_representado(
    page: Page,
    debug: DebugCapture,
    cuit_representado: str,
    error_logs: list[str],
) -> Optional[str]:
    """Elige el representado cuando el servicio muestra el selector.

    El servicio abre en ``IndexContribuyente.aspx`` (combo ``ddlCUIT`` +
    botón ``Aceptar``) cuando la cuenta representa a más de un contribuyente.
    Con un solo representado el sitio abre directo en el empleador y esta
    función no interviene.

    Devuelve el CUIT elegido (11 dígitos) o ``None`` si no hubo selector.
    """
    selector = page.locator(REPRESENTADO_SELECT_SELECTOR)
    if await selector.count() == 0:
        debug.log("sin selector de representados: el servicio abrió en el empleador de la sesión")
        return None
    await debug.capture(page, "index_contribuyente")

    opciones = await selector.first.evaluate(
        "el => Array.from(el.options).map(o => ({"
        "value: (o.value || '').trim(), "
        "label: (o.textContent || '').trim()"
        "}))"
    ) or []
    esperado = _solo_digitos(cuit_representado)
    elegido = ""
    if esperado:
        for opcion in opciones:
            valor = str(opcion.get("value") or "").strip()
            etiqueta = str(opcion.get("label") or "").strip()
            if _solo_digitos(valor) == esperado or _solo_digitos(etiqueta).startswith(esperado):
                elegido = valor or esperado
                break
        if not elegido:
            disponibles = ", ".join(
                f"{_solo_digitos(opcion.get('value'))} ({str(opcion.get('label') or '').strip()})"
                for opcion in opciones
            )
            mensaje = (
                f"No se encontró el CUIT representado {esperado} en el selector de representados. "
                f"Opciones disponibles: {disponibles or 'sin opciones'}"
            )
            error_logs.append(mensaje)
            debug.log_error(mensaje)
            await debug.capture(page, "representado_no_encontrado")
            return ""
    else:
        # Sin representado explícito se respeta la preselección del sitio.
        elegido = str((opciones[0] if opciones else {}).get("value") or "").strip()
        if not elegido:
            mensaje = "El selector de representados no tiene opciones"
            error_logs.append(mensaje)
            debug.log_error(mensaje)
            await debug.capture(page, "representado_sin_opciones")
            return ""
        debug.log(
            "representado sin solicitud explícita: se elige la primera opción cuit=%s",
            elegido,
        )

    debug.log("seleccionando representado cuit=%s", elegido)
    await selector.first.select_option(value=elegido)
    aceptar = page.locator(REPRESENTADO_ACEPTAR_SELECTOR)
    await aceptar.first.wait_for(state="visible", timeout=20_000)
    await aceptar.first.click(timeout=30_000)
    # Aceptar deja la sesión en el empleador (Index.aspx): el CUIT del
    # encabezado es la señal de que la selección se aplicó.
    try:
        await page.locator(EMPLEADOR_CUIT_SELECTOR).first.wait_for(
            state="visible", timeout=NAV_TIMEOUT_MS
        )
    except PlaywrightTimeoutError:
        pass
    await wait_page_ready(page)
    debug.log("representado seleccionado cuit=%s url=%s", elegido, page.url)
    await debug.capture(page, "representado_seleccionado")
    return _solo_digitos(elegido)


async def _verificar_representado(
    page: Page,
    debug: DebugCapture,
    empleador: dict[str, str],
    cuit_solicitado: str,
    cuit_seleccionado: Optional[str],
    error_logs: list[str],
) -> bool:
    """Confirma que la sesión quedó posicionada en el representado esperado.

    - Con selector: compara con el CUIT confirmado en ``_seleccionar_representado``.
    - Sin selector: compara con el representado solicitado por parámetro.
    - Siempre exige que la cabecera del empleador (``#ctl00_lblCuit``) informe
      un CUIT válido de 11 dígitos; sin eso no se descarga.
    Sin expectativa (sin selector y sin representado) solo deja registrado el
    empleador que informa el sitio.
    """
    actual = _solo_digitos(empleador.get("cuit"))
    if len(actual) != 11:
        mensaje = (
            "No se pudo confirmar el representado en Libro de Sueldos Digital: "
            "la cabecera del empleador no informó un CUIT válido"
        )
        error_logs.append(mensaje)
        debug.log_error(mensaje)
        await debug.capture(page, "representado_no_confirmado")
        return False
    esperado = _solo_digitos(cuit_seleccionado or cuit_solicitado)
    if not esperado:
        debug.log(
            "representado sin solicitud explícita; empleador de sesión cuit=%s",
            actual,
        )
        return True
    if actual != esperado:
        mensaje = (
            f"El empleador de Libro de Sueldos Digital ({actual}) no coincide "
            f"con el representado solicitado ({esperado})"
        )
        error_logs.append(mensaje)
        debug.log_error(mensaje)
        await debug.capture(page, "representado_no_confirmado")
        return False
    debug.log("verificación de representado OK cuit=%s", actual)
    return True


async def _leer_periodos(page: Page) -> list[dict[str, Any]]:
    periodos = await page.evaluate(LISTAR_PERIODOS_JS)
    return periodos or []


async def _recolectar_objetivos(
    page: Page,
    debug: DebugCapture,
    desde: tuple[int, int],
    hasta: tuple[int, int],
) -> list[dict[str, Any]]:
    """Recorre las páginas de períodos y devuelve las liquidaciones a descargar."""
    objetivos: list[dict[str, Any]] = []
    vistos: set[tuple[str, str]] = set()
    firma_anterior: tuple[str, ...] = ()

    for pagina in range(1, MAX_PERIODOS_PAGES + 1):
        url = f"{LSD_PERIODOS_URL}?p={pagina}&t={PERIODOS_PAGE_SIZE}"
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await wait_page_ready(page)
        periodos = await _leer_periodos(page)
        await debug.capture(page, f"periodos_pagina_{pagina}")

        if not periodos:
            debug.log("pagina=%s sin periodos (fin de paginacion)", pagina)
            break

        etiquetas: list[tuple[int, int]] = []
        for item in periodos:
            periodo = _parse_periodo(item.get("label", ""))
            if periodo is None:
                debug.log("periodo_ignorado label=%r", item.get("label"))
                continue
            etiquetas.append(periodo)
            if not (desde <= periodo <= hasta):
                continue

            for liq in item.get("liquidaciones") or []:
                if not liq.get("visible"):
                    debug.log(
                        "liquidacion_sin_verls periodo=%s liquidacion=%s",
                        _periodo_yyyymm(periodo),
                        liq.get("nro"),
                    )
                    continue
                # Los ids del repeater (ctl00..ctl04) se repiten en cada
                # página: deduplicamos por período + número de liquidación.
                clave = (
                    _periodo_yyyymm(periodo),
                    str(liq.get("nro") or liq.get("ctl") or ""),
                )
                if clave in vistos:
                    continue
                vistos.add(clave)
                objetivos.append(
                    {
                        "pagina": pagina,
                        "periodo": _periodo_yyyymm(periodo),
                        "periodo_label": _periodo_label(periodo),
                        "liquidacion": liq.get("nro"),
                        "verls_id": liq.get("id"),
                        "verls_title": liq.get("title"),
                    }
                )

        debug.log(
            "pagina=%s periodos=%s objetivos_acumulados=%s",
            pagina,
            [_periodo_label(p) for p in etiquetas],
            len(objetivos),
        )

        # Si la página repite el contenido de la anterior, llegamos al final
        # del listado (el sitio vuelve a mostrar la última página).
        firma = tuple(sorted(_periodo_yyyymm(p) for p in etiquetas))
        if firma and firma == firma_anterior:
            debug.log("pagina=%s contenido repetido (fin de paginacion)", pagina)
            break
        firma_anterior = firma

        # Los períodos vienen en orden descendente: al pasar el extremo
        # inferior del rango podemos cortar la paginación.
        if etiquetas and min(etiquetas) < desde:
            break

    objetivos.sort(key=lambda o: (o["periodo"], str(o["liquidacion"])))
    return objetivos


async def _esperar_botones_ls(page: Page, timeout_ms: int = LS_PAGE_TIMEOUT_MS) -> str:
    """Espera la página del LS y devuelve 'descargar' o 'generar'."""
    descargar = page.locator(DESCARGAR_BTN_SELECTOR)
    generar = page.locator(GENERAR_BTN_SELECTOR)
    deadline = time.monotonic() + (timeout_ms / 1000)

    while time.monotonic() < deadline:
        if "auth.afip.gob.ar" in (page.url or ""):
            raise RuntimeError("La sesión de ARCA expiró")
        for nombre, locator in (("descargar", descargar), ("generar", generar)):
            try:
                if await locator.count() > 0 and await locator.first.is_visible():
                    return nombre
            except Exception:
                continue
        await asyncio.sleep(0.5)

    raise RuntimeError(
        "No se encontró el botón de descarga/generación del Libro de Sueldos"
    )


async def _volver_con_salir(page: Page, debug: DebugCapture) -> None:
    try:
        salir = page.locator(SALIR_BTN_SELECTOR)
        if await salir.count() > 0 and await salir.first.is_visible():
            await salir.first.click(timeout=15_000)
            await page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as exc:
        debug.log("No se pudo salir de la pantalla del Libro de Sueldos: %s", exc)


async def _descargar_liquidacion(
    page: Page,
    debug: DebugCapture,
    objetivo: dict[str, Any],
    descargas_dir: Path,
) -> Path:
    periodo = objetivo["periodo"]
    liquidacion = str(objetivo.get("liquidacion") or "")
    paso = f"periodo_{periodo}_liq_{liquidacion or 'na'}"

    debug.log(
        "liquidacion_inicio periodo=%s liquidacion=%s verls=%s title=%s",
        periodo,
        liquidacion,
        objetivo["verls_id"],
        objetivo.get("verls_title"),
    )

    await page.goto(
        f"{LSD_PERIODOS_URL}?p={objetivo['pagina']}&t={PERIODOS_PAGE_SIZE}",
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS,
    )
    await wait_page_ready(page)

    verls = page.locator(f"input#{objetivo['verls_id']}")
    await verls.first.wait_for(state="visible", timeout=20_000)
    await verls.first.scroll_into_view_if_needed()
    await verls.first.click()

    try:
        estado = await _esperar_botones_ls(page)
        await debug.capture(page, f"{paso}_ls_abierta")

        if estado == "generar":
            debug.log("generando libro de sueldos periodo=%s liquidacion=%s", periodo, liquidacion)
            generar = page.locator(GENERAR_BTN_SELECTOR)
            await generar.first.click()
            await debug.capture(page, f"{paso}_ls_generando")
            await page.locator(DESCARGAR_BTN_SELECTOR).first.wait_for(
                state="visible", timeout=GENERAR_TIMEOUT_MS
            )
            await debug.capture(page, f"{paso}_ls_generada")

        async with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            await page.locator(DESCARGAR_BTN_SELECTOR).first.click()
        download = await download_info.value

        suggested = download.suggested_filename or f"libro_sueldos_{periodo}_{liquidacion}.pdf"
        destino = descargas_dir / _safe_filename(suggested)
        await download.save_as(str(destino))
        debug.log(
            "descarga_guardada periodo=%s liquidacion=%s archivo=%s",
            periodo,
            liquidacion,
            destino,
        )
        return destino
    finally:
        await _volver_con_salir(page, debug)


async def bot_libro_sueldos_digital_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = "",
    periodo_desde: str = "202501",
    periodo_hasta: str = "202512",
    headless: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {
        "empleador": {"cuit": "", "denominacion": ""},
        "periodos_solicitados": {"desde": periodo_desde, "hasta": periodo_hasta},
        "periodos_encontrados": [],
        "descargas": [],
    }
    error_logs: list[str] = []
    success = False

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(
        "Inicio bot Libro de Sueldos Digital representante=%s representado=%s periodos=%s..%s headless=%s",
        cuit_representante,
        cuit_representado or "(activo)",
        periodo_desde,
        periodo_hasta,
        headless,
    )

    desde = _parse_periodo(periodo_desde)
    hasta = _parse_periodo(periodo_hasta)
    if desde is None or hasta is None or desde > hasta:
        error_logs.append(
            "Rango de períodos inválido: PERIODO_DESDE/PERIODO_HASTA deben ser YYYYMM y desde <= hasta"
        )
        debug.log_error(error_logs[-1])
        return results, error_logs, False

    har_path = debug.har_dir / f"{BOT_NAME}.har"
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    try:
        async with async_playwright() as playwright:
            browser, context, page = await launch_arca_browser_context(
                playwright,
                headless=headless,
                context_kwargs={
                    "accept_downloads": True,
                    "record_har_path": str(har_path),
                    "record_har_content": "embed",
                },
            )
            page.set_default_timeout(NAV_TIMEOUT_MS)
            await debug.capture(page, "inicio")

            login_ok = await execute_step(
                debug,
                page,
                "login_arca",
                lambda: _iniciar_sesion_arca(
                    page, debug, cuit_representante, clave_representante, error_logs
                ),
            )
            if not login_ok:
                debug.log("Fin por login fallido errores=%s", error_logs)
                await context.close()
                await browser.close()
                return results, error_logs, False

            servicio_page = await execute_step(
                debug,
                page,
                "abrir_servicio_lsd",
                lambda: _abrir_servicio_lsd(page, debug, error_logs),
            )
            if servicio_page is None:
                debug.log("Fin por servicio no disponible errores=%s", error_logs)
                await context.close()
                await browser.close()
                return results, error_logs, False

            servicio_page.set_default_timeout(NAV_TIMEOUT_MS)

            # Selección de representado (si el servicio la pide) antes de
            # navegar: con una sola relación el sitio abre directo en el
            # empleador y esta interacción no se ejecuta.
            cuit_seleccionado = await execute_step(
                debug,
                servicio_page,
                "seleccionar_representado",
                lambda: _seleccionar_representado(
                    servicio_page, debug, cuit_representado, error_logs
                ),
            )
            if cuit_seleccionado == "":
                debug.log("Fin por representado no disponible errores=%s", error_logs)
                await context.close()
                await browser.close()
                return results, error_logs, False

            empleador = await _leer_empleador(servicio_page)
            results["empleador"] = empleador
            results["representado"] = {
                "cuit_solicitado": _solo_digitos(cuit_representado),
                "cuit_seleccionado": cuit_seleccionado or "",
                "cuit_empleador": _solo_digitos(empleador.get("cuit")),
                "denominacion": empleador.get("denominacion") or "",
            }
            debug.log(
                "empleador cuit=%s denominacion=%s seleccionado=%s",
                empleador.get("cuit"),
                empleador.get("denominacion"),
                cuit_seleccionado or "(sin selector)",
            )

            if not await _verificar_representado(
                servicio_page,
                debug,
                empleador,
                cuit_representado,
                cuit_seleccionado,
                error_logs,
            ):
                await context.close()
                await browser.close()
                return results, error_logs, False

            await execute_step(
                debug,
                servicio_page,
                "liquidaciones_ddjj",
                lambda: _ir_a_liquidaciones(servicio_page, debug),
            )

            objetivos = await execute_step(
                debug,
                servicio_page,
                "recolectar_periodos",
                lambda: _recolectar_objetivos(servicio_page, debug, desde, hasta),
            )
            results["periodos_encontrados"] = sorted(
                {objetivo["periodo"] for objetivo in objetivos}
            )
            debug.log(
                "objetivos=%s periodos=%s",
                len(objetivos),
                results["periodos_encontrados"],
            )

            if not objetivos:
                error_logs.append(
                    f"No se encontraron liquidaciones para el rango {periodo_desde}..{periodo_hasta}"
                )
                debug.log_error(error_logs[-1])

            for objetivo in objetivos:
                paso = f"descargar_{objetivo['periodo']}_liq_{objetivo['liquidacion'] or 'na'}"
                try:
                    destino = await execute_step(
                        debug,
                        servicio_page,
                        paso,
                        lambda objetivo=objetivo: _descargar_liquidacion(
                            servicio_page, debug, objetivo, debug.descargas_dir
                        ),
                    )
                    results["descargas"].append(
                        {
                            "periodo": objetivo["periodo"],
                            "liquidacion": objetivo["liquidacion"],
                            "archivo": destino.name,
                            "path": str(destino),
                        }
                    )
                except Exception as exc:
                    mensaje = (
                        f"Error al descargar período {objetivo['periodo']} "
                        f"liquidación {objetivo['liquidacion']}: {exc}"
                    )
                    error_logs.append(mensaje)
                    debug.log_error(mensaje)

            await context.close()
            await browser.close()
            browser = None
            context = None

            success = len(results["descargas"]) > 0 and not error_logs
    except Exception as exc:
        mensaje = f"Error en bot libro_sueldos_digital: {exc}"
        error_logs.append(mensaje)
        debug.log_error(mensaje)
        try:
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
        except Exception:
            pass

    debug.log(
        "Fin bot libro_sueldos_digital success=%s descargas=%s errores=%s",
        success,
        len(results["descargas"]),
        error_logs,
    )
    return results, error_logs, success


async def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=BOT_DIR / ".env")
        load_dotenv()
    except Exception:
        pass

    cuit_representante = os.getenv("LSD_CUIT_REPRESENTANTE", "")
    clave = os.getenv("LSD_CLAVE", "")
    cuit_representado = os.getenv("LSD_REPRESENTADO_CUIT", "")
    periodo_desde = os.getenv("PERIODO_DESDE", "202501")
    periodo_hasta = os.getenv("PERIODO_HASTA", "202512")
    headless = os.getenv("LSD_HEADLESS", "False").lower() == "true"

    if not cuit_representante or not clave:
        print("Faltan variables de entorno LSD_CUIT_REPRESENTANTE / LSD_CLAVE")
        return

    results, error_logs, success = await bot_libro_sueldos_digital_logger(
        cuit_representante,
        clave,
        cuit_representado=cuit_representado,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        headless=headless,
    )
    print("OK" if success else "ERROR")
    representado = results.get("representado") or {}
    if representado:
        print(
            "representado:",
            representado.get("cuit_empleador") or "(sin informar)",
            "-",
            representado.get("denominacion") or "(sin denominación)",
            f"(solicitado={representado.get('cuit_solicitado') or '-'}"
            f", seleccionado={representado.get('cuit_seleccionado') or '-'})",
        )
    print("descargas:", len(results.get("descargas", [])))
    for descarga in results.get("descargas", []):
        print(" -", descarga["archivo"])
    if error_logs:
        print("errores:")
        for error in error_logs:
            print(" -", error)


if __name__ == "__main__":
    asyncio.run(main())
