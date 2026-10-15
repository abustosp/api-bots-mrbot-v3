import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Download, async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "sct"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")

# --- Debug Capture Class ---

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
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
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

    async def capture(self, page, step: str) -> None:
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

# --- Main Bot Logic ---

async def bot_sct_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    proxy_request: bool = False,
    headless: bool = False,
    # Flags to enable downloads
    download_vencimientos: bool = True,
    download_deudas: bool = True,
    download_ddjj: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:

    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True
    
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot SCT Logger")
    
    proxy = get_proxy_config() if proxy_request else None
    
    # Use debug directory for downloads
    download_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed"
            )
            page = await context.new_page()

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            await debug.capture(page, "post_login")
            
            if not login_result.success:
                debug.log(f"Login failed: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Check if "Ver todos" is needed or if we need to relogin
            try:
                ver_todos_btn = page.get_by_role("link", name=re.compile(r"Ver\s+todos", re.I))
                if await ver_todos_btn.count():
                    await ver_todos_btn.first.wait_for(state="visible", timeout=3000)
            except Exception:
                pass

            page1 = await open_arca_service(
                page,
                service_name="SISTEMA DE CUENTAS",
                service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                service_unavailable_message="No se encuentra habilitado el servicio de SCT",
            )
            
            if page1 is None:
                debug.log("No se pudo abrir servicio SCT")
                await context.close()
                await browser.close()
                return results, error_logs, False
            if page1 is not page and not page.is_closed():
                await page.close()
            try:
                await page1.wait_for_load_state("networkidle")
            except Exception:
                pass

            # Some sessions are redirected back to AFIP auth when opening SCT.
            if "auth.afip.gob.ar/contribuyente_/login.xhtml" in (page1.url or ""):
                debug.log("SCT abrió en login AFIP; reintentando autenticación y apertura del servicio.")
                relogin_result = await login_arca(
                    page1,
                    cuit_representante,
                    clave_representante,
                    error_logs=error_logs,
                )
                await debug.capture(page1, "post_relogin")
                if not relogin_result.success:
                    debug.log(f"Relogin failed: {error_logs}")
                    await context.close()
                    await browser.close()
                    return results, error_logs, False

                # In many accounts the relogin lands directly in SCT.
                if "ctacte.cloud.afip.gob.ar/contribuyente/externo" not in (page1.url or ""):
                    reopened_page = await open_arca_service(
                        page1,
                        service_name="SISTEMA DE CUENTAS",
                        service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                        error_logs=error_logs,
                        ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                        service_unavailable_message="No se encuentra habilitado el servicio de SCT",
                    )
                    if reopened_page is None:
                        debug.log("No se pudo reabrir servicio SCT luego de relogin")
                        await context.close()
                        await browser.close()
                        return results, error_logs, False
                    page1 = reopened_page
                try:
                    await page1.wait_for_load_state("networkidle")
                except Exception:
                    pass

            await debug.capture(page1, "servicio_abierto")

            # Seleccionar representado (con logs como en comprobantes)
            try:
                combobox = page1.locator('select[name="$PropertySelection"]')
                if await combobox.count() > 0:
                    await combobox.wait_for(state="visible", timeout=3000)
                    opciones = combobox.locator("option")
                    count = await opciones.count()
                    selected = False
                    for i in range(count):
                        text = (await opciones.nth(i).text_content() or "").strip()
                        value = await opciones.nth(i).get_attribute("value")
                        if text == cuit_representado or value == cuit_representado:
                            await combobox.select_option(value)
                            selected = True
                            debug.log(f"Representado seleccionado: {value}")
                            break
                    
                    if not selected:
                        debug.log(f"Representado {cuit_representado} no encontrado en opciones. Continuando...")
                else:
                    debug.log("Selector de representado no encontrado. Asumiendo cuenta única o ya seleccionada.")
            except Exception as e:
                debug.log(f"Excepcion al seleccionar representado (continuando): {e}")

            # Iframe handling: the iframe is mandatory, but Exportar may be absent when there are no records.
            frame_loc = None
            try:
                iframe = page1.locator("iframe")
                await iframe.first.wait_for(state="attached", timeout=20000)
                await iframe.first.wait_for(state="visible", timeout=20000)
                frame_loc = page1.frame_locator("iframe")
                await debug.capture(page1, "iframe_load")
            except Exception as e:
                errMsg = f"No se encontró el iframe de SCT: {e}"
                error_logs.append(errMsg)
                debug.log(errMsg)
                await context.close()
                await browser.close()
                return results, error_logs, False

            async def find_export_button(timeout_ms: float = 8000):
                candidates = [
                    frame_loc.get_by_role("button", name=re.compile("Exportar", re.I)),
                    frame_loc.get_by_role("link", name=re.compile("Exportar", re.I)),
                    frame_loc.locator("div.buttons.bt-imp"),
                    frame_loc.locator("button:has-text('Exportar')"),
                    frame_loc.locator("a:has-text('Exportar')"),
                ]
                deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
                while asyncio.get_event_loop().time() < deadline:
                    for candidate in candidates:
                        try:
                            if await candidate.count() == 0:
                                continue
                            if await candidate.first.is_visible(timeout=200):
                                return candidate.first
                        except Exception:
                            continue
                    await asyncio.sleep(0.3)
                return None

            async def _find_tab_locator(tab_name: str):
                regex = re.compile(tab_name, re.I)
                scopes = (frame_loc, page1)
                for scope in scopes:
                    candidates = [
                        scope.get_by_role("tab", name=regex),
                        scope.get_by_role("link", name=regex),
                        scope.locator("a", has_text=regex),
                        scope.locator("button", has_text=regex),
                        scope.locator("li", has_text=regex),
                        scope.get_by_text(regex),
                    ]
                    for candidate in candidates:
                        try:
                            if await candidate.count() == 0:
                                continue
                            element = candidate.first
                            try:
                                await element.wait_for(state="visible", timeout=1200)
                            except Exception:
                                pass
                            if await element.is_visible(timeout=500):
                                return element
                        except Exception:
                            continue
                return None

            async def click_section_tab(tab_name: str) -> bool:
                try:
                    tab = await _find_tab_locator(tab_name)
                    if tab is None:
                        debug.log(f"No se encontró la pestaña '{tab_name}'.")
                        return False
                    await tab.click()
                    await asyncio.sleep(1)
                    return True
                except Exception as e:
                    debug.log(f"No se pudo abrir la pestaña '{tab_name}': {e}")
                    return False

            async def tab_badge_count(tab_name: str) -> Optional[int]:
                try:
                    tab = await _find_tab_locator(tab_name)
                    if tab is None:
                        return None
                    badge = tab.locator("span.badge, span.badge-danger, span.badge-pill, [class*=badge]")
                    if await badge.count() > 0:
                        text = (await badge.first.inner_text() or "").strip()
                        try:
                            return int(text)
                        except ValueError:
                            return None
                    text = (await tab.inner_text() or "").strip()
                    match = re.search(r"\b(\d+)\b", text)
                    return int(match.group(1)) if match else None
                except Exception:
                    return None

            async def download_section(formats: list[str], prefix: str, section_name: str) -> list[dict]:
                downloaded = []
                export_btn = await find_export_button()
                if export_btn is None:
                    debug.log(f"Sin botón Exportar en {section_name}; no hay elementos para descargar.")
                    return downloaded
                for fmt in formats:
                    try:
                        opcion = frame_loc.get_by_role("button", name=re.compile(rf"\b{fmt}\b")).or_(
                            frame_loc.get_by_role("link", name=re.compile(rf"\b{fmt}\b"))
                        )
                        if await opcion.count() == 0:
                            await export_btn.click()
                            await asyncio.sleep(0.4)
                        if await opcion.count() == 0:
                            debug.log(f"Formato {fmt} no disponible en {section_name}.")
                            continue
                        async with page1.expect_download() as dlinfo:
                            await opcion.first.click()
                        download = await dlinfo.value
                        filename = f"{prefix}_{download.suggested_filename}"
                        filepath = download_dir / filename
                        await download.save_as(str(filepath))
                        debug.log(f"Descargado {section_name}/{fmt}: {filename}")
                        downloaded.append({"seccion": section_name, "formato": fmt.lower(), "path": str(filepath)})
                    except Exception as e:
                        errMsg = f"Error descargando {prefix} {fmt}: {e}"
                        error_logs.append(errMsg)
                        debug.log(errMsg)
                return downloaded

            # 1. Vencimientos (Default tab, no badge)
            if download_vencimientos:
                count_venc = await tab_badge_count("Vencimientos")
                if count_venc == 0:
                    debug.log("Vencimientos sin elementos (badge=0), omitiendo descarga.")
                else:
                    if count_venc is None:
                        debug.log("Vencimientos sin contador de elementos; intentando descarga por defecto.")
                    results["archivos"].extend(
                        await download_section(["XLS", "CSV", "PDF"], "vencimientos", "vencimientos")
                    )

            # 2. Deudas
            if download_deudas:
                try:
                    if await click_section_tab("Deudas"):
                        await debug.capture(page1, "tab_deudas")
                        count_deudas = await tab_badge_count("Deudas")
                        if count_deudas is None:
                            debug.log("Deudas: no se encontró contador; intentando descarga.")
                        elif count_deudas == 0:
                            debug.log("Deudas sin elementos (badge=0), omitiendo descarga.")
                        if count_deudas is None or count_deudas > 0:
                            if count_deudas and count_deudas > 0:
                                debug.log(f"Deudas con {count_deudas} elementos, iniciando descarga.")
                            results["archivos"].extend(
                                await download_section(["XLS", "CSV", "PDF"], "deudas", "deudas")
                            )
                except Exception as e:
                    error_logs.append(f"Error en Deudas: {e}")
                    debug.log(f"Error en Deudas: {e}")

            # 3. DDJJ Pendientes
            if download_ddjj:
                try:
                    if await click_section_tab("DDJJ pendientes"):
                        await debug.capture(page1, "tab_ddjj")
                        count_ddjj = await tab_badge_count("DDJJ pendientes")
                        if count_ddjj is None:
                            debug.log("DDJJ pendientes: no se encontró contador; intentando descarga.")
                        elif count_ddjj == 0:
                            debug.log("DDJJ pendientes sin elementos (badge=0), omitiendo descarga.")
                        if count_ddjj is None or count_ddjj > 0:
                            if count_ddjj and count_ddjj > 0:
                                debug.log(f"DDJJ pendientes con {count_ddjj} elementos, iniciando descarga.")
                            results["archivos"].extend(
                                await download_section(["XLS", "CSV", "PDF"], "ddjj_pendientes", "ddjj pendientes")
                            )
                except Exception as e:
                    error_logs.append(f"Error en DDJJ Pendientes: {e}")
                    debug.log(f"Error en DDJJ Pendientes: {e}")

            await context.close()
            await browser.close()
            
    except Exception as e:
        error_logs.append(f"Error global en SCT: {e}")
        debug.log(f"Critical error: {e}")
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("SCT_CUIT_LOGIN", "")
    clave = os.getenv("SCT_CLAVE", "")
    cuit_rep = os.getenv("SCT_CUIT_REPRESENTADO", "")
    headless = os.getenv("SCT_HEADLESS", "False").lower() in ("true", "1", "yes")
    
    if cuit and clave and cuit_rep:
        res = await bot_sct_logger(cuit, clave, cuit_rep, headless=headless)
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
