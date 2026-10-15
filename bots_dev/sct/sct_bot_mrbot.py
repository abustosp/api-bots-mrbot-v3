import asyncio
import os
import re
from playwright.async_api import async_playwright, Download
from datetime import datetime
import base64
from typing import Optional
from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


async def _download_to_bytes(download: Download) -> bytes:
    path = await download.path()
    if path is None:
        with open(str(DOWNLOAD_DIR / "tmp.bin"), "wb") as tmp:
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
    vencimientos_excel_b64: bool = False,
    vencimientos_csv_b64: bool = False,
    vencimientos_pdf_b64: bool = False,


    deudas_excel_b64: bool = False,
    deudas_csv_b64: bool = False,
    deudas_pdf_b64: bool = False,


    ddjj_pendientes_excel_b64: bool = False,
    ddjj_pendientes_csv_b64: bool = False,
    ddjj_pendientes_pdf_b64: bool = False,


) -> tuple[dict[str, str], list[str], bool]:

    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True

    proxy = get_proxy_config() if proxy_request == True else None

    request_salidas = [
        vencimientos_excel_b64, vencimientos_csv_b64, vencimientos_pdf_b64,

        deudas_excel_b64, deudas_csv_b64, deudas_pdf_b64,

        ddjj_pendientes_excel_b64, ddjj_pendientes_csv_b64, ddjj_pendientes_pdf_b64,

    ]
    if not any(request_salidas):
        error_logs.append("No se ha seleccionado ningún tipo de salida para descargar.")
        return results, error_logs, False

    try:
        async with limit_browser(), async_playwright() as pw:
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
                try:
                    await context.close()
                    await browser.close()
                except Exception:
                    pass
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
                    try:
                        await context.close()
                        await browser.close()
                    except Exception:
                        pass
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
                        try:
                            await context.close()
                            await browser.close()
                        except Exception:
                            pass
                        return results, error_logs, False
                    page1 = reopened_page

                try:
                    await page1.wait_for_load_state("networkidle")
                except Exception:
                    pass

            # Seleccionar representado
            try:
                target_cuit = re.sub(r"\D", "", str(cuit_representado or ""))
                combobox = page1.locator('select[name="$PropertySelection"]')
                if await combobox.count() > 0:
                    await combobox.wait_for(state="visible", timeout=5000)

                    opciones = combobox.locator("option")
                    count = await opciones.count()
                    selected = False
                    for i in range(count):
                        option = opciones.nth(i)
                        text = (await option.text_content() or "").strip()
                        value = (await option.get_attribute("value") or "").strip()
                        text_digits = re.sub(r"\D", "", text)
                        value_digits = re.sub(r"\D", "", value)

                        if (
                            text == cuit_representado
                            or value == cuit_representado
                            or (target_cuit and target_cuit in {text_digits, value_digits})
                        ):
                            if value:
                                await combobox.select_option(value=value)
                            else:
                                await combobox.select_option(label=text)
                            selected = True
                            break

                    if not selected:
                        error_logs.append(f"No se pudo seleccionar el representado. Verifique el CUIT {cuit_representado}")
            except Exception as e:
                error_logs.append(f"Error al seleccionar representado: {e}")

            # Iframe de SCT
            frame_loc = None
            try:
                iframe = page1.locator("iframe")
                await iframe.first.wait_for(state="attached", timeout=20000)
                await iframe.first.wait_for(state="visible", timeout=20000)
                frame_loc = page1.frame_locator("iframe")
            except Exception as e:
                error_logs.append(f"No se encontró el iframe de SCT: {e}")
                try:
                    await page1.close()
                except Exception:
                    pass
                await context.close()
                await browser.close()
                return results, error_logs, False

            async def find_export_button():
                candidates = [
                    frame_loc.get_by_role("button", name=re.compile("Exportar", re.I)),
                    frame_loc.get_by_role("link", name=re.compile("Exportar", re.I)),
                    frame_loc.locator("div.buttons.bt-imp"),
                    frame_loc.locator("button:has-text('Exportar')"),
                    frame_loc.locator("a:has-text('Exportar')"),
                ]
                deadline = asyncio.get_event_loop().time() + 8
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
                candidates = []
                for scope in scopes:
                    candidates.extend([
                        scope.get_by_role("tab", name=regex),
                        scope.get_by_role("link", name=regex),
                        scope.locator("a", has_text=regex),
                        scope.locator("button", has_text=regex),
                        scope.locator("li", has_text=regex),
                        scope.get_by_text(regex),
                    ])
                deadline = asyncio.get_event_loop().time() + 8
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

            async def click_section_tab(tab_name: str) -> bool:
                try:
                    tab = await _find_tab_locator(tab_name)
                    if tab is None:
                        return False
                    await tab.click()
                    await asyncio.sleep(2)
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
            async def download_section(formats: list[str], section_label: str) -> dict[str, bytes]:
                downloaded: dict[str, bytes] = {}
                export_btn = await find_export_button()
                if export_btn is None:
                    return downloaded
                for fmt in formats:
                    try:
                        opcion = frame_loc.get_by_role("button", name=re.compile(rf"\b{fmt}\b", re.I)).or_(
                            frame_loc.get_by_role("link", name=re.compile(rf"\b{fmt}\b", re.I))
                        )
                        if await opcion.count() == 0:
                            await export_btn.click()
                            await asyncio.sleep(0.4)
                        if await opcion.count() == 0:
                            continue
                        async with page1.expect_download(timeout=25000) as dlinfo:
                            await opcion.first.click()
                        download = await dlinfo.value
                        downloaded[fmt.upper()] = await _download_to_bytes(download)
                    except Exception as e:
                        error_logs.append(f"Error descargando {section_label} {fmt}: {e}")
                return downloaded

            # Sección de Vencimientos (tab por defecto)
            try:
                vencimientos_requested = any([
                    vencimientos_excel_b64, vencimientos_csv_b64, vencimientos_pdf_b64,

                ])
                if vencimientos_requested:
                    count_vencimientos = await tab_badge_count("Vencimientos")
                    if count_vencimientos != 0:
                        section_data = await download_section(["XLS", "CSV", "PDF"], "vencimientos")
                        xls_data = section_data.get("XLS")
                        if xls_data:
                            if vencimientos_excel_b64:
                                results["excel"] = base64.b64encode(xls_data).decode("utf-8")


                        csv_data = section_data.get("CSV")
                        if csv_data:
                            if vencimientos_csv_b64:
                                results["csv"] = base64.b64encode(csv_data).decode("utf-8")


                        pdf_data = section_data.get("PDF")
                        if pdf_data:
                            if vencimientos_pdf_b64:
                                results["pdf"] = base64.b64encode(pdf_data).decode("utf-8")


            except Exception as e:
                error_logs.append(str(e))

            # Sección de Deudas
            try:
                deudas_requested = any([
                    deudas_excel_b64, deudas_csv_b64, deudas_pdf_b64,

                ])
                if deudas_requested:
                    if not await click_section_tab("Deudas"):
                        raise RuntimeError("No se encontró la pestaña Deudas")

                    count_deudas = await tab_badge_count("Deudas")
                    if count_deudas is None or count_deudas > 0:
                        section_data = await download_section(["XLS", "CSV", "PDF"], "deudas")
                        xls_data = section_data.get("XLS")
                        if xls_data:
                            if deudas_excel_b64:
                                results["deudas_excel"] = base64.b64encode(xls_data).decode("utf-8")


                        csv_data = section_data.get("CSV")
                        if csv_data:
                            if deudas_csv_b64:
                                results["deudas_csv"] = base64.b64encode(csv_data).decode("utf-8")


                        pdf_data = section_data.get("PDF")
                        if pdf_data:
                            if deudas_pdf_b64:
                                results["deudas_pdf"] = base64.b64encode(pdf_data).decode("utf-8")


            except Exception as e:
                error_logs.append(f"Error en sección de Deudas: {str(e)}")

            # Sección de DDJJ pendientes
            try:
                ddjj_requested = any([
                    ddjj_pendientes_excel_b64, ddjj_pendientes_csv_b64, ddjj_pendientes_pdf_b64,

                ])
                if ddjj_requested:
                    if not await click_section_tab("DDJJ pendientes"):
                        raise RuntimeError("No se encontró la pestaña DDJJ pendientes")

                    count_ddjj = await tab_badge_count("DDJJ pendientes")
                    if count_ddjj is None or count_ddjj > 0:
                        section_data = await download_section(["XLS", "CSV", "PDF"], "ddjj pendientes")
                        xls_data = section_data.get("XLS")
                        if xls_data:
                            if ddjj_pendientes_excel_b64:
                                results["ddjj_pendientes_excel"] = base64.b64encode(xls_data).decode("utf-8")


                        csv_data = section_data.get("CSV")
                        if csv_data:
                            if ddjj_pendientes_csv_b64:
                                results["ddjj_pendientes_csv"] = base64.b64encode(csv_data).decode("utf-8")


                        pdf_data = section_data.get("PDF")
                        if pdf_data:
                            if ddjj_pendientes_pdf_b64:
                                results["ddjj_pendientes_pdf"] = base64.b64encode(pdf_data).decode("utf-8")


            except Exception as e:
                error_logs.append(f"Error en sección de DDJJ pendientes: {str(e)}")

            # Cierre ordenado
            try:
                await page1.close()
            except Exception:
                pass
            await context.close()
            await browser.close()

    except Exception as e:
        error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {str(e)}")
        success = False

    return results, error_logs, success
