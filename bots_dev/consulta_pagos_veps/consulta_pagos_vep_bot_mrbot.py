import os
import tempfile
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from playwright.async_api import Page, async_playwright

from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


SETI_CONSULTA_VEPS_URL = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
SERVICE_NAME = "PRESENTACIÓN DE DDJJ Y PAGOS"


async def _click_if_visible(page: Page, locator, timeout_ms: int = 3_000) -> bool:
    try:
        if await locator.count() == 0:
            return False
        await locator.first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _cuit_contribuyente_options(page: Page) -> Any:
    return page.locator("#cuitcontribuyente-multiselect-options [role='option']")


async def _cuit_panel_visible(page: Page) -> bool:
    options = _cuit_contribuyente_options(page)
    try:
        await options.first.wait_for(state="visible", timeout=1_500)
        return True
    except Exception:
        try:
            dropdown = page.locator("#cuitcontribuyente-dropdown")
            classes = await dropdown.get_attribute("class")
            return bool(classes and "is-hidden" not in classes)
        except Exception:
            return False


async def _open_cuit_contribuyente(page: Page) -> bool:
    if await _cuit_panel_visible(page):
        return True

    triggers = [
        page.locator("input#cuitcontribuyente.multiselect-search"),
        page.get_by_role(
            "combobox", name=re.compile(r"Cuit del Contribuyente", re.IGNORECASE)
        ),
        page.locator("#cuitcontribuyente-dropdown").locator("xpath=.."),
    ]
    for trigger in triggers:
        try:
            await trigger.wait_for(state="visible", timeout=8_000)
        except Exception:
            continue
        for _ in range(2):
            with suppress(Exception):
                await trigger.click(timeout=4_000)
            for _ in range(15):
                await page.wait_for_timeout(400)
                if await _cuit_panel_visible(page):
                    return True
    return False


async def _read_representados_options(page: Page) -> List[str]:
    representados: List[str] = []
    options = _cuit_contribuyente_options(page)
    for idx in range(await options.count()):
        text = " ".join(((await options.nth(idx).text_content()) or "").split())
        if text and "TODOS" not in text.upper() and text not in representados:
            representados.append(text)
    return representados


async def _clear_overlays(page: Page) -> None:
    """Elimina overlays residuales que pueden interceptar clicks en la SPA."""
    with suppress(Exception):
        await page.evaluate(
            """() => {
                document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
                document.querySelectorAll('.modal.show').forEach((el) => el.classList.remove('show'));
                const modal = document.getElementById('__EVID__002636__EV__e-modal___btnClose');
                if (modal) modal.click();
            }"""
        )
    await page.wait_for_timeout(300)


async def _select_cuit_contribuyente(page: Page, cuit_representado: str) -> bool:
    normalized_target = re.sub(r"\D", "", cuit_representado or "")
    option_id = f"cuitcontribuyente-multiselect-option-{normalized_target}"

    for _ in range(3):
        await _clear_overlays(page)
        if not await _open_cuit_contribuyente(page):
            continue

        option = page.locator(f"#{option_id}")
        if await option.count() == 0:
            options = _cuit_contribuyente_options(page)
            for idx in range(await options.count()):
                candidate = options.nth(idx)
                candidate_text = re.sub(r"\D", "", await candidate.text_content() or "")
                candidate_value = re.sub(r"\D", "", await candidate.get_attribute("value") or "")
                if normalized_target and normalized_target in {candidate_text, candidate_value}:
                    option = candidate
                    break

        if await option.count() == 0:
            continue

        try:
            element_id = await option.get_attribute("id")
            if element_id:
                clicked = await page.evaluate(
                    """(id) => {
                        const el = document.getElementById(id);
                        if (!el) return false;
                        ['mousedown', 'mouseup', 'click'].forEach(type =>
                            el.dispatchEvent(new MouseEvent(type, { bubbles: true }))
                        );
                        return true;
                    }""",
                    element_id,
                )
            else:
                await option.click(force=True, timeout=4_000)
                clicked = True

            if clicked:
                await page.wait_for_timeout(600)
                fake_inputs = page.locator(".multiselect-fake-input")
                for idx in range(await fake_inputs.count()):
                    value = await fake_inputs.nth(idx).get_attribute("value")
                    if normalized_target and normalized_target in re.sub(r"\D", "", value or ""):
                        return True
                if await option.get_attribute("aria-selected") == "true":
                    return True
                classes = await option.get_attribute("class") or ""
                if "selected" in classes.lower():
                    return True
        except Exception:
            continue
    return False


