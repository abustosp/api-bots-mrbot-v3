import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

SETI_HOME_URL = "https://seti.afip.gob.ar/setiweb/#/inicio"
SETI_CONSULTA_VEPS_URL = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
SERVICE_NAME = "PRESENTACIÓN DE DDJJ Y PAGOS"

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _click_if_visible(page, locator, timeout_ms: int = 3_000) -> bool:
    try:
        if await locator.count() == 0:
            return False
        await locator.first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _cuit_contribuyente_options(page) -> Any:
    return page.locator("#cuitcontribuyente-multiselect-options [role='option']")


async def _cuit_panel_visible(page) -> bool:
    options = _cuit_contribuyente_options(page)
    try:
        await options.first.wait_for(state="visible", timeout=1_500)
        return True
    except Exception:
        # Fallback: el panel podría estar abierto sin que wait_for('visible') lo detecte
        # (timing del componente). Si hay opciones en el DOM y el panel no tiene is-hidden, cuenta.
        try:
            dropdown = page.locator("#cuitcontribuyente-dropdown")
            classes = await dropdown.get_attribute("class")
            if classes and "is-hidden" not in classes:
                return True
        except Exception:
            pass
        return False


async def _open_cuit_contribuyente(page) -> bool:
    if await _cuit_panel_visible(page):
        return True
    triggers = [
        page.locator("input#cuitcontribuyente.multiselect-search"),
        page.get_by_role("combobox", name="Cuit del Contribuyente."),
        page.locator("#cuitcontribuyente-dropdown").locator("xpath=.."),
    ]
    for trigger in triggers:
        try:
            await trigger.wait_for(state="visible", timeout=8_000)
        except Exception:
            continue
        for _attempt in range(2):
            try:
                await trigger.click(timeout=4_000)
            except Exception:
                pass
            # Espera a que el panel se abra.
            for _ in range(15):
                await page.wait_for_timeout(400)
                if await _cuit_panel_visible(page):
                    return True
    return False


async def _read_representados_options(page) -> List[str]:
    representados: List[str] = []
    options = _cuit_contribuyente_options(page)
    for idx in range(await options.count()):
        text = " ".join(((await options.nth(idx).text_content()) or "").split())
        if text and "TODOS" not in text.upper() and text not in representados:
            representados.append(text)
    return representados


async def _clear_overlays(page) -> None:
    """Elimina backdrops/modales residuales que interceptan clics, via JS."""
    try:
        await page.evaluate("""() => {
            document.querySelectorAll('.modal-backdrop').forEach((el) => el.remove());
            document.querySelectorAll('.modal.show').forEach((el) => el.classList.remove('show'));
            const modal = document.getElementById('__EVID__002636__EV__e-modal___btnClose');
            if (modal) { modal.click(); }
        }""")
    except Exception:
        pass
    await page.wait_for_timeout(300)


async def _select_cuit_contribuyente(page, cuit_representado: str) -> bool:
    normalized_target = re.sub(r"\D", "", cuit_representado)
    option_id = f"cuitcontribuyente-multiselect-option-{normalized_target}"
    option = page.locator(f"#{option_id}")
    for _round in range(3):
        await _clear_overlays(page)
        if await option.count() == 0:
            if not await _open_cuit_contribuyente(page):
                continue
            await page.wait_for_timeout(500)
            continue
        try:
            # Click vía JS dispatch: funciona aunque el panel esté oculto/interceptado.
            clicked = await page.evaluate(
                """(id) => {
                    const el = document.getElementById(id);
                    if (!el) return false;
                    el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    return true;
                }""",
                option_id,
            )
            if clicked:
                await page.wait_for_timeout(600)
                # Verifica que el valor seleccionado cambió (el fake-input refleja la selección).
                try:
                    fake = await page.locator(".multiselect-fake-input").nth(1).get_attribute("value")
                except Exception:
                    fake = None
                if fake and re.sub(r"\D", "", fake) == normalized_target:
                    return True
            await page.wait_for_timeout(500)
        except Exception:
            continue
    return False


async def _close_modal_if_present(page) -> None:
    # El modal de 'Información importante' puede tardar en aparecer (timing asíncrono).
    await page.wait_for_timeout(1_200)
    close_buttons = page.locator(
        "button[aria-label='Close'], .modal.show .btn-close, .modal .btn-close, .e-modal__close"
    )
    for _ in range(4):
        try:
            if await close_buttons.count() == 0:
                break
            await close_buttons.first.click(timeout=2_000)
        except Exception:
            pass
        await page.wait_for_timeout(400)


async def _ensure_seti_spa_loaded(page) -> None:
    """Asegura que la SPA de Seti esté renderizada (si el shell quedó vacío, recarga)."""
    for _ in range(3):
        await page.wait_for_timeout(1_500)
        try:
            shell = await page.locator("#app").count()
            has_cuit = (
                await page.locator("input#cuitcontribuyente.multiselect-search").count() > 0
                or "Cuit del Contribuyente" in await page.content()
            )
        except Exception:
            shell, has_cuit = 0, False
        if shell > 0 and has_cuit:
            return
        try:
            await page.reload(wait_until="domcontentloaded", timeout=20_000)
        except Exception:
            pass


