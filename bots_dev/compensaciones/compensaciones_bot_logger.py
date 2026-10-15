import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from playwright.async_api import Locator, Page, async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "compensaciones"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")
COMPENSACIONES_VUE_URL = (
    "https://ctacte.cloud.afip.gob.ar/contribuyente/scripts/vue/consultaCompensacionesAfectaciones/index.html"
)


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (
            self.logs_dir,
            self.screenshots_dir,
            self.html_dir,
            self.har_dir,
            self.descargas_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page: Optional[Page], step: str) -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None", step)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        self.logger.info("step=%s name=%s url=%s", self.step, step, getattr(page, "url", ""))

        screenshot_path = self.screenshots_dir / f"{prefix}.png"
        html_path = self.html_dir / f"{prefix}.html"

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


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "archivo"


def _parse_fecha(fecha: str) -> datetime:
    value = (fecha or "").strip()
    patterns = ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
    for pattern in patterns:
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    raise ValueError(
        f"Fecha invalida '{fecha}'. Formatos aceptados: YYYYMMDD, YYYY/MM/DD, DD/MM/YYYY"
    )


def _normalize_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


async def _safe_close(context, browser) -> None:
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass


async def _find_first_visible(
    candidates: list[Locator],
    wait_timeout_ms: int = 1500,
    visible_timeout_ms: int = 500,
    max_items: int = 10,
) -> Optional[Locator]:
    for candidate_group in candidates:
        try:
            count = await candidate_group.count()
            if count == 0:
                continue
            for idx in range(min(count, max_items)):
                element = candidate_group.nth(idx)
                try:
                    await element.wait_for(state="visible", timeout=wait_timeout_ms)
                except Exception:
                    pass
                if await element.is_visible(timeout=visible_timeout_ms):
                    return element
        except Exception:
            continue
    return None


async def _click_menu_item_by_href_keywords(
    page: Page,
    *,
    keywords: tuple[str, ...],
    debug: DebugCapture,
    max_links: int = 140,
) -> bool:
    keywords_norm = tuple(k.lower() for k in keywords)
    links = page.locator("a[href*='service=partial'][href*='panelMenu.$PartialLink']")
    try:
        count = await links.count()
    except Exception as exc:
        debug.log(f"No se pudieron leer links de menu: {exc}")
        return False

    for idx in range(min(count, max_links)):
        link = links.nth(idx)
        try:
            href = (await link.get_attribute("href") or "").strip()
            normalized_href = unquote(href).replace("+", " ").lower()
            if not all(token in normalized_href for token in keywords_norm):
                continue
            await link.evaluate("el => el.click()")
            await asyncio.sleep(0.8)
            debug.log(f"Menu click por href exitoso: {href}")
            return True
        except Exception:
            continue
    return False


async def _select_representado(
    page: Page,
    cuit_representado: str,
    *,
    error_logs: list[str],
    debug: DebugCapture,
) -> None:
    try:
        combobox = page.locator('select[name="$PropertySelection"]')
        if await combobox.count() == 0:
            debug.log("Selector de representado no encontrado. Asumiendo cuenta unica o ya seleccionada.")
            return

        await combobox.wait_for(state="visible", timeout=3000)
        opciones = combobox.locator("option")
        count = await opciones.count()
        objetivo = (cuit_representado or "").strip()
        objetivo_digits = _normalize_digits(objetivo)
        selected = False
        for i in range(count):
            text = (await opciones.nth(i).text_content() or "").strip()
            value = (await opciones.nth(i).get_attribute("value") or "").strip()
            text_digits = _normalize_digits(text)
            value_digits = _normalize_digits(value)
            if (
                text == objetivo
                or value == objetivo
                or (objetivo_digits and text_digits == objetivo_digits)
                or (objetivo_digits and value_digits == objetivo_digits)
            ):
                await combobox.select_option(value=value)
                selected = True
                debug.log(f"Representado seleccionado: {value or text}")
                break

        if not selected:
            warning = (
                f"No se pudo seleccionar el representado. Verifique el CUIT {cuit_representado}. "
                "Continuando con la seleccion actual."
            )
            error_logs.append(warning)
            debug.log(warning)
    except Exception as exc:
        warning = f"Excepcion al seleccionar representado (continuando): {exc}"
        error_logs.append(warning)
        debug.log(warning)


