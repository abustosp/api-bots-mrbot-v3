import os
import tempfile
import re
from datetime import datetime
from pathlib import Path

from typing import Dict, List, Optional, Tuple

from playwright.async_api import Page, async_playwright

from shared.arca_login import open_arca_service, start_arca_session
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

SERVICE_NAME = "LUFE"
SERVICE_PATTERN = re.compile(r"LUFE", re.I)


async def _try_click_expand_combobox(page: Page) -> None:
    for label in ("expand combobox", "Abrir"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _select_representado(
    page: Page,
    cuit_representado: str,
) -> Tuple[bool, List[str]]:
    opciones_encontradas: List[str] = []
    try:
        combobox = page.get_by_role("combobox", name="Ingrese al menos 3 caracteres")
        await combobox.wait_for(state="visible", timeout=10000)
        await combobox.click()

        await _try_click_expand_combobox(page)
        await page.wait_for_timeout(1500)

        options = page.locator('[role="option"], li[role="option"], .MuiAutocomplete-option')
        option_found: bool = False
        for _ in range(5):
            count = await options.count()
            if count > 0:
                for i in range(count):
                    text = await options.nth(i).inner_text()
                    opciones_encontradas.append(text)
                    if not option_found and cuit_representado in text:
                        await options.nth(i).click()
                        option_found = True
                if not option_found:
                    await options.first.click()
                    option_found = True
                return True, opciones_encontradas
            await page.wait_for_timeout(500)

        await combobox.fill(cuit_representado)
        await page.wait_for_timeout(1500)

        for _ in range(5):
            count = await options.count()
            if count > 0:
                for i in range(count):
                    text = await options.nth(i).inner_text()
                    opciones_encontradas.append(text)
                    if not option_found and cuit_representado in text:
                        await options.nth(i).click()
                        option_found = True
                if not option_found:
                    await options.first.click()
                    option_found = True
                return True, opciones_encontradas
            await page.wait_for_timeout(500)

        return False, opciones_encontradas
    except Exception:
        return False, opciones_encontradas


async def descargar_certificado_mipyme(
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    headless: bool = True,
    eliminar_descargas: bool = True,
    proxy_request: bool = False,
) -> Tuple[List[Dict], List[str], bool]:
    error_logs: List[str] = []
    results: List[Dict] = []

    if not all([cuit_representante, clave, cuit_representado]):
        error_logs.append("Faltan parametros requeridos: CUIT representante, clave y CUIT representado.")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None
    temp_dir = tempfile.TemporaryDirectory(prefix='certificado_mipyme_') if eliminar_descargas else None
    download_root = (
        Path(temp_dir.name)
        if temp_dir is not None
        else Path(os.getcwd()) / "descargas" / "certificado_mipyme" / cuit_representado
    )
    download_root.mkdir(parents=True, exist_ok=True)

    browser = None
    context = None

    try:
        async with limit_browser(), async_playwright() as pw:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]

            context_kwargs = {
                "locale": "es-AR",
                "timezone_id": "America/Argentina/Buenos_Aires",
                "accept_downloads": True,
                "extra_http_headers": {"Accept-Language": "es-AR,es;q=0.9"},
            }

            arca_session = await start_arca_session(
                pw,
                cuit_representante=cuit_representante,
                clave_representante=clave,
                error_logs=error_logs,
                headless=headless,
                proxy=proxy,
                launch_args=launch_args,
                context_kwargs=context_kwargs,
            )
            if arca_session is None:
                return results, error_logs, False

            browser = arca_session.browser
            context = arca_session.context
            page = arca_session.page

            service_page = await open_arca_service(
                page,
                service_name=SERVICE_NAME,
                service_matchers=(SERVICE_PATTERN,),
                error_logs=error_logs,
                service_unavailable_message=f"No se encuentra habilitado el servicio de {SERVICE_NAME}",
            )
            if service_page is None:
                return results, error_logs, False

            if service_page is not page and not page.is_closed():
                await page.close()

            representado_ok, opciones_encontradas = await _select_representado(service_page, cuit_representado)
            if not representado_ok:
                error_logs.append("No se pudo seleccionar el representado en el combo")
                return results, error_logs, False

            try:
                seleccionar_btn = service_page.get_by_role("button", name="Seleccionar")
                await seleccionar_btn.wait_for(state="visible", timeout=5000)
                await seleccionar_btn.click()
            except Exception:
                pass

            await service_page.wait_for_load_state("networkidle")
            await service_page.wait_for_timeout(2000)

            page_content = await service_page.content()
            if "No se encuentra habilitado" in page_content:
                error_logs.append("No se encuentra habilitado el servicio de Certificado MiPyME")
                return results, error_logs, False

            download_link = service_page.get_by_role("link", name=re.compile(r"Descargar Certificado MiPyME", re.I))
            await download_link.wait_for(state="visible", timeout=15000)

            async with service_page.expect_download(timeout=30000) as download_info:
                await download_link.click()
            download = await download_info.value

            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"sepyme_{cuit_representado}_{timestamp}.pdf"
            local_path = download_root / filename
            await download.save_as(local_path)

            result_item = {
                "archivo": filename,
                "local_path": str(local_path),
                "opciones_encontradas": opciones_encontradas,
            }


            results.append(result_item)

    except Exception as exc:
        error_logs.append(f"Error en bot Certificado MiPyME: {exc}")
        return results, error_logs, False
    finally:
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
        if temp_dir is not None:
            temp_dir.cleanup()

    return results, error_logs, True if results else False