async def bot_consulta_pagos_veps(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    periodo: str = "72",
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": [], "representados": []}
    error_logs: list[str] = []
    success = True

    proxy = get_proxy_config() if proxy_request else None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context()
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

            page1 = await open_arca_service(
                page,
                service_name=SERVICE_NAME,
                service_matchers=(re.compile(r"PRESENTACI[OÓ]N DE DDJJ Y PAGOS", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se encuentra habilitado el servicio de PRESENTACIÓN DE DDJJ Y PAGOS",
            )
            if page1 is None:
                await context.close()
                await browser.close()
                return results, error_logs, False
            await page1.wait_for_load_state("domcontentloaded")

            # Navega directo a la consulta de VEPs y asegura que la SPA renderice.
            await page1.goto(SETI_CONSULTA_VEPS_URL)
            await page1.wait_for_load_state("domcontentloaded")
            await _close_modal_if_present(page1)
            await _ensure_seti_spa_loaded(page1)

            # Espera el selector con reintentos (el modal puede bloquear la primera vez).
            combobox_cuit = page1.get_by_role("combobox", name="Cuit del Contribuyente.")
            for _ in range(4):
                try:
                    await combobox_cuit.wait_for(state="visible", timeout=8_000)
                    break
                except Exception:
                    await _close_modal_if_present(page1)
                    await _ensure_seti_spa_loaded(page1)
            if await combobox_cuit.count() == 0:
                error_logs.append("No se encontró el selector de CUIT del contribuyente")
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Lista de representados seleccionables
            await _open_cuit_contribuyente(page1)
            representados = await _read_representados_options(page1)
            results["representados"] = representados
            rep_path = DOWNLOAD_DIR / "representados.json"
            rep_path.write_text(json.dumps(representados, ensure_ascii=False, indent=2), encoding="utf-8")
            results["archivos"].append(str(rep_path))

            # Selección del representado
            selected = await _select_cuit_contribuyente(page1, cuit_representado)
            if not selected:
                error_logs.append(f"No se encontró el representado {cuit_representado} en la lista")
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Filtro de período o frecuencia (selección robusta por JS dispatch)
            await _clear_overlays(page1)
            combobox_periodo = page1.get_by_role(
                "combobox",
                name=re.compile(rf"^{re.escape(periodo.replace('72', '12'))}$", re.I),
            )
            if await combobox_periodo.count() == 0:
                combobox_periodo = page1.get_by_role("combobox", name=re.compile(r"^\d+$", re.I)).first
            if await combobox_periodo.count() > 0:
                try:
                    await combobox_periodo.click(timeout=5_000)
                except Exception:
                    await combobox_periodo.click(timeout=5_000, force=True)
                await page1.wait_for_timeout(400)
                option_periodo = page1.get_by_role("option", name=re.compile(rf"^{re.escape(periodo)}$", re.I))
                if await option_periodo.count() > 0:
                    try:
                        await option_periodo.first.click(timeout=4_000)
                    except Exception:
                        await option_periodo.first.click(timeout=4_000, force=True)
            await page1.wait_for_timeout(300)
            aplicar = page1.get_by_role("button", name="Aplicar")
            if await aplicar.count() > 0:
                try:
                    await aplicar.click(timeout=5_000)
                except Exception:
                    await aplicar.click(timeout=5_000, force=True)
                await page1.wait_for_load_state("domcontentloaded")

            # Exportar CSV
            await _clear_overlays(page1)
            download_btn = page1.get_by_role("button", name="download")
            try:
                await download_btn.wait_for(state="visible", timeout=15_000)
            except Exception:
                pass
            if await download_btn.count() == 0:
                error_logs.append("No se encontró el botón de descarga")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await _click_if_visible(page1, download_btn, 5_000)

            exportar_csv = page1.get_by_title("Exportar a CSV")
            if await exportar_csv.count() == 0:
                error_logs.append("No se encontró la opción 'Exportar a CSV'")
                await context.close()
                await browser.close()
                return results, error_logs, False
            async with page1.expect_download(timeout=30_000) as download_info:
                await exportar_csv.click()
            download = await download_info.value
            filename = f"pagos_veps_{re.sub(r'[^0-9]', '', cuit_representado)}.csv"
            target = DOWNLOAD_DIR / filename
            await download.save_as(str(target))
            results["archivos"].append(str(target))

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot consulta pagos vep: {exc}")
        success = False

    if not results["archivos"]:
        success = False

    return results, error_logs, success


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    representante = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE", "")
    representado = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO", "")
    clave = os.getenv("CONSULTA_PAGOS_VEP_CLAVE", "")
    periodo = os.getenv("CONSULTA_PAGOS_VEP_PERIODO", "72")

    if representante and representado and clave:
        res = await bot_consulta_pagos_veps(
            representante, clave, representado, periodo=periodo, proxy_request=False
        )
        print(res)
    else:
        print("Faltan variables de entorno: CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE, CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO, CONSULTA_PAGOS_VEP_CLAVE")


if __name__ == "__main__":
    asyncio.run(main())