async def _open_compensaciones_submodule(page: Page, *, error_logs: list[str], debug: DebugCapture) -> bool:
    opened_from_menu = False

    consultas_locator = await _find_first_visible(
        [
            page.get_by_role("cell", name=re.compile(r"Consultas", re.I)),
            page.get_by_role("link", name=re.compile(r"Consultas", re.I)),
            page.get_by_role("button", name=re.compile(r"Consultas", re.I)),
            page.locator("td", has_text=re.compile(r"Consultas", re.I)),
            page.locator("a", has_text=re.compile(r"Consultas", re.I)),
            page.get_by_text(re.compile(r"Consultas", re.I)),
        ]
    )
    if consultas_locator is None:
        clicked = await _click_menu_item_by_href_keywords(
            page,
            keywords=("consultas",),
            debug=debug,
        )
        if not clicked:
            debug.log("No se pudo abrir la seccion Consultas por menu visible; se intentara fallback por URL")
        else:
            opened_from_menu = True
    else:
        try:
            await consultas_locator.click()
            opened_from_menu = True
        except Exception:
            try:
                await consultas_locator.evaluate("el => el.click()")
                opened_from_menu = True
            except Exception:
                clicked = await _click_menu_item_by_href_keywords(
                    page,
                    keywords=("consultas",),
                    debug=debug,
                )
                opened_from_menu = clicked

    await asyncio.sleep(0.7)
    await debug.capture(page, "menu_consultas")

    compensaciones_locator = await _find_first_visible(
        [
            page.get_by_role("cell", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_role("link", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_role("button", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.locator("td", has_text=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.locator("a", has_text=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_text(re.compile(r"Consulta\s+Compensaciones", re.I)),
        ],
        wait_timeout_ms=3000,
    )
    if compensaciones_locator is None:
        clicked = await _click_menu_item_by_href_keywords(
            page,
            keywords=("consulta", "compensaciones", "afectaciones"),
            debug=debug,
        )
        opened_from_menu = opened_from_menu or clicked
    else:
        try:
            await compensaciones_locator.click()
            opened_from_menu = True
        except Exception:
            try:
                await compensaciones_locator.evaluate("el => el.click()")
                opened_from_menu = True
            except Exception:
                clicked = await _click_menu_item_by_href_keywords(
                    page,
                    keywords=("consulta", "compensaciones", "afectaciones"),
                    debug=debug,
                )
                opened_from_menu = opened_from_menu or clicked

    await asyncio.sleep(1)
    await debug.capture(page, "submodulo_compensaciones")

    if not opened_from_menu:
        debug.log("No se pudo abrir menu/submodulo por UI. Fallback directo a URL del modulo.")

    try:
        await page.goto(COMPENSACIONES_VUE_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
    except Exception as exc:
        error_logs.append(f"No se pudo abrir el submodulo de Consulta Compensaciones: {exc}")
        return False

    ready = await _wait_compensaciones_ready(page)
    if not ready:
        error_logs.append("No se pudo cargar la pantalla de Consulta Compensaciones y Afectaciones")
        return False

    await debug.capture(page, "compensaciones_ready")
    return True


async def _wait_compensaciones_ready(page: Page, timeout_ms: int = 35000) -> bool:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            fecha_inputs = page.locator("input[name='fecha']")
            if await fecha_inputs.count() >= 2 and await fecha_inputs.first.is_visible(timeout=300):
                return True
        except Exception:
            pass

        try:
            consultar = page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I))
            if await consultar.count() > 0 and await consultar.first.is_visible(timeout=300):
                return True
        except Exception:
            pass

        await asyncio.sleep(0.4)

    return False


async def _fill_filters_and_consult(
    page: Page,
    *,
    desde: str,
    hasta: str,
    error_logs: list[str],
    debug: DebugCapture,
) -> bool:
    try:
        fecha_inputs = page.locator("input[name='fecha']")
        input_count = await fecha_inputs.count()
        if input_count >= 2:
            await fecha_inputs.nth(0).click()
            await fecha_inputs.nth(0).fill(desde)
            await fecha_inputs.nth(1).click()
            await fecha_inputs.nth(1).fill(hasta)
        else:
            desde_input = await _find_first_visible(
                [
                    page.get_by_role("textbox", name=re.compile(r"Fecha\s+Desde", re.I)),
                    page.locator("input[placeholder*='Desde' i], input[name*='desde' i], input[id*='desde' i]"),
                ],
                wait_timeout_ms=3000,
            )
            hasta_input = await _find_first_visible(
                [
                    page.get_by_role("textbox", name=re.compile(r"Fecha\s+Hasta", re.I)),
                    page.locator("input[placeholder*='Hasta' i], input[name*='hasta' i], input[id*='hasta' i]"),
                ],
                wait_timeout_ms=3000,
            )
            if desde_input is None or hasta_input is None:
                error_logs.append("No se encontraron los campos de fecha en Compensaciones")
                return False

            await desde_input.click()
            await desde_input.fill(desde)
            await hasta_input.click()
            await hasta_input.fill(hasta)

        await debug.capture(page, "filtros_cargados")

        consultar_btn = await _find_first_visible(
            [
                page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I)),
                page.get_by_role("link", name=re.compile(r"CONSULTAR", re.I)),
                page.locator("button:has-text('CONSULTAR')"),
                page.locator("a:has-text('CONSULTAR')"),
            ],
            wait_timeout_ms=3000,
        )
        if consultar_btn is None:
            error_logs.append("No se encontro el boton CONSULTAR en Compensaciones")
            return False

        await consultar_btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1.2)
        await debug.capture(page, "consulta_ejecutada")
        return True
    except Exception as exc:
        error_logs.append(f"Error al completar filtros/consulta en Compensaciones: {exc}")
        return False


async def _find_export_button(scope) -> Optional[Locator]:
    return await _find_first_visible(
        [
            scope.get_by_role("button", name=re.compile(r"Exportar", re.I)),
            scope.get_by_role("link", name=re.compile(r"Exportar", re.I)),
            scope.locator("button:has-text('Exportar')"),
            scope.locator("a:has-text('Exportar')"),
            scope.locator("div.buttons.bt-imp"),
        ],
        wait_timeout_ms=1800,
    )


async def _find_export_option(scope, fmt: str) -> Optional[Locator]:
    fmt_regex = re.compile(rf"\b{re.escape(fmt)}\b", re.I)
    return await _find_first_visible(
        [
            scope.get_by_role("button", name=fmt_regex),
            scope.get_by_role("link", name=fmt_regex),
            scope.locator(f"button:has-text('{fmt}')"),
            scope.locator(f"a:has-text('{fmt}')"),
            scope.get_by_text(fmt_regex),
        ],
        wait_timeout_ms=1200,
    )


async def _download_format(
    page: Page,
    download_dir: Path,
    *,
    fmt: str,
    error_logs: list[str],
    debug: DebugCapture,
) -> Optional[str]:
    try:
        export_btn = await _find_export_button(page)
        if export_btn is None:
            debug.log("Sin boton Exportar; probablemente no hay elementos para descargar")
            return None

        await export_btn.click()
        await asyncio.sleep(0.35)

        export_option = await _find_export_option(page, fmt)
        if export_option is None:
            debug.log(f"Formato no disponible: {fmt}")
            return None

        async with page.expect_download(timeout=30000) as dlinfo:
            await export_option.click()
        download = await dlinfo.value

        filename = _safe_filename(f"compensaciones_{fmt.lower()}_{download.suggested_filename}")
        filepath = download_dir / filename
        await download.save_as(str(filepath))
        debug.log(f"Descarga completada: {filepath}")
        await debug.capture(page, f"descarga_{fmt.lower()}")
        return str(filepath)
    except Exception as exc:
        error_logs.append(f"Error descargando formato {fmt}: {exc}")
        debug.log(f"Error descargando formato {fmt}: {exc}")
        return None


async def bot_compensaciones_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    desde: str,
    hasta: str,
    proxy_request: bool = False,
    headless: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Compensaciones Logger")

    try:
        desde_dt = _parse_fecha(desde)
        hasta_dt = _parse_fecha(hasta)
        if desde_dt > hasta_dt:
            error_logs.append("La fecha 'desde' no puede ser mayor a la fecha 'hasta'")
            return results, error_logs, False
        desde_fmt = desde_dt.strftime("%d/%m/%Y")
        hasta_fmt = hasta_dt.strftime("%d/%m/%Y")
    except ValueError as exc:
        error_logs.append(str(exc))
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None
    download_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    browser = None
    context = None
    active_page: Optional[Page] = None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed",
            )
            page = await context.new_page()
            active_page = page

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            await debug.capture(page, "post_login")
            if not login_result.success:
                debug.log(f"Login fallido: {error_logs}")
                return results, error_logs, False

            page1 = await open_arca_service(
                page,
                service_name="SISTEMA DE CUENTAS",
                service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                service_unavailable_message="No se encuentra habilitado el servicio de Compensaciones",
            )
            if page1 is None:
                debug.log("No se pudo abrir el servicio de Compensaciones")
                return results, error_logs, False

            if page1 is not page and not page.is_closed():
                await page.close()
            active_page = page1
            try:
                await page1.wait_for_load_state("networkidle")
            except Exception:
                pass

            if "auth.afip.gob.ar/contribuyente_/login.xhtml" in (page1.url or ""):
                debug.log("Compensaciones abrio en login AFIP; reintentando autenticacion y apertura del servicio")
                relogin_result = await login_arca(
                    page1,
                    cuit_representante,
                    clave_representante,
                    error_logs=error_logs,
                )
                await debug.capture(page1, "post_relogin")
                if not relogin_result.success:
                    return results, error_logs, False

                if "ctacte.cloud.afip.gob.ar/contribuyente/externo" not in (page1.url or ""):
                    reopened_page = await open_arca_service(
                        page1,
                        service_name="SISTEMA DE CUENTAS",
                        service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                        error_logs=error_logs,
                        ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                        service_unavailable_message="No se encuentra habilitado el servicio de Compensaciones",
                    )
                    if reopened_page is None:
                        debug.log("No se pudo reabrir servicio de Compensaciones luego de relogin")
                        return results, error_logs, False
                    page1 = reopened_page
                    active_page = page1
                try:
                    await page1.wait_for_load_state("networkidle")
                except Exception:
                    pass

            await debug.capture(page1, "servicio_abierto")

            await _select_representado(
                page1,
                cuit_representado,
                error_logs=error_logs,
                debug=debug,
            )
            await debug.capture(page1, "representado")

            submodule_opened = await _open_compensaciones_submodule(
                page1,
                error_logs=error_logs,
                debug=debug,
            )
            if not submodule_opened:
                return results, error_logs, False

            consulted = await _fill_filters_and_consult(
                page1,
                desde=desde_fmt,
                hasta=hasta_fmt,
                error_logs=error_logs,
                debug=debug,
            )
            if not consulted:
                return results, error_logs, False

            for fmt in ("XLS", "CSV", "PDF"):
                path = await _download_format(
                    page1,
                    download_dir,
                    fmt=fmt,
                    error_logs=error_logs,
                    debug=debug,
                )
                if path:
                    results["archivos"].append(
                        {"seccion": "compensaciones", "formato": fmt, "path": path}
                    )

            await debug.capture(page1, "fin")

    except Exception as exc:
        error_logs.append(f"Error global en Compensaciones: {exc}")
        debug.log(f"Error critico: {exc}")
        success = False
        if active_page is not None:
            try:
                await debug.capture(active_page, "error_global")
            except Exception:
                pass
    finally:
        await _safe_close(context, browser)

    return results, error_logs, success