async def _close_modal_if_present(page: Page) -> None:
    await page.wait_for_timeout(1_200)
    close_buttons = page.locator(
        "button[aria-label='Close'], .modal.show .btn-close, .modal .btn-close, .e-modal__close"
    )
    for _ in range(4):
        try:
            if await close_buttons.count() == 0:
                return
            await close_buttons.first.click(timeout=2_000)
        except Exception:
            pass
        await page.wait_for_timeout(400)


async def _ensure_seti_spa_loaded(page: Page) -> None:
    for _ in range(3):
        await page.wait_for_timeout(1_500)
        try:
            has_cuit = (
                await page.locator("input#cuitcontribuyente.multiselect-search").count() > 0
                or "Cuit del Contribuyente" in await page.content()
            )
        except Exception:
            has_cuit = False
        if await page.locator("#app").count() > 0 and has_cuit:
            return
        with suppress(Exception):
            await page.reload(wait_until="domcontentloaded", timeout=20_000)


async def _select_periodo(page: Page, periodo: str) -> None:
    requested = str(periodo or "72")
    combobox = page.get_by_role(
        "combobox", name=re.compile(rf"^{re.escape(requested.replace('72', '12'))}$", re.IGNORECASE)
    )
    if await combobox.count() == 0:
        combobox = page.get_by_role("combobox", name=re.compile(r"^\d+$")).last
    if await combobox.count() == 0:
        return

    with suppress(Exception):
        await combobox.click(timeout=5_000)
    option = page.get_by_role("option", name=re.compile(rf"^{re.escape(requested)}$", re.IGNORECASE))
    if await option.count() > 0:
        with suppress(Exception):
            await option.first.click(timeout=4_000)


async def _exportar_csv(page: Page, target: Path) -> None:
    download_button = None
    candidates = [
        page.get_by_role("button", name=re.compile(r"download|descargar", re.IGNORECASE)),
        page.locator(
            "button[title*='descarg' i], button[aria-label*='descarg' i], [role='button'][title*='download' i]"
        ),
    ]
    for candidate in candidates:
        try:
            await candidate.first.wait_for(state="visible", timeout=15_000)
            download_button = candidate
            break
        except Exception:
            continue
    if download_button is None:
        raise RuntimeError("No se encontró el botón de descarga")

    if not await _click_if_visible(page, download_button, 5_000):
        raise RuntimeError("No se pudo abrir el menú de descarga")

    exportar_csv = page.get_by_title("Exportar a CSV")
    if await exportar_csv.count() == 0:
        exportar_csv = page.locator("[title*='Exportar a CSV' i]")
    if await exportar_csv.count() == 0:
        raise RuntimeError("No se encontró la opción 'Exportar a CSV'")

    async with page.expect_download(timeout=30_000) as download_info:
        try:
            await exportar_csv.first.click(timeout=5_000)
        except Exception:
            await exportar_csv.first.click(timeout=5_000, force=True)
    download = await download_info.value
    await download.save_as(str(target))


