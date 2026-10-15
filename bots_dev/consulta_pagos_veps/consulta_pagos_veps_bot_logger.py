import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

SETI_HOME_URL = "https://seti.afip.gob.ar/setiweb/#/inicio"
SETI_CONSULTA_VEPS_URL = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
SERVICE_NAME = "PRESENTACIÓN DE DDJJ Y PAGOS"


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


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "consulta_pagos_veps") -> None:
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

        try:
            await page.screenshot(path=str(self.screenshots_dir / f"{prefix}.png"), full_page=True)
            self.logger.info("screenshot=%s", self.screenshots_dir / f"{prefix}.png")
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path = self.html_dir / f"{prefix}.html"
            html_path.write_text(content, encoding="utf-8")
            self.logger.info("html=%s", html_path)
        except Exception:
            self.logger.exception("html_failed step=%s", step)


async def _click_if_visible(page, locator, timeout_ms: int = 3_000) -> bool:
    try:
        if await locator.count() == 0:
            return False
        await locator.first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def _cuit_contribuyente_options(page) -> Any:
    """Opciones del panel del desplegable 'Cuit del Contribuyente.' (lista de representados)."""
    return page.locator("#cuitcontribuyente-multiselect-options [role='option']")


async def _cuit_panel_visible(page) -> bool:
    options = _cuit_contribuyente_options(page)
    try:
        await options.first.wait_for(state="visible", timeout=1_500)
        return True
    except Exception:
        return False


async def _open_cuit_contribuyente(page) -> bool:
    """Abre el panel del desplegable 'Cuit del Contribuyente.' (si ya está abierto no hace nada)."""
    if await _cuit_panel_visible(page):
        return True
    trigger = page.locator("input#cuitcontribuyente.multiselect-search")
    try:
        await trigger.wait_for(state="visible", timeout=10_000)
    except Exception:
        return False
    for _attempt in range(3):
        try:
            await trigger.click(timeout=5_000)
        except Exception:
            return False
        # Espera a que el panel se abra y las opciones estén visibles (se llenan por XHR).
        for _ in range(10):
            await page.wait_for_timeout(400)
            if await _cuit_panel_visible(page):
                return True
    return False


async def _close_modal_if_present(page) -> None:
    """Cierra el modal 'Información importante' de Seti si apareció (intercepta clics)."""
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


async def _read_representados_options(page) -> List[str]:
    """Lee las opciones del panel 'Cuit del Contribuyente.' (lista de representados seleccionables)."""
    representados: List[str] = []
    options = _cuit_contribuyente_options(page)
    option_count = await options.count()
    for idx in range(option_count):
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
    """Selecciona la opción del representado en el desplegable 'Cuit del Contribuyente.'"""
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


async def bot_consulta_pagos_veps_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    periodo: str = "72",
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": [], "representados": []}
    error_logs: list[str] = []
    success = True

    debug = DebugCapture(prefix="consulta_pagos_veps")
    debug.log("Iniciando bot Consulta Pagos VEP Logger")
    debug.log(f"representante={cuit_representante} representado={cuit_representado}")

    proxy = get_proxy_config() if proxy_request else None
    har_path = debug.har_dir / "consulta_pagos_veps.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(record_har_path=har_path, record_har_content="embed")
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
                debug.log(f"Login fallido: {error_logs}")
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
                debug.log(f"Servicio no disponible: {error_logs}")
                await debug.capture(page, "servicio_no_disponible")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await page1.wait_for_load_state("domcontentloaded")
            await debug.capture(page1, "servicio_abierto")

            await page1.goto(SETI_HOME_URL)
            await page1.wait_for_load_state("domcontentloaded")
            await _click_if_visible(page1, page1.get_by_role("button", name="Entendido"))
            # Cierra el modal 'Información importante' si apareció (intercepta clics).
            await _close_modal_if_present(page1)
            await debug.capture(page1, "setiweb_inicio")

            # Navega a la consulta de VEPs y asegura que la SPA renderice (recarga si el shell quedó vacío).
            await page1.goto(SETI_CONSULTA_VEPS_URL)
            await page1.wait_for_load_state("domcontentloaded")
            await _ensure_seti_spa_loaded(page1)
            await debug.capture(page1, "consulta_vep")

            combobox_cuit = page1.get_by_role("combobox", name="Cuit del Contribuyente.")
            for _ in range(4):
                try:
                    await combobox_cuit.wait_for(state="visible", timeout=8_000)
                    break
                except Exception:
                    await _ensure_seti_spa_loaded(page1)
            if await combobox_cuit.count() == 0:
                error_logs.append("No se encontró el selector de CUIT del contribuyente")
                await debug.capture(page1, "selector_cuit_no_encontrado")
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Lista de representados seleccionables (panel del CUIT contribuyente)
            await _open_cuit_contribuyente(page1)
            representados = await _read_representados_options(page1)
            results["representados"] = representados
            debug.log(f"Representados encontrados: {len(representados)}: {representados}")
            rep_path = debug.descargas_dir / "representados.json"
            rep_path.write_text(json.dumps(representados, ensure_ascii=False, indent=2), encoding="utf-8")
            results["archivos"].append(str(rep_path))
            await debug.capture(page1, "representados_obtenidos")

            # Selección del representado (reabre el panel si hace falta y elige por CUIT)
            selected = await _select_cuit_contribuyente(page1, cuit_representado)
            if not selected:
                error_logs.append(f"No se encontró el representado {cuit_representado} en la lista")
                await debug.capture(page1, "representado_no_encontrado")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await debug.capture(page1, "representado_seleccionado")

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
            await debug.capture(page1, "filtros_aplicados")

            # Exportar CSV
            await _clear_overlays(page1)
            download_btn = page1.get_by_role("button", name="download")
            try:
                await download_btn.wait_for(state="visible", timeout=15_000)
            except Exception:
                pass
            if await download_btn.count() == 0:
                error_logs.append("No se encontró el botón de descarga")
                await debug.capture(page1, "boton_descarga_no_encontrado")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await _click_if_visible(page1, download_btn, 5_000)

            exportar_csv = page1.get_by_title("Exportar a CSV")
            if await exportar_csv.count() == 0:
                error_logs.append("No se encontró la opción 'Exportar a CSV'")
                await debug.capture(page1, "exportar_csv_no_encontrado")
                await context.close()
                await browser.close()
                return results, error_logs, False
            async with page1.expect_download(timeout=30_000) as download_info:
                await exportar_csv.click()
            download = await download_info.value
            filename = f"pagos_veps_{re.sub(r'[^0-9]', '', cuit_representado)}.csv"
            target = debug.descargas_dir / filename
            await download.save_as(str(target))
            debug.log(f"Descarga guardada: {target} (sugerido: {download.suggested_filename})")
            results["archivos"].append(str(target))
            await debug.capture(page1, "csv_descargado")

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot consulta pagos vep: {exc}")
        success = False
        debug.log(f"Error: {exc}")

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
        res = await bot_consulta_pagos_veps_logger(
            representante, clave, representado, periodo=periodo, proxy_request=False
        )
        print(res)
    else:
        print("Faltan variables de entorno: CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE, CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO, CONSULTA_PAGOS_VEP_CLAVE")


if __name__ == "__main__":
    asyncio.run(main())