async def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    cuit_representante = (
        os.getenv("COMPENSACIONES_CUIT_REPRESENTANTE", "")
        or os.getenv("SCT_COMPENSACIONES_CUIT_REPRESENTANTE", "")
        or os.getenv("CUIT_REPRESENTANTE_SCT_COMPENSACIONES", "")
    )
    clave_representante = (
        os.getenv("COMPENSACIONES_CLAVE_REPRESENTANTE", "")
        or os.getenv("SCT_COMPENSACIONES_CLAVE_REPRESENTANTE", "")
        or os.getenv("CLAVE_REPRESENTANTE_SCT_COMPENSACIONES", "")
    )
    cuit_representado = (
        os.getenv("COMPENSACIONES_CUIT_REPRESENTADO", "")
        or os.getenv("SCT_COMPENSACIONES_CUIT_REPRESENTADO", "")
        or os.getenv("CUIT_REPRESENTADO_SCT_COMPENSACIONES", "")
        or cuit_representante
    )
    desde = (
        os.getenv("COMPENSACIONES_DESDE", "")
        or os.getenv("SCT_COMPENSACIONES_DESDE", "")
        or os.getenv("DESDE_COMPENSACIONES", "")
    )
    hasta = (
        os.getenv("COMPENSACIONES_HASTA", "")
        or os.getenv("SCT_COMPENSACIONES_HASTA", "")
        or os.getenv("HASTA_COMPENSACIONES", "")
    )
    headless = (
        os.getenv("COMPENSACIONES_HEADLESS", "")
        or os.getenv("SCT_COMPENSACIONES_HEADLESS", "")
        or os.getenv("SCT_HEADLESS", "True")
    ).lower() in ("true", "1", "yes")

    if cuit_representante and clave_representante and cuit_representado and desde and hasta:
        res = await bot_compensaciones_logger(
            cuit_representante=cuit_representante,
            clave_representante=clave_representante,
            cuit_representado=cuit_representado,
            desde=desde,
            hasta=hasta,
            headless=headless,
        )
        print(res)


if __name__ == "__main__":
    asyncio.run(main())
