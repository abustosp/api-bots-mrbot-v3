import asyncio
import base64
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Download, async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

async def _download_to_bytes(download: Download) -> bytes:
    path = await download.path()
    if path is None:
        # If path is None, save to a temporary file
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        await download.save_as(tmp_path)
        path = tmp_path
    try:
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

async def bot_sct(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    proxy_request: bool = False,
    # Flags to enable downloads
    download_vencimientos: bool = True,
    download_deudas: bool = True,
    download_ddjj: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:

    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True
    
    proxy = get_proxy_config() if proxy_request else None
    
    # Base directory for downloads
    base_dir_root = Path("descargas/sct")
    stamp_dir = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_rep = re.sub(r"[^0-9]", "", cuit_representado)
    download_dir = base_dir_root / safe_rep / stamp_dir
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            if not login_result.success:
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
                await context.close()
                await browser.close()
                return results, error_logs, False
            if page1 is not page and not page.is_closed():
                await page.close()
            try:
                await page1.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass

            if "auth.afip.gob.ar/contribuyente_/login.xhtml" in (page1.url or ""):
                relogin_result = await login_arca(
                    page1,
                    cuit_representante,
                    clave_representante,
                    error_logs=error_logs,
                )
                if not relogin_result.success:
                    await context.close()
                    await browser.close()
                    return results, error_logs, False

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
                        await context.close()
                        await browser.close()
                        return results, error_logs, False
                    page1 = reopened_page

                try:
                    await page1.wait_for_load_state("networkidle")
                except Exception:
                    pass

            # Select Representado
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
                            break

                    if not selected:
                        error_logs.append(f"No se pudo seleccionar el representado. Verifique el CUIT {cuit_representado}")
            except Exception as e:
                error_logs.append(f"Error al seleccionar representado: {e}")

            # Iframe handling
            frame_loc = None
            try:
                iframe = page1.locator("iframe")
                await iframe.first.wait_for(state="attached", timeout=20000)
                await iframe.first.wait_for(state="visible", timeout=20000)
                frame_loc = page1.frame_locator("iframe")
            except Exception as e:
                error_logs.append(f"No se encontró el iframe de SCT: {e}")
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
                        return False
                    await tab.click()
                    await asyncio.sleep(1)
                    return True
                except Exception:
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
                            continue
                        async with page1.expect_download(timeout=25000) as dlinfo:
                            await opcion.first.click()
                        download = await dlinfo.value
                        filename = f"{prefix}_{download.suggested_filename}"
                        filepath = download_dir / filename
                        await download.save_as(str(filepath))
                        downloaded.append({"seccion": section_name, "formato": fmt.lower(), "path": str(filepath)})
                    except Exception as e:
                        error_logs.append(f"Error descargando {prefix} {fmt}: {e}")
                return downloaded

            # 1. Vencimientos (Default tab)
            if download_vencimientos:
                count_venc = await tab_badge_count("Vencimientos")
                if count_venc != 0:
                    results["archivos"].extend(
                        await download_section(["XLS", "CSV", "PDF"], "vencimientos", "vencimientos")
                    )

            # 2. Deudas
            if download_deudas:
                try:
                    if await click_section_tab("Deudas"):
                        count_deudas = await tab_badge_count("Deudas")
                        if count_deudas is None or count_deudas > 0:
                            results["archivos"].extend(
                                await download_section(["XLS", "CSV", "PDF"], "deudas", "deudas")
                            )
                except Exception as e:
                    error_logs.append(f"Error en Deudas: {e}")

            # 3. DDJJ Pendientes
            if download_ddjj:
                try:
                    if await click_section_tab("DDJJ pendientes"):
                        count_ddjj = await tab_badge_count("DDJJ pendientes")
                        if count_ddjj is None or count_ddjj > 0:
                            results["archivos"].extend(
                                await download_section(["XLS", "CSV", "PDF"], "ddjj_pendientes", "ddjj pendientes")
                            )
                except Exception as e:
                    error_logs.append(f"Error en DDJJ Pendientes: {e}")

            await context.close()
            await browser.close()
            
    except Exception as e:
        error_logs.append(f"Error global en SCT: {e}")
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("SCT_CUIT_LOGIN", "") or os.getenv("SCT_CUIT_REPRESENTANTE", "")
    clave = os.getenv("SCT_CLAVE", "") or os.getenv("SCT_CLAVE_REPRESENTANTE", "")
    cuit_rep = os.getenv("SCT_CUIT_REPRESENTADO", "") or cuit

    if cuit and clave and cuit_rep:
        res = await bot_sct(cuit, clave, cuit_rep)
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
