from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from playwright.async_api import Browser, BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, async_playwright

from shared.arca_login import launch_arca_browser_context, login_arca, open_arca_service, wait_page_ready


BOT_NAME = "carga_931"
PERIODO_OBJETIVO = "2026/08"
PERIODO = "08/2026"
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "descargas" / "carga-931-xubio"
CLAVES_PATH = INPUT_DIR / "claves.xlsx"
TEMP_DIR = BASE_DIR / "temp"
HEADLESS = os.getenv("CARGA_931_HEADLESS", "false").lower() in {"1", "true", "yes"}
DEFAULT_TIMEOUT = 30_000
VALIDATION_TIMEOUT = 180_000


@dataclass
class Contribuyente:
    cuit_inicio: str
    clave: str
    cuit_representado: str
    razon_social: str
    path_txt: str
    txt: str
    jurisdiccion: str

    @property
    def txt_path(self) -> Path:
        candidate = Path(self.path_txt) / self.txt
        if candidate.exists():
            return candidate
        return INPUT_DIR / self.txt

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", self.razon_social).strip("_") or self.cuit_representado


class DebugCapture:
    def __init__(self) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = TEMP_DIR / f"{BOT_NAME}_{run_id}"
        self.descargas_dir = self.run_dir / "descargas"
        self.html_dir = self.run_dir / "html"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.har_dir = self.run_dir / "har"
        for directory in (self.descargas_dir, self.html_dir, self.screenshots_dir, self.har_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.step = 0
        self.logger = logging.getLogger(f"{BOT_NAME}.{run_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = logging.FileHandler(self.run_dir / f"{BOT_NAME}.log")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

    def log(self, message: str, *args: Any) -> None:
        self.logger.info(message, *args)

    def error(self, message: str, *args: Any) -> None:
        self.logger.error(message, *args)

    async def capture(self, page: Page | None, name: str) -> None:
        if page is None:
            return
        self.step += 1
        prefix = f"{self.step:03d}_{re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_')}"
        try:
            await page.screenshot(path=str(self.screenshots_dir / f"{prefix}.png"), full_page=True)
        except Exception:
            pass
        try:
            (self.html_dir / f"{prefix}.html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            pass


async def execute_step(debug: DebugCapture, page: Page | None, name: str, action):
    debug.log("step_start=%s url=%s", name, getattr(page, "url", ""))
    try:
        result = await action()
        await debug.capture(page, name)
        debug.log("step_end=%s status=OK url=%s", name, getattr(page, "url", ""))
        return result
    except Exception as exc:
        debug.error("step_end=%s status=ERROR url=%s error=%s", name, getattr(page, "url", ""), exc)
        await debug.capture(page, f"{name}_error")
        raise


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _header(value: Any) -> str:
    return re.sub(r"\s+", " ", _value(value).lower())


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _related_workbooks(contribuyente: Contribuyente) -> tuple[list[str], list[str]]:
    target = _compact(contribuyente.razon_social)
    input_reports = [
        str(path)
        for path in INPUT_DIR.glob("*202608*.xlsx")
        if target in _compact(path.stem)
    ]
    control_reports = [
        str(path)
        for path in (INPUT_DIR / "reportes").glob("*202608*.xlsx")
        if target in _compact(path.stem)
    ]
    return input_reports, control_reports


def leer_contribuyentes(path: Path) -> list[Contribuyente]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    headers = [_header(value) for value in next(rows)]
    required = {
        "cuit inicio": "cuit_inicio",
        "clave de afip": "clave",
        "cuit representado": "cuit_representado",
        "razón social": "razon_social",
        "path txt": "path_txt",
        "txt": "txt",
        "jurisdiccion": "jurisdiccion",
    }
    indexes: dict[str, int] = {}
    for expected, field_name in required.items():
        if expected not in headers:
            raise ValueError(f"Falta la columna '{expected}' en {path}")
        indexes[field_name] = headers.index(expected)

    contributors = []
    for row in rows:
        values = [_value(row[indexes[field]]) for field in indexes]
        if not any(values):
            continue
        contributors.append(Contribuyente(*values))
    workbook.close()
    return contributors


def _first_visible(page: Page, patterns: list[str]):
    return page.locator(", ".join(patterns)).first


async def click_confirm(page: Page) -> None:
    visible_dialog = page.locator(".ui-dialog:visible")
    confirm = visible_dialog.get_by_role("button", name="Confirmar", exact=True)
    if await confirm.count() and await confirm.last.is_visible():
        await confirm.last.click()
        await page.wait_for_timeout(1_000)
        return
    modal_confirm = page.locator(".modal.in:visible").get_by_role("button", name="Confirmar", exact=True)
    if await modal_confirm.count() and await modal_confirm.last.is_visible():
        await modal_confirm.last.click()
        await page.wait_for_timeout(1_000)
        return
    for locator in (visible_dialog.locator("input[type='button'], input[type='submit']"),):
        if await locator.count() and await locator.last.is_visible():
            await locator.last.click()
            await page.wait_for_timeout(1_000)
            return


async def esperar_sin_overlay(page: Page) -> None:
    period_rows = page.locator("td.labelPeriodo span")
    if await period_rows.count() and await period_rows.first.is_visible():
        return
    for selector in ("#ctl00_ContentPlaceHolder1_overlay", ".overlay"):
        overlay = page.locator(selector)
        if await overlay.count():
            try:
                await overlay.first.wait_for(state="hidden", timeout=20_000)
            except PlaywrightTimeoutError:
                raise RuntimeError("ARCA no finalizó la carga de la pantalla de períodos")


async def localizar_periodo_objetivo(page: Page):
    periods = page.locator("td.labelPeriodo span")
    accepted_values = {PERIODO_OBJETIVO, PERIODO}
    for index in range(await periods.count()):
        candidate = periods.nth(index)
        value = re.sub(r"\s+", "", await candidate.inner_text())
        if value in accepted_values and await candidate.is_visible():
            return candidate
    return None


async def eliminar_periodo_si_existe(page: Page) -> None:
    await esperar_sin_overlay(page)
    visible_dialog = page.locator(".ui-dialog:visible")
    if await visible_dialog.count():
        dialog_text = await visible_dialog.inner_text()
        raise RuntimeError(f"ARCA dejó un diálogo abierto antes de eliminar el período: {dialog_text.strip()}")
    period = await localizar_periodo_objetivo(page)
    if period is None:
        return
    await period.click()
    await page.wait_for_timeout(1_000)
    selected_period = re.sub(r"\s+", "", await period.inner_text())
    if selected_period not in {PERIODO_OBJETIVO, PERIODO}:
        raise RuntimeError(
            f"No se pudo verificar el período objetivo {PERIODO_OBJETIVO}; "
            f"la pantalla seleccionó '{selected_period}'"
        )
    while True:
        delete_liquidation = page.get_by_role("button", name=re.compile(r"Eliminar la liquidación", re.I)).first
        if not await delete_liquidation.is_visible(timeout=1_500):
            break
        await delete_liquidation.click()
        await click_confirm(page)
        await page.wait_for_timeout(1_000)
    selected_period = re.sub(r"\s+", "", await period.inner_text())
    if selected_period not in {PERIODO_OBJETIVO, PERIODO}:
        raise RuntimeError(f"Se canceló el borrado: no se verificó {PERIODO_OBJETIVO}")
    delete_period = page.get_by_role("button", name=re.compile(r"Borrar Per[ií]odo", re.I)).first
    if await delete_period.is_visible(timeout=2_000):
        await delete_period.click()
        await click_confirm(page)
        await page.wait_for_timeout(1_500)


async def crear_periodo_y_liquidacion(page: Page, contribuyente: Contribuyente) -> None:
    liquidaciones_tab = page.get_by_role("link", name=re.compile(r"Liquidaciones\s+y\s+DDJJ", re.I))
    if await liquidaciones_tab.count() == 0:
        liquidaciones_tab = page.get_by_text(re.compile(r"Liquidaciones\s+y\s+DDJJ", re.I), exact=True)
    await liquidaciones_tab.first.click()
    await page.wait_for_timeout(1_500)
    await eliminar_periodo_si_existe(page)
    await page.get_by_role("link", name=re.compile(r"Nuevo Per[ií]odo", re.I)).click()
    period_input = page.get_by_role("textbox", name="Formato mm/aaaa", exact=True)
    await period_input.fill(PERIODO)
    # Prima individual y costo de emisión se completan automáticamente vía
    # proponePrima() (AJAX a Periodos_getDatos.aspx); no requieren carga manual.
    await page.wait_for_timeout(1_500)
    await page.get_by_role("button", name="Agregar", exact=True).click()
    await esperar_sin_overlay(page)
    await page.wait_for_timeout(1_000)
    duplicate_message = page.locator(".addPeriodoError").filter(
        has_text=re.compile(r"El per[ií]odo ingresado ya existe", re.I)
    ).first
    if await duplicate_message.is_visible(timeout=1_000):
        cancel = page.get_by_role("button", name="CANCELAR", exact=True)
        if await cancel.count() and await cancel.last.is_visible():
            await cancel.last.click()
        await page.reload(wait_until="domcontentloaded")
        await wait_page_ready(page, timeout_ms=20_000)
        await esperar_sin_overlay(page)
    new_period = await localizar_periodo_objetivo(page)
    if new_period is None:
        raise RuntimeError(f"No se encontró el período objetivo {PERIODO_OBJETIVO} en td.labelPeriodo")
    await new_period.click()
    await page.get_by_role("link", name=re.compile(r"Agregar Liquidaci[oó]n", re.I)).click()

    liquidation_input = page.locator("input[id$='TextBoxLiquidacion']").first
    await liquidation_input.fill("1")
    jurisdiction = page.locator("select[id$='DropDownJurisdicciones']").first
    options = await jurisdiction.locator("option").evaluate_all(
        "options => options.map(option => ({value: option.value, text: option.textContent.trim()}))"
    )
    target = re.sub(r"[^a-z0-9]", "", contribuyente.jurisdiccion.lower())
    selected = next(
        (option for option in options if target in re.sub(r"[^a-z0-9]", "", option["text"].lower())),
        None,
    )
    if selected is None:
        available = ", ".join(f"{item['value']}={item['text']}" for item in options)
        raise RuntimeError(f"No se encontró jurisdicción '{contribuyente.jurisdiccion}'. Opciones: {available}")
    await jurisdiction.select_option(selected["value"])
    await page.get_by_role("button", name="Agregar", exact=True).click()


async def cargar_y_validar(page: Page, txt_path: Path) -> None:
    await page.get_by_role("button", name=re.compile(r"Importar desde archivo", re.I)).click()
    file_input = page.locator("input[type='file']").first
    await file_input.set_input_files(str(txt_path))
    await page.get_by_text("Subir", exact=True).click()
    await page.get_by_role("button", name=re.compile(r"VALIDAR", re.I)).click()

    deadline = asyncio.get_running_loop().time() + VALIDATION_TIMEOUT / 1000
    while asyncio.get_running_loop().time() < deadline:
        status_locator = page.locator(".mensajeEstado, #ctl00_ContentPlaceHolder1_MensajeEstado").first
        status = (await status_locator.inner_text()).strip().upper() if await status_locator.count() else ""
        if status == "VALIDA":
            return
        if status == "INVALIDA":
            raise RuntimeError("ARCA informó que la liquidación es inválida")
        body = (await page.locator("body").inner_text()).upper()
        if re.search(r"ERROR|RECHAZAD", body) and not re.search(r"VALIDANDO|VERIFICANDO", body):
            raise RuntimeError("ARCA informó un error durante la validación")
        await page.wait_for_timeout(2_000)
    raise TimeoutError(f"La validación de {txt_path.name} superó {VALIDATION_TIMEOUT // 1000} segundos")


async def descargar_borrador(page: Page, debug: DebugCapture) -> dict[str, str]:
    await page.get_by_role("button", name=re.compile(r"Inicio", re.I)).click()
    borrador_button = page.get_by_role("button", name=re.compile(r"F931 Borrador", re.I))
    borrador_page = page
    try:
        async with page.expect_popup(timeout=5_000) as popup_info:
            await borrador_button.click()
        borrador_page = await popup_info.value
    except PlaywrightTimeoutError:
        pass
    await borrador_page.wait_for_timeout(3_000)
    await wait_page_ready(borrador_page, timeout_ms=20_000)

    result = {"url": borrador_page.url, "html": ""}
    try:
        result["html"] = await borrador_page.locator("body").inner_text()
    except Exception:
        pass
    for locator in (
        borrador_page.get_by_role("link", name=re.compile(r"Descargar|Borrador", re.I)),
        borrador_page.get_by_role("button", name=re.compile(r"Descargar|Borrador", re.I)),
    ):
        if await locator.count() == 0 or not await locator.first.is_visible():
            continue
        try:
            async with borrador_page.expect_download(timeout=10_000) as download_info:
                await locator.first.click()
            download = await download_info.value
            destination = debug.descargas_dir / f"F931_Borrador_{datetime.now():%Y%m%d_%H%M%S}{Path(download.suggested_filename).suffix}"
            await download.save_as(str(destination))
            result["download"] = str(destination)
        except PlaywrightTimeoutError:
            pass
        break
    return result


async def procesar_contribuyente(
    playwright: Playwright, contribuyente: Contribuyente, debug: DebugCapture
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "razon_social": contribuyente.razon_social,
        "cuit_representado": contribuyente.cuit_representado,
        "jurisdiccion": contribuyente.jurisdiccion,
        "txt": str(contribuyente.txt_path),
        "estado": "ERROR",
    }
    result["reportes"], result["controles"] = _related_workbooks(contribuyente)
    browser: Browser | None = None
    context: BrowserContext | None = None
    service_page: Page | None = None
    try:
        if not contribuyente.txt_path.exists():
            raise FileNotFoundError(f"No existe el TXT {contribuyente.txt_path}")
        har_path = debug.har_dir / f"{contribuyente.slug}.har"
        browser, context, login_page = await launch_arca_browser_context(
            playwright,
            headless=HEADLESS,
            context_kwargs={
                "accept_downloads": True,
                "record_har_path": str(har_path),
                "record_har_content": "embed",
            },
        )
        login_page.set_default_timeout(DEFAULT_TIMEOUT)

        async def login() -> None:
            errors: list[str] = []
            login_result = await login_arca(
                login_page,
                contribuyente.cuit_inicio,
                contribuyente.clave,
                error_logs=errors,
                fail_on_captcha=True,
            )
            for message in errors:
                debug.log("arca_login=%s", message)
            if login_result.captcha_detected:
                raise RuntimeError("Captcha detectado")
            if login_result.password_change_required:
                raise RuntimeError("Se requiere cambiar la clave fiscal")
            if not login_result.success:
                raise RuntimeError(errors[-1] if errors else "No se pudo iniciar sesión en ARCA")

        await execute_step(debug, login_page, f"{contribuyente.slug}_01_login", login)

        async def open_service() -> Page:
            errors: list[str] = []
            opened = await open_arca_service(
                login_page,
                service_name="Libros sueldos digital",
                service_matchers=(re.compile(r"LIBRO[S]?\s+DE\s+SUELDOS\s+DIGITAL", re.I),),
                error_logs=errors,
                service_unavailable_message="No se encuentra habilitado el servicio de Libros sueldos digital",
            )
            if opened is None:
                raise RuntimeError(errors[-1] if errors else "No se encuentra habilitado el servicio de Libros sueldos digital")
            opened.set_default_timeout(DEFAULT_TIMEOUT)
            await wait_page_ready(opened)
            return opened

        service_page = await execute_step(debug, login_page, f"{contribuyente.slug}_02_servicio", open_service)
        await debug.capture(service_page, f"{contribuyente.slug}_02_servicio_pagina")
        await execute_step(debug, service_page, f"{contribuyente.slug}_03_periodo_liquidacion", lambda: crear_periodo_y_liquidacion(service_page, contribuyente))
        await execute_step(debug, service_page, f"{contribuyente.slug}_04_cargar_validar", lambda: cargar_y_validar(service_page, contribuyente.txt_path))
        borrador = await execute_step(debug, service_page, f"{contribuyente.slug}_05_descargar_borrador", lambda: descargar_borrador(service_page, debug))
        result["borrador"] = {key: value for key, value in borrador.items() if key != "html"}
        result["scrape_borrador"] = borrador.get("html", "")
        result["estado"] = "OK"
    except Exception as exc:
        result["error"] = str(exc)
        debug.error("contribuyente=%s estado=ERROR error=%s", contribuyente.razon_social, exc)
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
    return result


async def main() -> None:
    contributors = leer_contribuyentes(CLAVES_PATH)
    debug = DebugCapture()
    debug.log("inicio bot=%s periodo=%s contribuyentes=%s", BOT_NAME, PERIODO, len(contributors))
    results = []
    async with async_playwright() as playwright:
        for contributor in contributors:
            debug.log("inicio_contribuyente=%s", contributor.razon_social)
            results.append(await procesar_contribuyente(playwright, contributor, debug))

    report = {
        "bot": BOT_NAME,
        "periodo": PERIODO,
        "inicio": debug.run_dir.name,
        "fuente_claves": str(CLAVES_PATH),
        "resultados": results,
    }
    report_path = debug.descargas_dir / f"resultado_931_202608.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    debug.log("fin report=%s", report_path)


if __name__ == "__main__":
    asyncio.run(main())
