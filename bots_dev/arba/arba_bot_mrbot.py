import os
import tempfile
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from playwright.async_api import Browser, BrowserContext, async_playwright

from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


def _safe_filename_part(value: str, default: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = INVALID_FILENAME_RE.sub("", text)
    text = text.strip()
    return text or default


def _validate_period(periodo: str) -> str:
    digits = re.sub(r"\D", "", periodo or "")
    if len(digits) != 6:
        raise ValueError(f"Periodo invalido: {periodo!r}. Debe ser AAAAMM.")
    month = int(digits[4:6])
    if not 1 <= month <= 12:
        raise ValueError(f"Periodo invalido: mes {month} fuera de rango.")
    now = datetime.now()
    period_date = datetime(year=int(digits[:4]), month=month, day=1)
    if period_date > now.replace(day=1):
        raise ValueError(f"Periodo {periodo} es futuro. No se permiten periodos posteriores a {now.strftime('%Y%m')}.")
    return digits


def _build_download_name(
    cuit: str,
    periodo: str,
    denominacion: str,
) -> str:
    cuit_digits = re.sub(r"\D", "", cuit or "")
    if not cuit_digits:
        raise ValueError("CUIT invalido o vacio.")
    fin_cuit = cuit_digits[-1]
    denominacion_safe = _safe_filename_part(denominacion, "SIN_DENOMINACION")
    return f"{fin_cuit} - {cuit_digits} - RETPER ARBA - {periodo} - {denominacion_safe}.zip"


async def bot_arba(
    cuit: str,
    clave: str,
    periodo: str,
    denominacion: str,

    proxy_request: bool = False,
) -> Tuple[Dict[str, Any], List[str], bool]:
    results: Dict[str, Any] = {"archivos": []}
    error_logs: List[str] = []
    success = True

    cuit_digits = re.sub(r"\D", "", cuit or "")
    if len(cuit_digits) != 11:
        return results, ["CUIT invalido para Ret/Per IIBB ARBA"], False

    if not (clave or "").strip():
        return results, ["La clave es obligatoria"], False

    if not (denominacion or "").strip():
        return results, ["La denominacion es obligatoria"], False

    try:
        period = _validate_period(periodo)
    except ValueError as e:
        return results, [str(e)], False

    anio = period[:4]
    mes = period[4:6]

    proxy = get_proxy_config() if proxy_request else None

    browser: Browser | None = None
    context: BrowserContext | None = None

    with tempfile.TemporaryDirectory(prefix="arba_") as temp_dir:
        temp_path = Path(temp_dir)

        try:
            async with limit_browser(), async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    proxy=proxy,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = await browser.new_context(
                    locale="es-AR",
                    timezone_id="America/Argentina/Buenos_Aires",
                    accept_downloads=True,
                )
                page = await context.new_page()
                page.set_default_timeout(60_000)

                login_url = "https://sso.arba.gov.ar/Login/login?service=https%3A%2F%2Fdfe.arba.gov.ar%3A443%2FDomicilioElectronico%2FpreDescargarRetenciones.do"
                await page.goto(login_url)

                await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").fill(cuit_digits)
                await page.get_by_role("textbox", name="Ingresá tu clave").fill(clave)
                await page.get_by_role("button", name="Ingresar").click()
                await page.wait_for_load_state("networkidle")

                rol_select = page.locator("select[name='rol']")
                if await rol_select.count() > 0:
                    print("Seleccionando rol Contribuyente/Contribuyente de Ingresos Brutos")
                    await rol_select.select_option("Contribuyente/Contribuyente de Ingresos Brutos")
                    await page.get_by_role("button", name="Continuar").click()
                    await page.wait_for_load_state("networkidle")

                if "sso.arba.gov.ar/Login/login" in page.url:
                    error_text = "Credenciales inválidas"
                    try:
                        error_elem = page.locator("font[color='red']")
                        if await error_elem.count() > 0:
                            error_text = (await error_elem.first.text_content()).strip()
                    except Exception:
                        pass
                    error_logs.append(error_text)
                    return results, error_logs, False

                await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
                await page.wait_for_load_state("networkidle")

                await page.locator("input[name=\"anio\"]").fill(anio)
                await page.locator("input[name=\"mes\"]").fill(mes)
                await page.get_by_role("button", name="Consultar").click()
                await page.wait_for_load_state("networkidle")

                await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
                await page.wait_for_load_state("networkidle")

                await page.locator("input[name=\"anio\"]").fill(anio)
                await page.locator("input[name=\"mes\"]").fill(mes)
                await page.get_by_role("button", name="Consultar").click()
                await page.wait_for_load_state("networkidle")
                
                page_content = await page.content()
                if "No Existen archivos para el periodo solicitado" in page_content:
                    error_text = "No Existen archivos para el periodo solicitado"
                    print(f"Sin archivos: {error_text}")
                    error_logs.append(error_text)
                    return results, error_logs, True

                filename = _build_download_name(cuit_digits, period, denominacion)
                file_path = temp_path / filename

                async with page.expect_download() as download_info:
                    await page.get_by_role("button", name="Descargar").click()
                download = await download_info.value

                await download.save_as(str(file_path))

                if file_path.exists():
                    results["archivos"].append(
                        {
                            "tipo": "RETPER ARBA",
                            "archivo": filename,
                        }
                    )
                else:
                    error_logs.append("No se pudo descargar el archivo de Ret/Per IIBB ARBA")

        except Exception as exc:
            error_logs.append(f"Error en bot Ret/Per IIBB ARBA: {exc}")
            success = False
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

        if not results["archivos"]:
            if not error_logs:
                error_logs.append("No se descargaron archivos de Ret/Per IIBB ARBA")
            return results, error_logs, False

    return results, error_logs, success