async def bot_consulta_pagos_veps(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    periodo: str = "72",
    proxy_request: bool = False,

) -> tuple[dict[str, Any], list[str], bool]:
    """Consulta pagos VEP y exporta el CSV."""
    results: dict[str, Any] = {
        "archivos": [],
        "representados": [],
        "archivo": None,
        "archivo_path": None,

    }
    error_logs: list[str] = []
    browser = None
    context = None
    temp_dir = tempfile.TemporaryDirectory(prefix='consulta_pagos_vep_')
    download_dir = Path(temp_dir.name)
    download_path: Optional[Path] = None

    try:
        proxy = get_proxy_config() if proxy_request else None
        async with limit_browser(), async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            if not login_result.success:
                return results, error_logs, False

            service_page = await open_arca_service(
                page,
                service_name=SERVICE_NAME,
                service_matchers=(re.compile(r"PRESENTACI[OÓ]N DE DDJJ Y PAGOS", re.IGNORECASE),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message=(
                    "No se encuentra habilitado el servicio de PRESENTACION DE DDJJ Y PAGOS"
                ),
            )
            if service_page is None:
                return results, error_logs, False
            if service_page is not page:
                with suppress(Exception):
                    await page.close()

            await service_page.goto(SETI_CONSULTA_VEPS_URL, wait_until="domcontentloaded")
            await _close_modal_if_present(service_page)
            await _ensure_seti_spa_loaded(service_page)

            combobox_cuit = service_page.get_by_role(
                "combobox", name=re.compile(r"Cuit del Contribuyente", re.IGNORECASE)
            )
            for _ in range(4):
                try:
                    await combobox_cuit.wait_for(state="visible", timeout=8_000)
                    break
                except Exception:
                    await _close_modal_if_present(service_page)
                    await _ensure_seti_spa_loaded(service_page)
            if await combobox_cuit.count() == 0:
                error_logs.append("No se encontró el selector de CUIT del contribuyente")
                return results, error_logs, False

            await _open_cuit_contribuyente(service_page)
            representados = await _read_representados_options(service_page)
            results["representados"] = representados

            if not await _select_cuit_contribuyente(service_page, cuit_representado):
                error_logs.append(f"No se encontró el representado {cuit_representado} en la lista")
                return results, error_logs, False

            await _clear_overlays(service_page)
            await _select_periodo(service_page, periodo)
            aplicar = service_page.get_by_role("button", name=re.compile(r"^Aplicar$", re.IGNORECASE))
            if await aplicar.count() > 0:
                with suppress(Exception):
                    await aplicar.click(timeout=5_000)
                with suppress(Exception):
                    await service_page.wait_for_load_state("domcontentloaded", timeout=10_000)

            cuit_digits = re.sub(r"\D", "", cuit_representado or "")
            filename = (
                f"pagos_veps_{cuit_digits}_"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            download_path = download_dir / filename
            await _exportar_csv(service_page, download_path)
            results["archivo"] = filename
            results["archivo_path"] = str(download_path.resolve())
            results["archivos"].append(str(download_path))

    except Exception as exc:
        error_logs.append(f"Error en bot consulta pagos VEP: {exc}")
    finally:
        if context is not None:
            with suppress(Exception):
                await context.close()
        if browser is not None:
            with suppress(Exception):
                await browser.close()
        with suppress(Exception):
            temp_dir.cleanup()

    success = download_path is not None and download_path.name == results.get("archivo")
    return results, error_logs, success


async def main() -> None:
    cuit_representante = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE", "")
    clave_representante = os.getenv("CONSULTA_PAGOS_VEP_CLAVE", "")
    cuit_representado = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO", "")
    if not all((cuit_representante, clave_representante, cuit_representado)):
        print(
            "Faltan variables de entorno: CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE, "
            "CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO, CONSULTA_PAGOS_VEP_CLAVE"
        )
        return

    result = await bot_consulta_pagos_veps(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        periodo=os.getenv("CONSULTA_PAGOS_VEP_PERIODO", "72"),
        proxy_request=os.getenv("CONSULTA_PAGOS_VEP_PROXY", "false").lower() == "true",
    )
    print(result)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
